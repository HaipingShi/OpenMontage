"""File-first shot specifications shared by agents, CLI and Backlot."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import jsonschema

from lib.paths import PROJECTS_DIR
from schemas.artifacts import validate_artifact


class ShotError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code, self.status_code = code, status_code


class ShotConflict(ShotError):
    def __init__(self, message: str = 'Shot changed; reload before saving'):
        super().__init__('REVISION_CONFLICT', message, 409)


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise ShotError('UNSAFE_ID', 'Expected a project or shot identifier', 400)
    return value


def project_path(project: str | Path, root: Path | None = None) -> Path:
    if isinstance(project, Path):
        path = project.resolve()
        if root is not None and not path.is_relative_to(root.resolve()):
            raise ShotError('UNSAFE_PATH', 'Project escapes projects root', 403)
    else:
        base = Path(root or PROJECTS_DIR).resolve()
        path = (base / identifier(project)).resolve()
        if not path.is_relative_to(base):
            raise ShotError('UNSAFE_PATH', 'Project escapes projects root', 403)
    if not path.is_dir():
        raise ShotError('PROJECT_NOT_FOUND', 'Project does not exist', 404)
    return path


def safe_path(project: Path, relative: str | Path) -> Path:
    base = Path(project).resolve()
    text = str(relative)
    rel = Path(text)
    if rel.is_absolute() or '..' in rel.parts or '\\' in text or '\x00' in text:
        raise ShotError('UNSAFE_PATH', 'Path must remain inside the project', 403)
    path = (base / rel).resolve()
    if not path.is_relative_to(base):
        raise ShotError('UNSAFE_PATH', 'Symlink or path escapes project', 403)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('JSON file exceeds size limit')
        return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, OSError) as exc:
        raise ShotError('CORRUPT_FILE', f'Refusing to overwrite unreadable {path.name}', 409) from exc


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temp.open('w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


_MUTEXES: dict[str, threading.RLock] = {}
_MUTEX_MAP_LOCK = threading.Lock()
_TRANSACTION_LOCAL = threading.local()


@contextmanager
def transaction(project: Path, name: str = 'project'):
    """Cross-process short filesystem mutex; business leases live separately."""
    # Names include the shot- prefix, while shot ids themselves allow 128
    # characters. Validate that full name before reducing its filename length.
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}', name):
        raise ShotError('UNSAFE_ID', 'Expected an internal mutex identifier', 400)
    mutex_name = name if len(name) <= 128 else hashlib.sha256(name.encode()).hexdigest()
    mutex = safe_path(project, f'shots/.mutex/{mutex_name}.guard')
    mutex.parent.mkdir(parents=True, exist_ok=True)
    key = str(mutex)
    with _MUTEX_MAP_LOCK:
        local_lock = _MUTEXES.setdefault(key, threading.RLock())
    with local_lock:
        held = getattr(_TRANSACTION_LOCAL, 'held', None)
        if held is None:
            held = _TRANSACTION_LOCAL.held = set()
        if key in held:
            yield
            return
        with mutex.open('a+b') as handle:
            windows = __import__('os').name == 'nt'
            if windows:
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                if windows:
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def validate(spec: dict) -> None:
    try:
        # JSON schema does not reject NaN by itself.
        json.dumps(spec, allow_nan=False)
        validate_artifact('shot_spec', spec)
        refs = [s['ref_id'] for s in spec['subjects']]
        if len(refs) != len(set(refs)):
            raise ValueError('Duplicate subject ref_id')
        stage = spec.get('stage')
        if stage:
            ids = [obj['id'] for obj in stage['objects']]
            if len(ids) != len(set(ids)):
                raise ValueError('Duplicate stage object id')
            camera = stage['camera']
            if camera['position'] == camera['target'] or camera.get('near', .1) >= camera.get('far', 100):
                raise ValueError('Invalid stage camera')
            if any(o.get('subject_ref') and o['subject_ref'] not in refs for o in stage['objects']):
                raise ValueError('Stage object references an unknown subject')
    except (jsonschema.ValidationError, ValueError, TypeError, KeyError) as exc:
        raise ShotError('INVALID_SPEC', str(exc)[:1000]) from exc


def load(project: str | Path, shot_id: str) -> dict:
    path = safe_path(project_path(project), f'shots/{identifier(shot_id)}.json')
    spec = read_json(path)
    if spec is None:
        raise ShotError('SHOT_NOT_FOUND', 'Shot specification does not exist', 404)
    validate(spec)
    if spec['shot_id'] != shot_id:
        raise ShotError('SHOT_ID_MISMATCH', 'Stored shot id does not match its filename', 409)
    return spec


def save(project: str | Path, spec: dict, expected_revision: int | None = None,
         holder: str = 'agent', lease_id: str | None = None) -> dict:
    from datetime import datetime, timezone
    from lib.shot_workbench import require_lock
    project = project_path(project)
    shot_id = identifier(spec.get('shot_id'))
    with transaction(project, f'shot-{shot_id}'):
        require_lock(project, shot_id, holder, lease_id)
        path = safe_path(project, f'shots/{shot_id}.json')
        previous = read_json(path)
        revision = previous['revision'] if previous else 0
        expected = spec.get('revision') if expected_revision is None else expected_revision
        if type(expected) is not int or expected != revision or spec.get('revision') != revision:
            raise ShotConflict()
        if previous and spec.get('scene_id') != previous['scene_id']:
            raise ShotError('IMMUTABLE_SCENE', 'A shot cannot change its scene id', 409)
        result = copy.deepcopy(spec)
        result.update(revision=revision + 1, updated_by=holder, updated_at=datetime.now(timezone.utc).isoformat())
        validate(result)
        atomic_json(path, result)
        # Saving renews this exact lease, while still inside the shot mutex.
        lock_path = safe_path(project, f'shots/{shot_id}.lock')
        lease = read_json(lock_path)
        lease['updated_at'] = result['updated_at']
        atomic_json(lock_path, lease)
        return result


def _artifact(project: Path, name: str) -> dict:
    for relative in [f'artifacts/{name}.json', f'{name}.json']:
        data = read_json(safe_path(project, relative))
        if data is not None:
            if not isinstance(data, dict):
                raise ShotError('INVALID_ARTIFACT', f'{name} must be an object', 409)
            return data
    for cp in sorted(project.glob('checkpoint_*.json')):
        data = read_json(safe_path(project, cp.relative_to(project)), {})
        if not isinstance(data, dict) or not isinstance(data.get('artifacts', {}), dict):
            raise ShotError('INVALID_ARTIFACT', 'Checkpoint and its artifacts must be objects', 409)
        value = data.get('artifacts', {}).get(name)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            path = Path(value)
            if path.is_absolute():
                if not path.resolve().is_relative_to(project.resolve()):
                    raise ShotError('UNSAFE_PATH', 'Checkpoint artifact escapes project', 403)
            else:
                path = safe_path(project, value)
            artifact = read_json(path, {})
            if not isinstance(artifact, dict):
                raise ShotError('INVALID_ARTIFACT', f'{name} must be an object', 409)
            return artifact
    return {}


def _reference(project: Path, source: Path, role: str, subject_ref: str | None = None,
               parent_asset: str | None = None, read_only: bool = False) -> dict:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if not source.resolve().is_relative_to(project.resolve()):
        raise ShotError('EXPLICIT_IMPORT_REQUIRED', 'Import outside assets into the project before citing them', 403)
    dest = source.resolve()
    ref = {'asset_path':dest.relative_to(project.resolve()).as_posix(), 'sha256':digest,'role':role}
    if subject_ref: ref['subject_ref'] = subject_ref
    if parent_asset: ref['parent_asset'] = parent_asset
    if read_only: ref['read_only'] = True
    return ref


def from_scene(scene: dict, project: str | Path) -> dict:
    """Create a structural draft; unknown creative choices stay empty for lint."""
    from lib.artist_profile import bible_assets
    from lib.project_assets import project_artist_root, load_project_artist, binding_descriptor
    project = project_path(project)
    sl = scene.get('shot_language') or {}
    context = copy.deepcopy(scene.get('generation_context') or {})
    framing = sl.get('shot_size') or scene.get('framing') or 'medium'
    from lib.shot_prompt_builder import _SHOT_SIZE_PHRASES, _MOVEMENT_PHRASES
    if framing not in _SHOT_SIZE_PHRASES: framing = 'medium'
    movement = sl.get('camera_movement') or scene.get('movement') or 'static'
    if movement not in {*_MOVEMENT_PHRASES, 'orbit', 'follow'}: movement = 'static'
    actions = {a['character_id']: '. '.join(a.get('action_sequence',[])) for a in scene.get('character_actions',[]) if a.get('character_id')}
    artist_ids = list(dict.fromkeys(context.get('artist_ids') or []))
    artist_root = project_artist_root(project)
    artist_ids += [ref for ref in actions if ref not in artist_ids and (safe_path(artist_root, ref)/'artist.yaml').is_file()]
    warnings, identities, refs, subjects = [], {}, [], []
    for ref_id in list(dict.fromkeys([*artist_ids, *actions])):
        is_artist = ref_id in artist_ids
        part = 'hands' if framing in {'insert','extreme_close_up'} else 'face'
        subject = {'kind':'artist' if is_artist else 'character','ref_id':ref_id,'placement':'',
                   'action':actions.get(ref_id,''),'visible_parts':[part],'look':''}
        if is_artist:
            try:
                profile = load_project_artist(project, ref_id)
                identity = profile['identity']; look = identity['looks'][0]
                subject['look'] = look['id']
                identities[ref_id] = {'name':profile['names'].get('latin') or profile['names']['stage'],
                    'prompt_core':identity['prompt_core'],'negative_prompt':identity.get('negative_prompt',''),
                    'wardrobe':look['wardrobe'],'locked_traits':identity['locked_traits'],
                    'visible_traits':identity.get('visible_traits',{}), 'profile_version':profile['version'],
                    'source_binding':binding_descriptor(project, ref_id)}
                official = bible_assets(profile, look['id'])
                if not official: warnings.append(f'{ref_id}: canonical identity reference is missing')
                # Unapproved and superseded wardrobe/identity candidates remain
                # in the artist browser; initialization cannot promote them.
                for asset in official:
                    role = 'identity' if asset.get('reference_role','identity') == 'identity' else 'wardrobe'
                    if role == 'wardrobe' and asset.get('reference_role') != 'wardrobe_proportions': continue
                    path = Path(asset['abs_path'])
                    if path.is_file():
                        entry = _reference(project,path,role,ref_id,path.relative_to(project).as_posix(),True)
                        entry['source_artist'] = ref_id
                        refs.append(entry)
            except ShotError:
                raise
            except (FileNotFoundError, KeyError, ValueError) as exc:
                warnings.append(f'{ref_id}: identity could not be loaded ({type(exc).__name__})')
        subjects.append(subject)
    character_design = _artifact(project, 'character_design')
    for character in character_design.get('characters',[]):
        if character.get('id') in actions:
            identities.setdefault(character['id'], {'name':character.get('display_name',character['id']),
                'prompt_core':character.get('identity_prompt',character.get('silhouette_notes','')),
                'wardrobe':character.get('wardrobe',''),'locked_traits':character.get('locked_traits',[]),
                'visible_traits':character.get('visible_traits',{})})
    manifest = _artifact(project,'asset_manifest')
    for asset in manifest.get('assets',[]):
        if asset.get('scene_id') != scene['id']: continue
        for ref in asset.get('references',[]):
            if isinstance(ref,dict) and ref.get('asset_path'):
                source = safe_path(project,ref['asset_path'])
                if source.is_file(): refs.append(_reference(project,source,ref.get('role','composition'),ref.get('subject_ref'),ref.get('parent_asset')))
        if asset.get('type') == 'image' and asset.get('path'):
            source = safe_path(project,asset['path'])
            if source.is_file(): refs.append(_reference(project,source,'first_frame'))
    seen = set(); unique = []
    for ref in refs:
        key=(ref['sha256'],ref['role'],ref.get('subject_ref'))
        if key not in seen: unique.append(ref); seen.add(key)
    route_map={'story_i2v':'i2v','performance_lipsync':'lipsync','continuation':'i2v','cast_multi_ref':'multi_ref'}
    route=route_map.get(scene.get('render_route')) or ('i2v' if any(r['role']=='first_frame' for r in unique) else 't2v')
    duration=scene.get('end_seconds',5)-scene.get('start_seconds',0)
    if not math.isfinite(duration) or duration <= 0: raise ShotError('INVALID_DURATION','Scene must have positive duration')
    spec={'shot_id':identifier(scene['id']),'scene_id':scene['id'],'revision':0,'updated_by':'agent',
        'intent':scene.get('shot_intent') or scene.get('description',''), 'duration_seconds':duration,
        'aspect_ratio':'16:9','target_resolution':'1920x1080',
        'framing':{'shot_size':framing,'lens_mm':sl.get('lens_mm',50),'camera_height':1.6,'camera_angle':'level','depth_of_field':sl.get('depth_of_field','medium')},
        'movement':{'type':movement,'intensity':0 if movement=='static' else 1,'cause':scene.get('movement_cause','')},
        'subjects':subjects,'references':unique,
        'environment':{'location':scene.get('description','') if not subjects else '', 'time':'',
                       'lighting':sl.get('lighting_key','natural'),'palette':sl.get('color_temperature','neutral'),
                       'lighting_key':sl.get('lighting_key','natural'),'color_temperature':sl.get('color_temperature','neutral')},
        'exclusions':[],'notes':'','route':route,'target_tool':None,
        'identity_context':identities,'generation_context':context,'initialization_warnings':warnings}
    if context.get('continuation_of'): spec['continuation_of'] = context['continuation_of']
    for texture in scene.get('texture_keywords',[]):
        if re.search(r'\b(no|without|not)\b|没有|不要|不出现',texture,re.I): spec['exclusions'].append(texture)
    validate(spec)
    return spec
