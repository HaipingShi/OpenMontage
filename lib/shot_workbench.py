"""Shot leases, handoffs and headless CLI; all state remains on disk."""
from __future__ import annotations

import argparse
import copy
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.shot_spec import (ShotError, _artifact, atomic_json, from_scene, identifier,
                           load, project_path, read_json, safe_path, save, transaction, validate)

LEASE_SECONDS = 2 * 60 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expired(lease: dict) -> bool:
    try:
        updated = datetime.fromisoformat(lease.get('updated_at', lease['since']).replace('Z', '+00:00'))
        if updated.tzinfo is None:
            return True
        return (datetime.now(timezone.utc) - updated).total_seconds() > LEASE_SECONDS
    except (ValueError, KeyError, TypeError):
        return True


def _lease(project: Path, shot_id: str) -> dict | None:
    data = read_json(safe_path(project, f'shots/{identifier(shot_id)}.lock'))
    if data is not None and (not isinstance(data,dict) or data.get('holder') not in {'agent','user'}):
        raise ShotError('CORRUPT_LOCK', 'Unreadable shot lease; explicit recovery is required', 409)
    return data


def initialize(project: str | Path, shot_id: str) -> dict:
    project = project_path(project); identifier(shot_id)
    with transaction(project, f'shot-{shot_id}'):
        path = safe_path(project, f'shots/{shot_id}.json')
        if path.exists():
            return load(project, shot_id)
        plan = _artifact(project, 'scene_plan')
        scene = next((s for s in plan.get('scenes',[]) if s.get('id') == shot_id or s.get('shot_id') == shot_id), None)
        if scene is None:
            raise ShotError('SCENE_NOT_FOUND', 'Shot id does not match a scene in scene_plan', 404)
        spec = from_scene(scene, project)
        spec['shot_id'] = shot_id
        validate(spec)
        atomic_json(path, spec)
        return spec


def status(project: str | Path, shot_id: str, holder: str = 'user') -> dict:
    project = project_path(project); identifier(shot_id)
    with transaction(project, f'shot-{shot_id}'):
        lease = _lease(project,shot_id)
        expired = bool(lease and _expired(lease))
        return {'spec':load(project,shot_id), 'lock':lease, 'expired':expired,
                'read_only':bool(not lease or expired or lease['holder'] != holder)}


def require_lock(project: str | Path, shot_id: str, holder: str, lease_id: str | None = None) -> dict:
    if holder not in {'agent','user'}:
        raise ShotError('INVALID_HOLDER','Holder must be agent or user',400)
    project = project_path(project)
    lease = _lease(project,shot_id)
    if not lease or _expired(lease):
        raise ShotError('LOCK_REQUIRED','Acquire or explicitly steal the expired shot lease first',409)
    if lease['holder'] != holder or not lease_id or lease_id != lease.get('lease_id'):
        raise ShotError('LOCK_HELD','Shot is held by another editor or stale lease',409)
    return lease


def _changes(before, after, path: str = '') -> list[dict]:
    if isinstance(before,dict) and isinstance(after,dict):
        result=[]
        for key in sorted(set(before)|set(after)):
            if key in {'revision','updated_at','updated_by'}: continue
            pointer=path+'/'+key.replace('~','~0').replace('/','~1')
            if key not in before:
                result.append({'path':pointer,'before':None,'after':after[key]})
            elif key not in after:
                result.append({'path':pointer,'before':before[key],'after':None})
            else:
                result.extend(_changes(before[key],after[key],pointer))
        return result
    if before == after: return []
    return [{'path':path or '/', 'before':before,'after':after}]


def _audit(project: Path, shot_id: str, holder: str, action: str, reason: str,
           changes: list[dict], extra: dict | None = None) -> None:
    from schemas.artifacts import validate_artifact
    with transaction(project):
        path=safe_path(project,'artifacts/decision_log.json')
        log=read_json(path,{'version':'1.0','project_id':project.name,'decisions':[]})
        validate_artifact('decision_log',log)
        option=f'{holder}:{action}'
        log['decisions'].append({'decision_id':f'shot-{uuid.uuid4().hex}', 'stage':'assets',
            'category':'shot_handoff','subject':f'Shot handoff: {shot_id}',
            'options_considered':[{'option_id':option,'label':option,'score':1,'reason':reason or action}],
            'selected':option,'reason':reason or action,'timestamp':_now(),'shot_id':shot_id,
            'holder':holder,'changed_fields':[c['path'] for c in changes],
            'handoff':{'action':action,'changes':changes,**(extra or {})},'user_visible':True})
        validate_artifact('decision_log',log)
        atomic_json(path,log)


