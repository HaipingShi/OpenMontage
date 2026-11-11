import {el, mediaURL} from '/ui/lib.js';

export const reviewLabels = {unreviewed: '未标注', approved: '通过', rejected: '不合格', trimmable: '可裁切使用'};
const reasonLabels = {face: '面貌', action: '动作', costume: '服装', lip_sync: '口型', framing: '构图', camera: '运镜', lighting: '光线', environment: '环境', objects: '手 / 道具', continuity: '时序', other: '其他'};
const timeText = value => value == null ? '整段' : `${Number(value).toFixed(2)}s`;
export function sourceReview(reviews, path) {
  return (reviews.sources || []).find(record => record.media_path === path);
}

export function openShotReview({projectId, card, media, reviews, modal, onSaved}) {
  const saved = sourceReview(reviews, media.path);
  let issues = structuredClone(saved?.issues || []);
  let editingIssue = null;
  modal.replaceChildren();
  const page = el('div', {class: 'modal-page shot-review'});
  const close = () => { viewer.pause?.(); modal.classList.remove('open'); };
  const viewer = media.type === 'video'
    ? el('video', {src: mediaURL(projectId, media.path), controls: '', preload: 'metadata', playsinline: ''})
    : el('img', {src: mediaURL(projectId, media.path), alt: `镜头 ${card.id}`});
  const status = el('select', {'aria-label': '审片状态'},
    Object.entries(reviewLabels).map(([value, label]) => el('option', {value}, label)));
  status.value = saved?.status || 'unreviewed';
  const start = el('input', {type: 'number', min: 0, step: '0.01', placeholder: '整段', 'aria-label': '问题开始秒数'});
  const end = el('input', {type: 'number', min: 0, step: '0.01', placeholder: '单个时间点', 'aria-label': '问题结束秒数'});
  const note = el('textarea', {maxlength: 4000, rows: 3, placeholder: '描述问题或裁切建议', 'aria-label': '问题说明'});
  const reasons = Object.entries(reasonLabels).map(([value, label]) => {
    const input = el('input', {type: 'checkbox', value});
    return {input, node: el('label', {}, input, label)};
  });
  const message = el('p', {class: 'review-message', role: 'status', 'aria-live': 'polite'});
  const list = el('div', {class: 'review-issues'});
  const drawIssues = () => {
    list.replaceChildren(...issues.map((issue, index) => el('div', {class: 'review-issue'},
      el('span', {}, `${issue.scene_id} · ${timeText(issue.start_seconds)}${issue.end_seconds != null ? '–' + timeText(issue.end_seconds) : ''} · ${issue.reasons.map(key => reasonLabels[key]).join('、')} ${issue.note}`),
      el('button', {type: 'button', onclick: () => {
        editingIssue = issue;
        start.value = issue.start_seconds ?? ''; end.value = issue.end_seconds ?? ''; note.value = issue.note;
        reasons.forEach(({input}) => {input.checked = issue.reasons.includes(input.value);});
        message.textContent = '修改内容后点击加入问题或保存。';
      }}, '编辑'),
      el('button', {type: 'button', onclick: () => {
        if (editingIssue === issue) {
          editingIssue = null; start.value = ''; end.value = ''; note.value = '';
          reasons.forEach(({input}) => {input.checked = false;});
        }
        issues.splice(index, 1); drawIssues();
      }}, '删除'))));
  };
  const capture = input => {
    viewer.pause?.();
    input.value = Number(viewer.currentTime || 0).toFixed(2);
  };
  const addIssue = () => {
    const a = start.value === '' ? null : Number(start.value);
    const b = end.value === '' ? null : Number(end.value);
    const selected = reasons.filter(({input}) => input.checked).map(({input}) => input.value);
    if ((!selected.length && !note.value.trim()) || (b != null && (a == null || b < a)) ||
        (a != null && (!Number.isFinite(a) || a < 0)) || (b != null && (!Number.isFinite(b) || b < 0))) {
      message.textContent = '请填写原因或说明，并检查时间区间。'; return false;
    }
    if (Number.isFinite(viewer.duration) && ((a || 0) > viewer.duration || (b || 0) > viewer.duration)) {
      message.textContent = '问题时间不能超过素材时长。'; return false;
    }
    const issue = {...(editingIssue || {}), scene_id: editingIssue?.scene_id || card.id, start_seconds: a, end_seconds: b, reasons: selected, note: note.value.trim()};
    const index = issues.indexOf(editingIssue);
    if (index >= 0) issues[index] = issue; else issues.push(issue);
    editingIssue = null;
    start.value = ''; end.value = ''; note.value = '';
    reasons.forEach(({input}) => {input.checked = false;});
    message.textContent = '问题已加入，点击保存写入项目。'; drawIssues(); return true;
  };
  const save = el('button', {type: 'button', class: 'review-save', onclick: async () => {
    if (note.value.trim() || reasons.some(({input}) => input.checked) || start.value || end.value) {
      if (!addIssue()) return;
    }
    save.disabled = true; message.textContent = '正在保存…';
    try {
      const response = await fetch(`/api/project/${encodeURIComponent(projectId)}/reviews`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scene_id: card.id, media_path: media.path, revision: saved?.revision || 0, status: status.value, issues}),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(response.status === 409 ? '其他窗口已修改，请关闭后重新打开标注。' : String(error.detail || '保存失败'));
      }
      await onSaved(); close();
    } catch (error) {message.textContent = `保存失败：${error.message}`;}
    finally {save.disabled = false;}
  }}, '保存到项目');
  const linked = saved?.linked_scene_ids || (reviews.sceneLinks?.[media.path]) || [card.id];
  page.append(el('button', {type: 'button', onclick: close}, '关闭'),
    el('h2', {}, `镜头标注 · ${card.id}`), viewer,
    el('p', {class: 'review-source'}, `源素材：${media.path} · 关联镜头：${linked.join('、')}`),
    el('p', {}, '状态与问题按源素材共享；时间以当前素材起点为 0 秒。'),
    el('label', {}, '审片状态 ', status), list,
    el('div', {class: 'review-times'}, el('label', {}, '开始（秒）', start),
      el('button', {type: 'button', disabled: media.type !== 'video' ? '' : null, onclick: () => capture(start)}, '暂停并取当前点'),
      el('label', {}, '结束（秒，可选）', end),
      el('button', {type: 'button', disabled: media.type !== 'video' ? '' : null, onclick: () => capture(end)}, '暂停并取结束点')),
    el('div', {class: 'review-reasons'}, reasons.map(({node}) => node)), note,
    el('button', {type: 'button', onclick: addIssue}, '加入问题'), message, save);
  drawIssues(); modal.append(page); modal.classList.add('open');
  // Closing or replacing the modal also stops its detached media player.
  const observer = new MutationObserver(() => {
    if (!modal.classList.contains('open') || !viewer.isConnected) {viewer.pause?.(); observer.disconnect();}
  });
  observer.observe(modal, {attributes: true, childList: true});
}

export function renderFixList(reviews, onOpen) {
  const sources = reviews.fix_list || [];
  return el('section', {class: 'shot-fix-list'},
    el('div', {class: 'section-title'}, '审片修正清单', el('span', {class: 'meta'}, `${sources.length} 个源素材`)),
    sources.length ? sources.map(record => el('div', {class: 'review-issue'},
      el('div', {}, el('b', {}, `${reviewLabels[record.status]} · ${record.linked_scene_ids.join('、') || '原镜头已变更'}`),
        el('div', {class: 'review-source'}, record.media_path),
        ...record.issues.map(issue => el('p', {}, `${issue.scene_id} · ${timeText(issue.start_seconds)}${issue.end_seconds != null ? '–' + timeText(issue.end_seconds) : ''} · ${issue.reasons.map(key => reasonLabels[key]).join('、')} ${issue.note}${issue.desired_change ? ' → '+issue.desired_change : ''}`))),
      record.linked_scene_ids.length ? el('button', {type: 'button', onclick: () => onOpen(record)}, '查看 / 修改') : null))
      : el('p', {}, '暂无修正项。点击镜头下方「标注」记录审片反馈。'));
}
