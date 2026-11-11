import {el, mediaURL, subscribe, fmtMoney} from './lib.js';
import {WorkbenchStage} from './workbench-stage.js';

const parts = location.pathname.split('/').filter(Boolean);
const project = decodeURIComponent(parts[1] || ''), shot = decodeURIComponent(parts[2] || '');
const endpoint = `/api/shot/${encodeURIComponent(project)}/${encodeURIComponent(shot)}`;
const $ = id => document.getElementById(id);
const clone = value => JSON.parse(JSON.stringify(value));
const ROLES = ['identity', 'wardrobe', 'environment', 'style', 'composition', 'first_frame', 'last_frame', 'depth', 'pose'];
const ROLE_LABELS = {identity: '身份', wardrobe: '服装', environment: '环境', style: '风格', composition: '构图', first_frame: '首帧', last_frame: '尾帧', depth: '深度', pose: '姿势'};
const SHOTS = ['extreme_wide', 'wide', 'medium_wide', 'medium', 'medium_close', 'close_up', 'extreme_close_up', 'over_shoulder', 'insert', 'establishing'];
const MOVEMENTS = ['static', 'pan_left', 'pan_right', 'tilt_up', 'tilt_down', 'dolly_in', 'dolly_out', 'tracking_left', 'tracking_right', 'crane_up', 'crane_down', 'handheld', 'steadicam', 'whip_pan', 'orbital', 'zoom_in', 'zoom_out', 'rack_focus', 'orbit', 'follow'];
let state, draft, dirty = false, busy = false, compiled = null, externalRevision = null;
let ownsLease = false;
let renderRequest = null;

const stage = new WorkbenchStage($('stage'), value => {
  if (!ownsLease || busy) return;
  draft.stage = value;
  draft.framing.lens_mm = value.camera.lens_mm;
  draft.framing.camera_height = value.camera.position[1];
  for (const object of value.objects.filter(object => object.kind === 'human')) {
    if (object.subject_ref && !draft.subjects.some(subject => subject.ref_id === object.subject_ref)) {
      draft.subjects.push({kind: 'character', ref_id: object.subject_ref, action: '', visible_parts: ['full_body']});
      renderSubjects(); stage.setSubjects(draft.subjects);
    }
  }
  markDirty();
});

function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }

async function api(path = '', payload, method = 'POST') {
  const response = await fetch(endpoint + path, payload === undefined ? {} : {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) {
    const error = new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body) || `请求失败 ${response.status}`);
    error.status = response.status; error.body = body;
    if (response.status === 409) {
      $('conflict').hidden = false;
      status('保存冲突：你的草稿已保留。请核对文件中的当前版本。', true);
    }
    throw error;
  }
  return body;
}

function leasePayload() { return {revision: draft.revision, lease_id: state.lock?.lease_id}; }

function unresolvedRequest() { return ['running', 'unknown'].includes(renderRequest?.status); }

function requestMessage(request) {
  const labels = {succeeded: '生成已成功', failed: '生成失败', running: '生成仍在执行', unknown: '生成结果或费用状态未知 · 待核对费用'};
  return `${labels[request.status] || `生成状态 ${request.status}`} · 请求 ${request.request_id}${request.error ? ` · ${request.error}` : ''}`;
}

function setControls() {
  ownsLease = Boolean(state?.lock?.holder === 'user' && state.lock.lease_id && !state.lock.expired && !state.read_only);
  document.body.dataset.readOnly = String(!ownsLease);
  const disabled = !ownsLease || busy;
  $('shot-form').querySelectorAll('input,textarea,select').forEach(input => { input.disabled = disabled; });
  $('subject-list').querySelectorAll('input,textarea,select,button').forEach(input => { input.disabled = disabled; });
  $('reference-list').querySelectorAll('input,select,button').forEach(input => { input.disabled = disabled || input.dataset.canonical === 'true'; });
  for (const id of ['save', 'release', 'upload', 'add-subject', 'stage-export']) $(id).disabled = disabled;
  $('acquire').hidden = ownsLease; $('acquire').disabled = busy || (state?.lock?.holder === 'agent' && !state.lock.expired);
  $('compile').disabled = busy || !state;
  $('render').disabled = disabled || dirty || !compiled || compiled.generatable === false || compiled.lint?.some(item => item.level === 'error') || compiled.estimated_cost == null;
  $('render').textContent = unresolvedRequest() ? '查看生成状态' : '生成这个镜头';
  stage.setReadOnly(!ownsLease);
  if (busy) {
    stage.container.querySelectorAll('[data-stage-write]').forEach(node => { node.disabled = true; });
    if (stage.transform) stage.transform.enabled = false;
  }
  $('revision-label').textContent = draft ? `r${draft.revision}${dirty ? ' · 未保存' : ''}` : '';
}

