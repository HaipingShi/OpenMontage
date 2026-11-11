// Add the same review editor to a project's hand-authored assets review page.
import {getJSON, el} from '/ui/lib.js';
import {openShotReview, sourceReview, reviewLabels, renderFixList} from '/ui/reviews.js';
const projectId = decodeURIComponent(location.pathname.split('/')[2]);
const modal = el('div', {class: 'modal-bg', id: 'shot-review-modal'});
document.body.append(modal);
modal.addEventListener('click', event => {if (event.target === modal) modal.classList.remove('open');});
document.addEventListener('keydown', event => {if (event.key === 'Escape') modal.classList.remove('open');});
const summary = el('div');
document.querySelector('main').append(summary);
let board, reviews;
async function refreshReviews() {
  [board, reviews] = await Promise.all([
    getJSON(`/api/project/${encodeURIComponent(projectId)}/state`),
    getJSON(`/api/project/${encodeURIComponent(projectId)}/reviews`),
  ]);
  reviews.sceneLinks = {};
  for (const card of board.storyboard?.scenes || []) {
    for (const media of [card.visual, ...card.takes]) {
      if (!media?.exists) continue;
      const ids = reviews.sceneLinks[media.path] ||= [];
      if (!ids.includes(card.id)) ids.push(card.id);
    }
  }
  decorate();
  summary.replaceChildren(renderFixList(reviews, record => {
    const card = board.storyboard.scenes.find(c => record.linked_scene_ids.includes(c.id));
    const media = [card?.visual, ...(card?.takes || [])].find(m => m?.path === record.media_path);
    if (card && media) open(card, media);
  }));
}
function open(card, media) {
  document.querySelectorAll('article video').forEach(video => video.pause());
  openShotReview({projectId, card, media, reviews, modal, onSaved: refreshReviews});
}
function decorate() {
  if (!board) return;
  for (const article of document.querySelectorAll('article')) {
    const id = article.querySelector('h3')?.textContent;
    const card = board.storyboard?.scenes.find(c => c.id === id);
    if (!card?.visual?.exists) continue;
    let button = article.querySelector('.shot-review-button');
    if (!button) {
      button = el('button', {class: 'shot-review-button', type: 'button', onclick: () => open(card, card.visual)});
      article.append(button);
    }
    const record = sourceReview(reviews, card.visual.path);
    button.textContent = `${reviewLabels[record?.status || 'unreviewed']} · 标注`;
  }
}
new MutationObserver(decorate).observe(document.getElementById('sections'), {childList: true});
refreshReviews().catch(error => {summary.textContent = `读取标注失败：${error.message}`;});
