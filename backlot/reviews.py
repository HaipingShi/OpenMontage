"""User shot feedback, stored independently of production artifacts."""
from __future__ import annotations

import json
import hashlib
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from backlot.state import load_board_state

_lock = threading.RLock()


class Issue(BaseModel):
    scene_id: str = Field(min_length=1, max_length=200)
    start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reasons: list[Literal['face', 'action', 'costume', 'lip_sync', 'framing', 'camera', 'lighting', 'environment', 'objects', 'continuity', 'other']] = Field(default_factory=list, max_length=11)
    note: str = Field(default='', max_length=4000)
    severity: Literal['minor', 'major', 'blocking'] = 'major'
    desired_change: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def valid_range(self):
        if self.end_seconds is not None and (self.start_seconds is None or self.end_seconds < self.start_seconds):
            raise ValueError('end must follow start')
        if not self.reasons and not self.note.strip():
            raise ValueError('issue requires a reason or note')
        return self


class ShotReview(BaseModel):
    scene_id: str = Field(min_length=1, max_length=200)
    media_path: str = Field(min_length=1, max_length=2000)
    revision: int = Field(default=0, ge=0)
    status: Literal['unreviewed', 'approved', 'rejected', 'trimmable']
    issues: list[Issue] = Field(default_factory=list, max_length=200)


def _path(project: Path) -> Path:
    path = project / 'reviews' / 'shot_reviews.json'
    if not path.resolve().is_relative_to(project.resolve()):
        raise HTTPException(403, 'review path escapes project')
    return path


def _read(project: Path) -> dict:
    path = _path(project)
    if not path.exists():
        return {'version': '1.0', 'sources': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('sources'), dict):
            raise ValueError('invalid sources')
        for key, record in data['sources'].items():
            if not isinstance(record, dict) or record.get('media_path') != key:
                raise ValueError('invalid source review')
            ShotReview.model_validate({**record, 'scene_id': '_validation'})
        return data
    except (ValueError, OSError) as exc:
        raise HTTPException(409, 'Saved reviews are unreadable; refusing to overwrite them') from exc


def _sources(project: Path) -> dict[str, list[str]]:
    board = load_board_state(project).get('storyboard') or {}
    sources: dict[str, list[str]] = {}
    for scene in board.get('scenes', []):
        for media in [scene.get('visual'), *scene.get('takes', [])]:
            if not media or not media.get('exists') or not media.get('path'):
                continue
            target = (project / media['path']).resolve()
            if not target.is_relative_to(project.resolve()):
                continue
            key = target.relative_to(project.resolve()).as_posix()
            ids = sources.setdefault(key, [])
            if scene['id'] not in ids:
                ids.append(scene['id'])
    return sources


def load_reviews(project: Path) -> dict:
    with _lock:
        data = _read(project)
        links = _sources(project)
        records = [{**record, 'linked_scene_ids': links.get(key, [])}
                   for key, record in data['sources'].items()]
        return {'version': data['version'], 'sources': records,
                'fix_list': [record for record in records
                             if record['status'] in ('rejected', 'trimmable') or record['issues']]}


def save_review(project: Path, review: ShotReview) -> dict:
    target = (project / review.media_path).resolve()
    if not target.is_relative_to(project.resolve()):
        raise HTTPException(403, 'media path escapes project')
    key = target.relative_to(project.resolve()).as_posix()
    with _lock:
        links = _sources(project)
        if review.scene_id not in links.get(key, []):
            raise HTTPException(422, 'media does not belong to this scene')
        if any(issue.scene_id not in links[key] for issue in review.issues):
            raise HTTPException(422, 'issue scene does not use this source')
        data = _read(project)
        previous = data['sources'].get(key, {})
        if review.revision != previous.get('revision', 0):
            raise HTTPException(409, 'Review changed in another window; reopen it before saving')
        record = {'media_path': key, 'status': review.status,
                  'issues': [issue.model_dump() for issue in review.issues],
                  'revision': review.revision + 1,
                  'updated_at': datetime.now(timezone.utc).isoformat()}
        data['sources'][key] = record
        path = _path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f'.{uuid.uuid4().hex}.tmp')
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return {**record, 'linked_scene_ids': links[key]}


def submit_reviews(project: Path) -> dict:
    """Freeze human notes and source hashes for later analysis; no approval/generation."""
    with _lock:
        reviews = load_reviews(project)
        if not reviews['sources']:
            raise HTTPException(422, '请先保存至少一段素材的标注')
        sources = []
        for record in reviews['sources']:
            target = (project / record['media_path']).resolve()
            if not target.is_relative_to(project.resolve()) or not target.is_file():
                raise HTTPException(409, '标注素材已变更或不存在，请重新确认')
            digest = hashlib.sha256()
            with target.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            sources.append({**record, 'source_sha256': digest.hexdigest()})
        ident = uuid.uuid4().hex
        result = {'version': '1.0', 'submission_id': ident,
                  'submitted_at': datetime.now(timezone.utc).isoformat(),
                  'project_id': project.name, 'review_file': 'reviews/shot_reviews.json',
                  'snapshot_path': f'reviews/submissions/{ident}.json', 'sources': sources,
                  'purpose': 'human_annotations_for_agent_analysis',
                  'generation_authorized': False}
        directory = _path(project).parent
        snapshots = directory / 'submissions'
        if not snapshots.resolve().is_relative_to(project.resolve()):
            raise HTTPException(403, 'submission path escapes project')
        snapshots.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        (snapshots / f'{ident}.json').write_text(payload, encoding='utf-8')
        handoff = directory / 'review_handoff.json'
        if not handoff.resolve().is_relative_to(project.resolve()):
            raise HTTPException(403, 'handoff path escapes project')
        tmp = directory / f'.{ident}.tmp'
        try:
            tmp.write_text(payload, encoding='utf-8')
            tmp.replace(handoff)
        finally:
            tmp.unlink(missing_ok=True)
        return result