function markDirty() {
  dirty = true; compiled = null; $('render').disabled = true; $('confirmation').hidden = true;
  $('compile-state').textContent = '字段已修改，请重新编译；生成前需要保存镜头单。';
  setControls();
}

async function action(fn) {
  if (busy) return;
  busy = true; setControls(); status('正在处理…');
  try { await fn(); if ($('status').textContent === '正在处理…') status('操作已完成。'); }
  catch (error) { if (error.status !== 409) status(error.message, true); }
  finally { busy = false; setControls(); }
}

async function refresh(force = false) {
  const incoming = await api();
  state = incoming;
  const activeRequest = incoming.requests?.find(request => ['running', 'unknown'].includes(request.status));
  if (activeRequest) renderRequest = {...activeRequest, completed: false};
  else if (renderRequest) {
    const current = incoming.requests?.find(request => request.request_id === renderRequest.request_id);
    if (current) renderRequest = {...current, completed: ['succeeded', 'failed'].includes(current.status)};
  }
  if (!force && draft && incoming.spec.revision === draft.revision) {
    renderArtists(); renderCandidates(); setControls(); return;
  }
  if (dirty && !force) {
    if (incoming.spec.revision !== draft.revision) { externalRevision = incoming.spec.revision; $('conflict').hidden = false; status(`文件更新至 r${externalRevision}，未保存草稿已保留。`); }
    setControls(); return;
  }
  draft = clone(incoming.spec); dirty = false; externalRevision = null; compiled = null;
  $('conflict').hidden = true; $('confirmation').hidden = true;
  $('shot-label').textContent = `${project} / ${draft.scene_id} / ${shot}`;
  $('board-link').href = `/p/${encodeURIComponent(project)}`;
  renderForm(); renderSubjects(); renderReferences(); renderArtists(); renderCandidates();
  stage.load(draft.stage, draft.target_resolution, draft.subjects);
  if (!draft.stage) {
    stage.stage.camera.lens_mm = draft.framing.lens_mm || 50;
    stage.stage.camera.position[1] = draft.framing.camera_height ?? 1.6;
    stage.updateCamera(); stage.drawCamera();
  }
  setControls();
  status(unresolvedRequest() ? requestMessage(renderRequest) : ownsLease ? '你正在编辑。保存后与 Agent 共用同一份镜头单。' : state.lock?.holder === 'agent' ? 'Agent 正在编辑 · 工作台只读' : '当前只读，接管镜头后可编辑。');
}

function field(label, value, update, {type = 'text', choices, wide = false, maxLength, min, max} = {}) {
  const input = choices ? el('select', {}, ...choices.map(choice => el('option', {value: typeof choice === 'string' ? choice : choice.value}, typeof choice === 'string' ? choice : choice.label))) : el(type === 'textarea' ? 'textarea' : 'input', type === 'textarea' ? {} : {type});
  input.value = value ?? ''; input.setAttribute('aria-label', label);
  if (maxLength) input.maxLength = maxLength;
  if (min != null) input.min = min; if (max != null) input.max = max;
  if (type === 'number') input.step = 'any';
  input.addEventListener('input', () => { if (!ownsLease || busy) return; update(type === 'number' ? Number(input.value) : input.value); markDirty(); });
  return el('label', {class: wide ? 'wide' : ''}, el('span', {}, label), input);
}

