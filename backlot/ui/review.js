import {getJSON, el, mediaURL} from '/ui/lib.js';
const $ = id => document.getElementById(id);
const projectId = decodeURIComponent(location.pathname.split('/')[2]);
const api = `/api/project/${encodeURIComponent(projectId)}`;
const labels = {unreviewed:'未标注',approved:'通过',rejected:'需重做',trimmable:'可裁切'};
const categories = {face:'面貌 / 身份',action:'动作 / 物理',costume:'服装',lip_sync:'口型',framing:'构图',camera:'运镜',lighting:'光线',environment:'环境',objects:'手 / 道具',continuity:'时序 / 连贯',other:'其他'};
const severityLabels = {minor:'轻微',major:'明显',blocking:'严重'};
let items=[], current=null, selectedIssue=-1, loadingEditor=false;
const video=$('video');
const tabId = sessionStorage.getItem('review-tab') || crypto.randomUUID();
sessionStorage.setItem('review-tab', tabId);
const key=item=>`openmontage:review:${projectId}:${item.media.path}:${tabId}`;
const payload=item=>({scene_id:item.card.id,media_path:item.media.path,revision:item.revision,status:item.status,issues:structuredClone(item.issues)});
const time=t=>t==null?'整段':`${Number(t).toFixed(2)}s`;
function saveState(text,error=false){$('save-state').textContent=text;$('save-state').classList.toggle('error',error);}
function backup(item){try{localStorage.setItem(key(item),JSON.stringify(payload(item)));}catch{$('notice').textContent='本机草稿存储不可用，请等待项目保存成功后再关闭页面。';}}
function changed(){
 if(loadingEditor||!current)return;
 current.generation++;backup(current);saveState('待保存…');drawRail();drawIssues();
 clearTimeout(current.timer);const item=current;item.timer=setTimeout(()=>flush(item),450);
}
async function flush(item){
 clearTimeout(item.timer);
 if(item.invalid){if(item===current)saveState('请先修正问题时间或类型',true);return false;}
 if(item.conflict){if(item===current){saveState('保存冲突，草稿已保留',true);$('reload').hidden=false;}return false;}
 if(item.saving){await item.saving;return item.savedGeneration>=item.generation;}
 item.saving=(async()=>{
  while(item.savedGeneration<item.generation){
   const version=item.generation;const data=payload(item);
   if(item===current)saveState('保存中…');
   try{
    const response=await fetch(`${api}/reviews`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(!response.ok){const body=await response.json();if(response.status===409)item.conflict=true;throw new Error(response.status===409?'另一窗口已有修改，草稿已保留':typeof body.detail==='string'?body.detail:'时间区间或标注内容无效');}
    const saved=await response.json();item.revision=saved.revision;item.savedGeneration=version;
    if(item.savedGeneration<item.generation)backup(item);
    if(item.savedGeneration===item.generation){try{localStorage.removeItem(key(item));}catch{}if(item===current)saveState(item.invalid?'请检查问题时间范围':'已保存到项目',!!item.invalid);}
   }catch(error){if(item===current){saveState('未保存 · '+error.message,true);$('reload').hidden=!item.conflict;}return false;}
  }
  return true;
 })();
 try{return await item.saving;}finally{item.saving=null;}
}
function drawRail(){
 const filter=$('filter').value;
 $('shots').replaceChildren(...items.filter(item=>filter==='all'||(filter==='issues'?(item.issues.length||['rejected','trimmable'].includes(item.status)):item.status===filter)).map(item=>el('button',{type:'button',class:`shot ${item===current?'active':''}`,'aria-current':item===current?'true':null,onclick:()=>select(item)},el('span',{class:`badge ${item.status}`},labels[item.status]),el('span',{class:'number'},item.card.id.replace('sh-','')),el('span',{class:'caption'},item.card.description||item.card.section_label||''),el('span',{class:'caption'},`${item.issues.length} 条问题`))));
 $('progress').textContent=`${items.length} 段 · ${items.filter(i=>i.status!=='unreviewed').length} 段有结论 · ${items.reduce((n,i)=>n+i.issues.length,0)} 条问题`;
}
function drawIssues(){
 if(!current)return;
 $('empty-issues').hidden=!!current.issues.length;
 $('issue-list').replaceChildren(...current.issues.map((issue,index)=>el('button',{type:'button',class:`issue-row ${index===selectedIssue?'active':''}`,onclick:()=>editIssue(index)},el('span',{},`${index+1}. ${time(issue.start_seconds)}${issue.end_seconds!=null?' — '+time(issue.end_seconds):''}`),el('small',{},`${severityLabels[issue.severity||'major']} · ${issue.reasons.map(r=>categories[r]).join(' / ')}`))));
}
function editIssue(index){
 if(current)current.invalid=false;
 selectedIssue=index;const issue=current?.issues[index];$('issue-editor').hidden=!issue;if(!issue){drawIssues();return;}
 loadingEditor=true;
 $('start').value=issue.start_seconds??'';$('end').value=issue.end_seconds??'';$('severity').value=issue.severity||'major';$('note').value=issue.note||'';$('desired-change').value=issue.desired_change||'';
 $('categories').querySelectorAll('input').forEach(input=>{input.checked=issue.reasons.includes(input.value);});
 loadingEditor=false;drawIssues();
}
function select(item){
 if(current===item)return;
 if(current?.invalid){saveState('请先修正当前问题的时间范围',true);return;}
 if(current&&current.savedGeneration<current.generation)flush(current);
 video.pause();video.removeAttribute('src');video.load();$('load-video').hidden=false;
 current=item;selectedIssue=-1;
 $('shot-title').textContent=item.card.id.replace('sh-','');$('section').textContent=`${item.card.section_label||'原始素材'} · ${item.media.duration_seconds?.toFixed(2)||'—'} 秒`;
 $('intent').textContent=item.card.shot_intent||item.card.description||'';
 $('download').href=mediaURL(projectId,item.media.path);$('verdict').value=item.status;$('clock').textContent='00:00.00';
 $('prompt').textContent=item.asset.prompt||item.media.prompt||'暂无提示词';
 $('reference-images').replaceChildren(...(item.asset.references||[]).map(ref=>el('figure',{},el('img',{src:mediaURL(projectId,ref.asset_path),alt:ref.role,loading:'lazy'}),el('figcaption',{},({first_frame:'首帧',last_frame:'尾帧',identity:'身份参考',environment:'环境参考'})[ref.role]||ref.role))));
 $('previous').disabled=items.indexOf(item)===0;$('next').disabled=items.indexOf(item)===items.length-1;$('reload').hidden=!item.conflict;
 saveState(item.conflict?'草稿与项目有冲突':item.savedGeneration<item.generation?'草稿待保存':'已加载项目记录',item.conflict);
 editIssue(item.issues.length?0:-1);drawRail();
}
function addIssue(whole){
 if(!current)return;
 if(!whole&&!video.getAttribute('src')){saveState('先加载并定位视频，或选择整段问题',true);return;}
 video.pause();
 const issue={scene_id:current.card.id,start_seconds:whole?null:Number(video.currentTime.toFixed(2)),end_seconds:null,reasons:['other'],severity:'major',note:'',desired_change:''};
 current.issues.push(issue);if(current.status==='unreviewed')current.status='rejected';$('verdict').value=current.status;
 editIssue(current.issues.length-1);changed();$('note').focus();
}
function editFields(){
 if(loadingEditor||!current||selectedIssue<0)return;
 const start=$('start').value===''?null:Number($('start').value),end=$('end').value===''?null:Number($('end').value);
 const duration=Number.isFinite(video.duration)?video.duration:current.media.duration_seconds;
 if((start!=null&&(!Number.isFinite(start)||start<0))||(end!=null&&(!Number.isFinite(end)||start==null||end<start))||(duration&&(start>duration||end>duration))){current.invalid=true;saveState('请检查问题时间范围',true);return;}
 const reasons=[...$('categories').querySelectorAll('input:checked')].map(input=>input.value);
 if(!reasons.length&&!$('note').value.trim()){current.invalid=true;saveState('至少选择一种问题类型或填写说明',true);return;}
 current.invalid=false;
 Object.assign(current.issues[selectedIssue],{start_seconds:start,end_seconds:end,reasons,severity:$('severity').value,note:$('note').value,desired_change:$('desired-change').value});changed();
}
$('categories').replaceChildren(...Object.entries(categories).map(([value,label])=>el('label',{},el('input',{type:'checkbox',value,onchange:editFields}),label)));
for(const id of ['start','end','note','desired-change'])$(id).addEventListener('input',editFields);
$('severity').addEventListener('change',editFields);
$('verdict').addEventListener('change',()=>{current.status=$('verdict').value;changed();});
$('mark-time').onclick=()=>addIssue(false);$('mark-whole').onclick=()=>addIssue(true);
$('capture-end').onclick=()=>{video.pause();$('end').value=video.currentTime.toFixed(2);editFields();};
$('delete-issue').onclick=()=>{current.issues.splice(selectedIssue,1);editIssue(current.issues.length?Math.min(selectedIssue,current.issues.length-1):-1);changed();};
$('save-now').onclick=()=>current&&flush(current);
$('filter').onchange=drawRail;
$('previous').onclick=()=>select(items[items.indexOf(current)-1]);$('next').onclick=()=>select(items[items.indexOf(current)+1]);
$('load-video').onclick=async()=>{if(!current)return;video.src=mediaURL(projectId,current.media.path);try{await video.play();$('load-video').hidden=true;}catch{saveState('请使用播放器按钮播放',true);$('load-video').hidden=true;}};
video.addEventListener('timeupdate',()=>{$('clock').textContent=`${String(Math.floor(video.currentTime/60)).padStart(2,'0')}:${(video.currentTime%60).toFixed(2).padStart(5,'0')}`;});
const step=delta=>{video.pause();if(!video.getAttribute('src'))return;video.currentTime=Math.min(Number.isFinite(video.duration)?video.duration:Infinity,Math.max(0,video.currentTime+delta));};
$('step-back').onclick=()=>step(-.05);$('step-forward').onclick=()=>step(.05);
$('reload').onclick=async()=>{try{const draft=localStorage.getItem(key(current));if(draft){localStorage.setItem(key(current)+':conflict:'+Date.now(),draft);localStorage.removeItem(key(current));}}catch{}const saved=await getJSON(`${api}/reviews`);const row=saved.sources.find(r=>r.media_path===current.media.path);current.revision=row?.revision||0;current.status=row?.status||'unreviewed';current.issues=structuredClone(row?.issues||[]);current.savedGeneration=current.generation;current.conflict=false;const item=current;current=null;select(item);$('notice').textContent='已重新读取项目标注；冲突时的本机草稿仍保留，可从导出文件找回。';};
async function allSaved(){const result=await Promise.all(items.map(item=>flush(item)));return result.every(Boolean);}
function downloadJSON(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=el('a',{href:url,download:name});document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=()=>downloadJSON({version:'1.0',project_id:projectId,exported_at:new Date().toISOString(),sources:items.filter(i=>i.revision||i.generation).map(payload),browser_drafts:Object.keys(localStorage).filter(k=>k.startsWith(`openmontage:review:${projectId}:`)).flatMap(k=>{try{return [JSON.parse(localStorage.getItem(k))];}catch{return [];}})},`${projectId}-reviews.json`);
$('submit').onclick=async()=>{const button=$('submit');button.disabled=true;try{if(!await allSaved())throw Error('仍有标注未保存，请先处理右侧保存提示。');const response=await fetch(`${api}/reviews/submit`,{method:'POST'});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'提交失败');$('notice').textContent=`${data.sources.length} 段审核记录已提交，已保存素材哈希与标注快照。回到聊天告诉 Agent「标注完成，分析问题」。本次提交不会启动生成。`;}catch(e){$('notice').textContent=e.message;}finally{button.disabled=false;}};
window.addEventListener('beforeunload',event=>{if(items.some(i=>i.invalid||i.savedGeneration<i.generation)){event.preventDefault();event.returnValue='';}});
try{
 const [board,reviews,manifest]=await Promise.all([getJSON(`${api}/state`),getJSON(`${api}/reviews`),getJSON(mediaURL(projectId,'artifacts/asset_manifest.json'))]);
 $('title').textContent=`${board.title||projectId} · 标注`;
 const generationStopped=manifest.metadata?.generation_status==='stopped_by_user';
 $('notice').textContent=(generationStopped?'生成保持停止。':'')+'标注时间从每段原始素材的 0 秒开始，编辑自动保存到项目。';
 const paths=new Set();
 for(const card of board.storyboard?.scenes||[]){
  for(const media of [card.visual,...(card.takes||[])]){
   if(!media?.exists||media.type!=='video'||paths.has(media.path))continue;paths.add(media.path);
   const saved=reviews.sources.find(r=>r.media_path===media.path);
   const item={card,media,asset:manifest.assets.find(a=>a.path===media.path)||{},revision:saved?.revision||0,status:saved?.status||'unreviewed',issues:structuredClone(saved?.issues||[]),generation:0,savedGeneration:0,saving:null,conflict:false};
   try{const raw=localStorage.getItem(key(item));if(raw){const draft=JSON.parse(raw);if(draft.revision===item.revision){item.status=draft.status;item.issues=draft.issues;item.generation=1;}else item.conflict=true;}}catch{}
   items.push(item);
  }
 }
 try{const delivery=await getJSON(mediaURL(projectId,'artifacts/h3-download-manifest-v3.json'));$('download-all').href=mediaURL(projectId,delivery.archive_path);$('download-all').setAttribute('download','');$('download-all').textContent=`下载全部${delivery.count}段（ZIP）`;$('download-all').hidden=false;}catch{}
 drawRail();if(items.length){select(items[0]);for(const item of items)if(item.generation&&!item.conflict)flush(item);}else{$('notice').textContent='当前项目暂无已保存的视频素材。';document.querySelectorAll('.editor button,.editor select,.transport button,#load-video').forEach(b=>b.disabled=true);}
}catch(error){$('notice').textContent='读取失败：'+error.message;}