def acquire(project: str | Path, shot_id: str, holder: str, reason: str = '', steal: bool = False) -> dict:
    if holder not in {'agent','user'}:
        raise ShotError('INVALID_HOLDER','Holder must be agent or user',400)
    if not isinstance(reason, str):
        raise ShotError('INVALID_REASON', 'Handoff reason must be a string')
    if len(reason)>2000: raise ShotError('INVALID_REASON','Handoff reason exceeds 2000 characters')
    if steal and not reason.strip():
        raise ShotError('STEAL_REASON_REQUIRED','A forced handoff needs a reason',400)
    project=project_path(project); identifier(shot_id)
    with transaction(project,f'shot-{shot_id}'):
        spec=initialize(project,shot_id)
        previous=_lease(project,shot_id)
        if previous and not steal:
            # Another browser/agent must retain the lease capability obtained
            # from the first handoff; re-open cannot reset its base revision.
            if previous['holder']==holder and not _expired(previous):
                return previous
            raise ShotError('LOCK_HELD','Shot has a lease; use explicit steal with a reason',409)
        if steal:
            directory = safe_path(project, f'shots/{shot_id}.requests')
            requests = [read_json(safe_path(project, path.relative_to(project)))
                        for path in directory.glob('*.json')]
            if any(isinstance(r,dict) and r.get('status') in {'running','unknown'} for r in requests):
                raise ShotError('RENDER_IN_FLIGHT','Resolve the in-flight render before forcing a handoff',409)
        lease={'holder':holder,'since':_now(),'updated_at':_now(),'reason':reason,
               'base_revision':spec['revision'],'lease_id':uuid.uuid4().hex,'base_spec':copy.deepcopy(spec)}
        _audit(project,shot_id,holder,'steal' if steal else 'acquire',reason,[],
               {'previous_holder':previous['holder'] if previous else None,'base_revision':spec['revision']})
        atomic_json(safe_path(project,f'shots/{shot_id}.lock'),lease)
        return lease


def heartbeat(project: str | Path, shot_id: str, holder: str, lease_id: str | None = None) -> dict:
    project=project_path(project)
    with transaction(project,f'shot-{identifier(shot_id)}'):
        lease=require_lock(project,shot_id,holder,lease_id)
        lease['updated_at']=_now()
        atomic_json(safe_path(project,f'shots/{shot_id}.lock'),lease)
        return lease


def release(project: str | Path, shot_id: str, holder: str = 'user', lease_id: str | None = None) -> dict:
    project=project_path(project)
    with transaction(project,f'shot-{identifier(shot_id)}'):
        lease=require_lock(project,shot_id,holder,lease_id)
        spec=load(project,shot_id)
        changes=_changes(lease.get('base_spec',{}),spec)
        result={'shot_id':shot_id,'base_revision':lease['base_revision'],'revision':spec['revision'],
                'holder':holder,'changes':changes}
        _audit(project,shot_id,holder,'release',lease.get('reason',''),changes,
               {'base_revision':lease['base_revision'],'revision':spec['revision']})
        safe_path(project,f'shots/{shot_id}.lock').unlink()
        return result


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['open','status','release','steal','acquire','heartbeat','edit','compile','dry-run'])
    parser.add_argument('project');parser.add_argument('shot_id')
    parser.add_argument('--holder',choices=['agent','user'])
    parser.add_argument('--reason',default='');parser.add_argument('--lease-id')
    parser.add_argument('--revision',type=int);parser.add_argument('--tool')
    parser.add_argument('--file',type=Path)
    args=parser.parse_args(argv)
    try:
        project=project_path(args.project)
        holder=args.holder or ('user' if args.command in {'open','release'} else 'agent')
        if args.command in {'open','acquire','steal'}:
            result=acquire(project,args.shot_id,holder,args.reason,steal=args.command=='steal')
            if args.command=='open':
                from backlot.__main__ import _port, _server_alive, _spawn_server
                import time
                port=_port()
                if not _server_alive(port):
                    _spawn_server(port)
                    deadline=time.monotonic()+15
                    while time.monotonic()<deadline and not _server_alive(port): time.sleep(.2)
                    if not _server_alive(port): raise ShotError('SERVER_UNAVAILABLE','Backlot could not start',503)
                result={'url':f'http://127.0.0.1:{port}/w/{project.name}/{args.shot_id}',
                        'lock':{k:v for k,v in result.items() if k!='base_spec'}}
        elif args.command=='status':
            result=status(project,args.shot_id,holder)
        elif args.command in {'release','heartbeat','edit'}:
            lease_id=args.lease_id or (status(project,args.shot_id,holder)['lock'] or {}).get('lease_id')
            if args.command=='release': result=release(project,args.shot_id,holder,lease_id)
            elif args.command=='heartbeat': result=heartbeat(project,args.shot_id,holder,lease_id)
            else:
                raw=args.file.read_text() if args.file else sys.stdin.read()
                data=json.loads(raw)
                result=save(project,data,args.revision,holder,lease_id)
        else:
            from lib.shot_execution import preview
            spec=load(project,args.shot_id)
            result=preview(spec,args.tool,project=project)
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        return 0
    except (ShotError,ValueError,OSError) as exc:
        print(json.dumps({'error':getattr(exc,'code','INVALID_INPUT'),'message':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