function renderForm() {
  const framing = draft.framing ||= {}, movement = draft.movement ||= {type: 'static', intensity: 0}, environment = draft.environment ||= {};
  const form = $('shot-form'); form.replaceChildren();
  form.append(field('镜头意图', draft.intent, value => { draft.intent = value; }, {type: 'textarea', wide: true, maxLength: 4000}),
    field('时长（秒）', draft.duration_seconds, value => { draft.duration_seconds = value; }, {type: 'number', min: .1, max: 3600}),
    field('画幅', draft.aspect_ratio, value => { draft.aspect_ratio = value; }),
    field('目标分辨率', draft.target_resolution, value => { draft.target_resolution = value; try { stage.resolution = value; stage.updateCamera(); stage.resize(); } catch { /* Keep invalid draft for server lint. */ } }),
    field('生成方式', draft.route, value => { draft.route = value; }, {choices: ['t2v', 'i2v', 'first_last', 'multi_ref', 'lipsync']}),
    field('目标工具', draft.target_tool || '', value => { draft.target_tool = value || null; }, {choices: [{value: '', label: '按现有选择规则'}, ...(state.tools || []).map(tool => ({value: tool.name || tool.tool_name, label: `${tool.name || tool.tool_name}${tool.available === false || tool.status === 'unavailable' ? '（未配置）' : ''}`}))]}),
    field('景别', framing.shot_size || 'medium', value => { framing.shot_size = value; }, {choices: SHOTS}),
    field('焦段（mm）', framing.lens_mm || 50, value => { framing.lens_mm = value; if (draft.stage) { stage.stage.camera.lens_mm = value; draft.stage = stage.serialise(); try { stage.updateCamera(); } catch {} } }, {type: 'number', min: 1, max: 2000}),
    field('机位高度（米）', framing.camera_height ?? 1.6, value => { framing.camera_height = value; if (draft.stage) { stage.stage.camera.position[1] = value; draft.stage = stage.serialise(); stage.updateCamera(); } }, {type: 'number'}),
    field('相机角度', framing.camera_angle || '', value => { framing.camera_angle = value; }),
    field('运动类型', movement.type, value => { movement.type = value; if (value !== 'static') movement.cause ||= ''; }, {choices: MOVEMENTS}),
    field('运动强度（0–3）', movement.intensity, value => { movement.intensity = value; }, {type: 'number', min: 0, max: 3}),
    field('运动原因', movement.cause || '', value => { movement.cause = value; }, {wide: true, maxLength: 2000}));
  for (const [key, label] of [['location', '地点'], ['time', '时间'], ['lighting', '光线'], ['palette', '色板']]) form.append(field(label, Array.isArray(environment[key]) ? environment[key].join(', ') : environment[key], value => { environment[key] = value; }, {wide: key === 'lighting'}));
  form.append(field('排除项（每行一项）', (draft.exclusions || []).join('\n'), value => { draft.exclusions = value.split('\n').map(item => item.trim()).filter(Boolean); }, {type: 'textarea', wide: true}),
    field('附加说明（原样附在最后）', draft.notes, value => { draft.notes = value; }, {type: 'textarea', wide: true, maxLength: 4000}));
}

function renderSubjects() {
  const list = $('subject-list'); list.replaceChildren();
  draft.subjects ||= [];
  draft.subjects.forEach((subject, index) => {
    const card = el('div', {class: 'subject-card'}, el('h3', {}, `主体 ${index + 1}`));
    card.append(field('主体类型', subject.kind, value => { subject.kind = value; }, {choices: ['artist', 'character', 'prop', 'environment']}),
      field('主体 ID', subject.ref_id, value => {
        const previous = subject.ref_id; subject.ref_id = value;
        for (const reference of draft.references) if (reference.subject_ref === previous) reference.subject_ref = value;
        if (draft.stage) for (const object of draft.stage.objects) if (object.subject_ref === previous) object.subject_ref = value;
        for (const object of stage.stage.objects) if (object.subject_ref === previous) object.subject_ref = value;
        stage.setSubjects(draft.subjects);
      }),
      field('位置（舞台导出自动填写）', subject.placement, value => { subject.placement = value; }, {wide: true}),
      field('动作', subject.action, value => { subject.action = value; }, {type: 'textarea', wide: true}),
      field('造型', subject.look, value => { subject.look = value; }, {wide: true}));
    const visible = Array.isArray(subject.visible_parts) ? subject.visible_parts : subject.visible_parts ? [subject.visible_parts] : [];
    const checks = el('div', {class: 'checks wide'}, '可见部位：');
    for (const [name, label] of [['face', '脸'], ['upper_body', '上半身'], ['full_body', '全身'], ['hands', '手'], ['back', '背面']]) {
      const checkbox = el('input', {type: 'checkbox', 'aria-label': `${subject.ref_id} ${label}`}); checkbox.checked = visible.includes(name);
      checkbox.addEventListener('change', () => { if (!ownsLease || busy) return; const items = new Set(Array.isArray(subject.visible_parts) ? subject.visible_parts : visible); if (checkbox.checked) items.add(name); else items.delete(name); subject.visible_parts = [...items]; markDirty(); });
      checks.append(el('label', {}, checkbox, label));
    }
    card.append(checks);
    const traits = draft.identity_context?.[subject.ref_id]?.locked_traits;
    if (traits?.length) card.append(el('div', {class: 'wide hint'}, '固定特征（只读）：', el('ul', {}, ...traits.map(trait => el('li', {}, trait)))));
    card.append(el('button', {type: 'button', onclick: () => {
      if (!ownsLease || busy) return;
      draft.subjects.splice(index, 1);
      if (draft.stage) draft.stage.objects = draft.stage.objects.filter(object => object.subject_ref !== subject.ref_id);
      draft.references = draft.references.map(reference => reference.subject_ref === subject.ref_id ? {...reference, subject_ref: ''} : reference);
      markDirty(); renderSubjects(); renderReferences(); stage.load(draft.stage, draft.target_resolution, draft.subjects); setControls();
    }}, '移除此主体'));
    list.append(card);
  });
}

function renderReferences() {
  const list = $('reference-list'); list.replaceChildren();
  for (const [index, reference] of (draft.references || []).entries()) {
    const canonical = reference.read_only;
    const card = el('article', {class: 'reference-card'});
    if (/\.(png|jpe?g|webp)$/i.test(reference.asset_path)) card.append(el('img', {src: mediaURL(project, reference.asset_path), alt: `${ROLE_LABELS[reference.role]}参考图`, loading: 'lazy'}));
    card.append(el('p', {}, reference.asset_path));
    const role = field('参考角色', reference.role, value => { reference.role = value; }, {choices: ROLES.map(name => ({value: name, label: ROLE_LABELS[name]}))});
    const subject = field('参考对应主体', reference.subject_ref || '', value => { reference.subject_ref = value; }, {choices: [{value: '', label: '全局'}, ...draft.subjects.map(item => ({value: item.ref_id, label: item.ref_id}))]});
    role.querySelector('select').dataset.canonical = String(Boolean(canonical)); subject.querySelector('select').dataset.canonical = String(Boolean(canonical));
    card.append(el('div', {class: 'reference-fields'}, role, subject), el('p', {}, `SHA256 ${reference.sha256.slice(0, 16)}…`));
    if (reference.parent_asset || reference.origin_asset) {
      const parent = reference.parent_asset || reference.origin_asset;
      card.append(el('p', {}, `来源：${parent} → ${reference.asset_path}`));
      if (/\.json$/i.test(parent) && !parent.startsWith('artists/')) card.append(el('a', {href: mediaURL(project, parent), target: '_blank', rel: 'noopener'}, '查看相机 / 姿势参数 JSON'));
    }
    if (canonical) card.append(el('span', {class: 'badge'}, '正式身份图 · 只读'));
    else {
      const weight = field('参考权重（可选）', reference.weight ?? '', value => { if (value === '') delete reference.weight; else reference.weight = Number(value); });
      card.append(weight, field('上游来源（可选）', reference.parent_asset, value => { if (value) reference.parent_asset = value; else delete reference.parent_asset; }));
      card.append(el('button', {type: 'button', onclick: () => { if (!ownsLease || busy) return; draft.references.splice(index, 1); markDirty(); renderReferences(); setControls(); }}, '移除引用'));
    }
    list.append(card);
  }
  if (!draft.references?.length) list.append(el('p', {class: 'empty'}, '还没有参考图。'));
}

function renderArtists() {
  const list = $('artist-list'); list.replaceChildren();
  const artists = Array.isArray(state.artists) ? state.artists : Object.values(state.artists || {});
  for (const artist of artists) {
    const card = el('article', {class: 'artist-card'}, el('strong', {}, artist.display_name || artist.names?.stage || artist.id || artist.artist_id));
    const traits = artist.locked_traits || artist.identity?.locked_traits || [];
    card.append(el('ul', {}, ...traits.map(trait => el('li', {}, trait))));
    for (const reference of artist.references || artist.identity?.bible || []) {
      card.append(el('p', {class: reference.canonical ? 'badge' : 'lint-warn'}, `${reference.canonical ? '正式图（只读）' : '候选图（未作为正式身份引用）'} · ${reference.view || reference.role || ''}${reference.review_status ? ` · 审核：${reference.review_status}` : ''}`));
      if (reference.asset_path || reference.project_path) card.append(el('img', {src: mediaURL(project, reference.asset_path || reference.project_path), alt: `${artist.id} 身份图`, loading: 'lazy'}));
    }
    if (artist.warnings) card.append(el('p', {class: 'lint-warn'}, artist.warnings.join('；')));
    list.append(card);
  }
  if (!artists.length) list.append(el('p', {class: 'empty'}, '此镜头没有艺人档案。'));
}

function renderCandidates() {
  const list = $('candidates'); list.replaceChildren();
  const candidates = (state.candidates || []).flatMap(asset => asset.candidates?.length
    ? asset.candidates.map((candidate, index) => ({...asset, ...candidate, selected: asset.selected_candidate === index})) : [asset]);
  for (const candidate of candidates) {
    const path = candidate.path || candidate.asset_path || candidate.media_path;
    const verification = candidate.verification || {};
    const reviewStatus = verification.status || candidate.status || 'unreviewed';
    const rejected = ['fail', 'rejected'].includes(reviewStatus) || candidate.rejected;
    const card = el('article', {class: 'candidate-card'}, el('strong', {}, `${rejected ? '废片' : '候选'} · 尝试 ${candidate.attempt || candidate.number || '1'} · 审核 ${reviewStatus}${candidate.selected ? ' · 已选用' : ''}`));
    if (path) {
      if (/\.(mp4|webm|mov)$/i.test(path)) card.append(el('video', {src: mediaURL(project, path), controls: '', preload: 'metadata'}));
      else if (/\.(png|jpe?g|webp)$/i.test(path)) card.append(el('img', {src: mediaURL(project, path), alt: '镜头候选', loading: 'lazy'}));
      card.append(el('p', {}, path));
    }
    const notes = candidate.rejection_reason || candidate.notes || verification.notes || verification.reason;
    if (notes) card.append(el('p', {}, typeof notes === 'string' ? notes : JSON.stringify(notes)));
    const log = candidate.generation_log || {};
    if (candidate.cost_usd != null) card.append(el('p', {class: 'hint'}, `本次费用 ${fmtMoney(candidate.cost_usd)}`));
    if (candidate.parent_asset || log.parent_asset) card.append(el('p', {class: 'hint'}, `来源：${candidate.parent_asset || log.parent_asset}`));
    if (candidate.compiled_hash || log.compiled_hash) card.append(el('p', {class: 'hint'}, `编译指纹 ${candidate.compiled_hash || log.compiled_hash}`));
    card.append(el('details', {}, el('summary', {}, '审核与生成来源（只读）'), el('pre', {}, JSON.stringify({verification, generation_log: log}, null, 2))));
    list.append(card);
  }
  if (!candidates.length) list.append(el('p', {class: 'empty'}, '此镜头还没有候选。'));
}

function toolDescription(result) {
  const options = result.payload?.tool_options || {};
  const fixed = ['model', 'model_version', 'model_variant', 'backend'].filter(key => options[key] != null).map(key => `${key}: ${options[key]}`);
  return `工具：${result.tool_name || draft.target_tool || '尚未选择'}${result.provider ? ` · ${result.provider}` : ''}${fixed.length ? ` · ${fixed.join(' · ')}` : ''}`;
}

function showCompiled(result) {
  compiled = result;
  const box = $('compiled-preview'); box.replaceChildren();
  box.append(el('p', {class: 'hint'}, toolDescription(result)));
  if (result.payload?.tool_options) box.append(el('details', {}, el('summary', {}, '固定工具参数（只读）'), el('pre', {}, JSON.stringify(result.payload.tool_options, null, 2))));
  box.append(el('h3', {}, '最终提示词（只读）'), el('pre', {'data-final-prompt': ''}, result.prompt || ''), el('h3', {}, 'Negative'), el('pre', {}, result.negative_prompt || '—'));
  const refs = result.payload?.reference_images || [];
  box.append(el('h3', {}, '实际传入参考顺序'), el('ol', {class: 'reference-order'}, ...refs.map(reference => el('li', {}, typeof reference === 'string' ? reference : `${ROLE_LABELS[reference.role] || reference.role || ''} · ${reference.asset_path || reference.path || ''} · ${reference.subject_ref || '全局'}`))));
  if (result.payload?.first_frame) box.append(el('p', {}, `首帧：${JSON.stringify(result.payload.first_frame)}`));
  if (result.payload?.last_frame) box.append(el('p', {}, `尾帧：${JSON.stringify(result.payload.last_frame)}`));
  if (result.payload?.reference_map) box.append(el('details', {}, el('summary', {}, '按人物分开的参考映射'), el('pre', {}, JSON.stringify(result.payload.reference_map, null, 2))));
  box.append(el('h3', {}, '检查结果'), el('ul', {}, ...(result.lint || []).map(item => el('li', {class: `lint-${item.level}`}, `${item.level.toUpperCase()} · ${item.code} · ${item.message || item.description || item.detail || ''}`))));
  box.append(el('p', {class: 'quote'}, result.estimated_cost == null ? '费用未知 · 无法生成' : `预估 ${fmtMoney(result.estimated_cost)}`), el('p', {class: 'hint'}, `编译指纹 ${result.compiled_hash || ''}`));
  $('compile-state').textContent = dirty ? '这是未保存草稿的编译结果。保存后请重新编译。' : '编译结果与当前镜头单一致。';
  setControls();
}

async function save() {
  if (!ownsLease) throw new Error('请先接管镜头');
  const result = await api('', {spec: draft, ...leasePayload()}, 'PUT');
  const saved = result.spec || result;
  draft = clone(saved); state.spec = saved; dirty = false; externalRevision = null; compiled = null;
  $('conflict').hidden = true; $('confirmation').hidden = true;
  status(`已保存镜头单 r${draft.revision}。`); renderReferences(); renderSubjects(); setControls();
  return saved;
}

async function compile() {
  showCompiled(await api('/compile', {...(ownsLease ? {spec: draft} : {}), tool_name: draft.target_tool, ...leasePayload()}));
}

async function render(confirmationToken, requestId) {
  if (!ownsLease || dirty || !compiled) throw new Error('请先保存并编译当前镜头单');
  if (unresolvedRequest() && renderRequest.compiled_hash !== compiled.compiled_hash) {
    await refresh(); status(requestMessage(renderRequest), renderRequest.status === 'unknown'); return;
  }
  if (!renderRequest || renderRequest.completed || renderRequest.compiled_hash !== compiled.compiled_hash) {
    renderRequest = {request_id: crypto.randomUUID().replaceAll('-', ''), compiled_hash: compiled.compiled_hash, completed: false};
  }
  if (requestId) renderRequest.request_id = requestId;
  let result;
  try {
    result = await api('/render', {...leasePayload(), request_id: renderRequest.request_id, compiled_hash: compiled.compiled_hash, tool_name: compiled.tool_name || draft.target_tool, ...(confirmationToken ? {confirmation_token: confirmationToken} : {})});
  } catch (error) {
    if (!error.status) { renderRequest.status = 'unknown'; error.message = `网络中断 · 请求 ${renderRequest.request_id} 的结果待核对，再试将复用同一请求。`; }
    throw error;
  }
  renderRequest = {...renderRequest, ...result, completed: ['succeeded', 'failed'].includes(result.status)};
  if (result.needs_confirmation) {
    const panel = $('confirmation'); panel.hidden = false;
    panel.replaceChildren(el('p', {}, toolDescription(compiled)), el('p', {}, `需要确认：${result.reason || '本次费用超过门槛，或首次使用付费工具。'} 预估 ${fmtMoney(result.estimated_cost ?? result.quote?.estimated_cost)}`),
      el('button', {type: 'button', onclick: () => action(async () => { panel.hidden = true; await render(result.confirmation_token, result.request_id); })}, '确认费用并生成'),
      el('button', {type: 'button', onclick: () => { panel.hidden = true; renderRequest = null; }}, '取消'));
    status('请核对费用，确认后才会执行生成。');
  } else {
    await refresh(renderRequest.completed);
    status(requestMessage(renderRequest), ['failed', 'unknown'].includes(renderRequest.status));
  }
}

async function uploadFiles(files) {
  if (!ownsLease) throw new Error('请先接管镜头');
  if (dirty) await save();
  for (const file of files) {
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw new Error('只接受 PNG / JPG / WebP 图片');
    if (file.size > 10 * 1024 * 1024) throw new Error('参考图片不能超过 10 MB');
    const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
    const result = await api('/references', {...leasePayload(), data, role: 'composition'});
    draft = clone(result.spec); state.spec = draft;
  }
  dirty = false; compiled = null; renderReferences(); setControls(); status('参考图已上传。可设置参考角色与对应主体。'); $('upload').value = '';
}

$('save').addEventListener('click', () => action(save));
$('compile').addEventListener('click', () => action(compile));
$('render').addEventListener('click', () => action(() => render()));
$('reload').addEventListener('click', () => action(() => refresh(true)));
$('acquire').addEventListener('click', () => action(async () => { await api('/acquire', {reason: '用户在镜头工作台接管编辑'}); await refresh(true); }));
$('release').addEventListener('click', () => action(async () => { if (dirty) await save(); const result = await api('/release', {lease_id: state.lock?.lease_id}); await refresh(true); status(`已交回 Agent。修改字段：${(result.changed_fields || result.changes || []).map(item => typeof item === 'string' ? item : item.path).join('、') || '无新增修改'}`); }));
$('add-subject').addEventListener('click', () => { if (!ownsLease || busy) return; draft.subjects.push({kind: 'character', ref_id: `person-${draft.subjects.length + 1}`, action: '', visible_parts: ['full_body']}); markDirty(); renderSubjects(); stage.setSubjects(draft.subjects); setControls(); });
$('stage-export').addEventListener('click', () => action(async () => {
  if (!ownsLease) throw new Error('请先接管镜头');
  if (dirty) await save();
  const output = await stage.export();
  const result = await api('/stage-export', {...leasePayload(), stage: output.stage, images: output.images, pose: output.pose});
  draft = clone(result.spec || result); state.spec = draft; dirty = false; compiled = null;
  renderReferences(); renderSubjects(); stage.load(draft.stage, draft.target_resolution, draft.subjects); setControls(); status(`舞台已导出：composition / depth / pose，镜头单 r${draft.revision}。`);
}));
$('upload').addEventListener('change', () => action(() => uploadFiles([...$('upload').files])));
const dropZone = $('drop-zone');
dropZone.addEventListener('dragover', event => { event.preventDefault(); if (ownsLease) dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', event => { event.preventDefault(); dropZone.classList.remove('dragover'); if (ownsLease) action(() => uploadFiles([...event.dataTransfer.files])); });

await action(() => refresh(true));
subscribe(`/api/project/${encodeURIComponent(project)}/events`, () => { if (!busy) refresh().catch(error => status(error.message, true)); });
setInterval(() => {
  if (ownsLease && !busy) api('/heartbeat', {lease_id: state.lock?.lease_id}).catch(() => { ownsLease = false; refresh().catch(error => status(error.message, true)); });
}, 60000);
