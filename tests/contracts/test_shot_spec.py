"""File-first shot contracts: revisions, leases and headless handoff."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from schemas.artifacts import validate_artifact
from lib.shot_spec import ShotError, from_scene, load, save, safe_path
from lib.shot_workbench import acquire, initialize, release, status, heartbeat
from tests.fixtures.artist_factory import write_library


@pytest.fixture
def project(tmp_path, monkeypatch):
    from lib import artist_profile
    monkeypatch.setattr(artist_profile, 'ARTISTS_DIR', tmp_path/'unused-global-catalog')
    p = tmp_path / 'film'
    (p / 'artifacts').mkdir(parents=True)
    (p / 'project.json').write_text(json.dumps({'pipeline_type': 'cinematic'}))
    write_library(p / 'assets/artists', pending_alpha=True, provider_bound=True)
    scenes = [{'id': 's1', 'type': 'generated', 'description': '旧抽屉与针线',
               'start_seconds': 0, 'end_seconds': 5,
               'shot_language': {'shot_size': 'insert', 'camera_movement': 'static', 'lens_mm': 85}},
              {'id': 's2', 'type': 'generated', 'description': '两人走过街角',
               'start_seconds': 5, 'end_seconds': 10, 'render_route': 'cast_multi_ref',
               'generation_context': {'artist_ids': ['beta', 'alpha']}}]
    (p/'artifacts/scene_plan.json').write_text(json.dumps({'version': '1.0', 'scenes': scenes}))
    return p


def test_from_scene_and_optional_schema(project):
    scene = json.loads((project/'artifacts/scene_plan.json').read_text())['scenes'][0]
    spec = from_scene(scene, project)
    validate_artifact('shot_spec', spec)
    assert spec['scene_id'] == 's1'
    assert spec['duration_seconds'] == 5
    assert spec['framing']['shot_size'] == 'insert'
    assert spec['subjects'] == []
    assert spec['revision'] == 0


@pytest.mark.parametrize('route,expected', [('story_i2v','i2v'), ('performance_lipsync','lipsync'),
                                          ('continuation','i2v'), ('cast_multi_ref','multi_ref')])
def test_automv_routes(project, route, expected):
    spec = from_scene({'id':'s1', 'type':'generated', 'description':'街角',
                       'start_seconds':0,'end_seconds':5,'render_route':route}, project)
    assert spec['route'] == expected


def test_revision_conflict_and_holder_guard(project):
    initialize(project, 's1')
    lock = acquire(project, 's1', 'agent', reason='implement scene')
    spec = load(project, 's1')
    updated = save(project, {**spec, 'notes': '窗光照到针身'}, holder='agent', lease_id=lock['lease_id'])
    assert updated['revision'] == 1
    with pytest.raises(ShotError) as err:
        save(project, spec, holder='agent', lease_id=lock['lease_id'])
    assert err.value.status_code == 409
    with pytest.raises(ShotError):
        save(project, updated, holder='user')


def test_release_diff_and_append_only_decision_log(project):
    initialize(project, 's1')
    lock = acquire(project, 's1', 'user', reason='摆机位')
    spec = load(project, 's1')
    save(project, {**spec, 'notes': '安静的光'}, holder='user', lease_id=lock['lease_id'])
    result = release(project, 's1', 'user', lease_id=lock['lease_id'])
    assert any(c['path'] == '/notes' and c['after'] == '安静的光' for c in result['changes'])
    assert status(project, 's1')['lock'] is None
    log = json.loads((project/'artifacts/decision_log.json').read_text())
    validate_artifact('decision_log', log)
    assert len(log['decisions']) >= 2
    assert log['decisions'][-1]['category'] == 'shot_handoff'


def test_expiry_and_steal_require_reason(project):
    initialize(project, 's1')
    lock = acquire(project, 's1', 'user', reason='workbench')
    path = project/'shots/s1.lock'
    raw = json.loads(path.read_text())
    raw['updated_at'] = '2000-01-01T00:00:00+00:00'
    path.write_text(json.dumps(raw))
    assert status(project, 's1')['expired']
    with pytest.raises(ShotError):
        acquire(project, 's1', 'agent', steal=True)
    next_lock = acquire(project, 's1', 'agent', reason='stale recovery', steal=True)
    with pytest.raises(ShotError):
        release(project, 's1', 'user', lease_id=lock['lease_id'])
    assert next_lock['holder'] == 'agent'


def test_heartbeat_keeps_same_lease(project):
    initialize(project, 's1')
    lock = acquire(project, 's1', 'agent', reason='editing')
    refreshed = heartbeat(project, 's1', 'agent', lease_id=lock['lease_id'])
    assert refreshed['lease_id'] == lock['lease_id']


@pytest.mark.parametrize('path', ['../outside.png', '/etc/passwd', 'assets/../../outside', 'assets\\outside.png'])
def test_paths_cannot_escape(project, path):
    with pytest.raises(ShotError):
        safe_path(project, path)


def test_symlink_and_corrupt_file_preserved(project, tmp_path):
    (project/'assets').rename(project/'safe-assets')
    (project/'assets').symlink_to(tmp_path/'outside')
    with pytest.raises(ShotError):
        safe_path(project, 'assets/img.png')
    (project/'shots').mkdir()
    (project/'shots/s1.json').write_text('{broken')
    with pytest.raises(ShotError):
        initialize(project, 's1')
    assert (project/'shots/s1.json').read_text() == '{broken'


def test_two_processes_cannot_take_same_shot(project):
    initialize(project, 's1')
    script = '''from pathlib import Path
from lib.shot_workbench import acquire
from lib.shot_spec import ShotError
import sys
try:
 acquire(Path(sys.argv[1]), 's1', sys.argv[2], reason='race')
 print('won')
except ShotError:
 print('lost')
'''
    processes = [subprocess.Popen([sys.executable, '-c', script, str(project), actor],
                                  stdout=subprocess.PIPE, text=True) for actor in ('user', 'agent')]
    answers = [p.communicate(timeout=30)[0].strip() for p in processes]
    assert sorted(answers) == ['lost', 'won']


@pytest.mark.parametrize('state', ['running','unknown'])
def test_steal_cannot_interrupt_persisted_render(project, state):
    initialize(project,'s1')
    acquire(project,'s1','user',reason='editing')
    directory=project/'shots/s1.requests'
    directory.mkdir()
    (directory/'request1.json').write_text(json.dumps({'shot_id':'s1','status':state}))
    with pytest.raises(ShotError,match='in-flight'):
        acquire(project,'s1','agent',reason='force handoff',steal=True)


def test_maximum_length_shot_id_can_initialize(project):
    shot_id='s' * 128
    path=project/'artifacts/scene_plan.json'
    plan=json.loads(path.read_text())
    plan['scenes'][0]['id']=shot_id
    path.write_text(json.dumps(plan))
    assert initialize(project,shot_id)['shot_id']==shot_id


def test_cli_edit_compile_and_dry_run_without_browser(project, monkeypatch, capsys):
    from lib.shot_workbench import main
    import lib.shot_execution as execution
    import lib.shot_spec as shot_spec
    monkeypatch.setattr(shot_spec,'PROJECTS_DIR',project.parent)
    lock=acquire(project,'s1','agent',reason='headless')
    data=load(project,'s1')
    data['notes']='A warm window highlights the closed drawer.'
    file=project/'edit.json';file.write_text(json.dumps(data))
    assert main(['edit',project.name,'s1','--file',str(file),'--revision','0','--lease-id',lock['lease_id']])==0
    assert json.loads(capsys.readouterr().out)['revision']==1
    def preview(spec,tool_name=None,project=None):
        assert project.name=='film' and spec['revision']==1
        return {'compiled_hash':'stable','estimated_cost':0,'dry_run':True,'tool_name':tool_name}
    monkeypatch.setattr(execution,'preview',preview)
    for command in ['compile','dry-run']:
        assert main([command,project.name,'s1','--tool','mock-tool'])==0
        assert json.loads(capsys.readouterr().out)['compiled_hash']=='stable'
    assert main(['release',project.name,'s1','--holder','agent','--lease-id',lock['lease_id']])==0
    assert json.loads(capsys.readouterr().out)['changes'][0]['path']=='/notes'


def test_draft_validation_rejects_invalid_stage_and_duplicate_subjects(project):
    from lib.shot_spec import validate
    data=initialize(project,'s1')
    subject={'kind':'character','ref_id':'person','visible_parts':['face']}
    with pytest.raises(ShotError,match='Duplicate subject'):
        validate({**data,'subjects':[subject,subject]})
    camera={'position':[0,1,5],'target':[0,1,0],'lens_mm':35}
    object={'id':'a','kind':'human','position':[0,0,0],'subject_ref':'person'}
    stage={'version':'1.0','units':'m','camera':camera,'objects':[object]}
    validate({**data,'subjects':[subject],'stage':stage})
    for invalid in [
        {**stage,'objects':[object,object]},
        {**stage,'camera':{**camera,'target':camera['position']}},
        {**stage,'camera':{**camera,'near':2,'far':1}},
        {**stage,'objects':[{**object,'subject_ref':'missing'}]},
        {**stage,'objects':[{**object,'position':[float('nan'),0,0]}]},
    ]:
        with pytest.raises(ShotError):
            validate({**data,'subjects':[subject],'stage':invalid})


def test_legacy_checkpoint_artifact_and_character_snapshot(project):
    from lib.shot_spec import _artifact
    (project/'artifacts/scene_plan.json').unlink()
    scene={'id':'legacy','description':'A character at a desk','start_seconds':0,'end_seconds':3,
           'character_actions':[{'character_id':'tailor','action_sequence':['folds linen']}]}
    (project/'checkpoint_scene_plan.json').write_text(json.dumps({'artifacts':{'scene_plan':{'scenes':[scene]}}}))
    (project/'artifacts/character_design.json').write_text(json.dumps({'characters':[{
        'id':'tailor','display_name':'Tailor','identity_prompt':'Adult with a round face',
        'wardrobe':'linen apron','locked_traits':['dark eyes']}]}))
    spec=initialize(project,'legacy')
    assert spec['subjects'][0]['action']=='folds linen'
    assert spec['identity_context']['tailor']['wardrobe']=='linen apron'
    checkpoint=project/'checkpoint_scene_plan.json'
    (project/'artifacts/legacy.json').write_text(json.dumps({'scenes':[scene]}))
    checkpoint.write_text(json.dumps({'artifacts':{'scene_plan':'artifacts/legacy.json'}}))
    assert _artifact(project,'scene_plan')['scenes'][0]['id']=='legacy'
    checkpoint.write_text(json.dumps({'artifacts':{'scene_plan':'/tmp/outside-artifact.json'}}))
    with pytest.raises(ShotError,match='escapes'):
        _artifact(project,'scene_plan')


def test_invalid_duration_corrupt_lock_and_immutable_scene(project):
    with pytest.raises(ShotError,match='positive duration'):
        from_scene({'id':'bad','start_seconds':3,'end_seconds':3},project)
    spec=initialize(project,'s1')
    lock=acquire(project,'s1','agent','editing')
    with pytest.raises(ShotError,match='scene id'):
        save(project,{**spec,'scene_id':'s2'},holder='agent',lease_id=lock['lease_id'])
    with pytest.raises(ShotError):
        heartbeat(project,'s1','invalid',lock['lease_id'])
    (project/'shots/s1.lock').write_text(json.dumps({'holder':'invalid'}))
    with pytest.raises(ShotError,match='Unreadable'):
        status(project,'s1')


def test_cli_open_status_heartbeat_and_errors(project,monkeypatch,capsys):
    from lib.shot_workbench import main
    import lib.shot_spec as shot_spec
    import backlot.__main__ as backlot_cli
    monkeypatch.setattr(shot_spec,'PROJECTS_DIR',project.parent)
    monkeypatch.setattr(backlot_cli,'_server_alive',lambda port:True)
    monkeypatch.setattr(backlot_cli,'_port',lambda:4750)
    assert main(['open',project.name,'s1','--reason','camera placement'])==0
    output=json.loads(capsys.readouterr().out)
    assert output['url']=='http://127.0.0.1:4750/w/film/s1'
    assert 'base_spec' not in output['lock']
    lease=output['lock']['lease_id']
    assert main(['heartbeat',project.name,'s1','--holder','user','--lease-id',lease])==0
    assert json.loads(capsys.readouterr().out)['lease_id']==lease
    assert main(['status',project.name,'s1','--holder','user'])==0
    assert not json.loads(capsys.readouterr().out)['read_only']
    assert main(['steal',project.name,'s1'])==1
    assert json.loads(capsys.readouterr().err)['error']=='STEAL_REASON_REQUIRED'
    assert main(['status','../outside','s1'])==1
    assert json.loads(capsys.readouterr().err)['error']=='UNSAFE_ID'


def test_atomic_write_failure_preserves_original_and_cleans_temp(project,monkeypatch):
    from lib.shot_spec import atomic_json,read_json
    path=project/'unchanged.json';atomic_json(path,{'revision':1})
    original=Path.replace
    def fail(source,destination):
        if destination==path: raise OSError('simulated replace failure')
        return original(source,destination)
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(OSError): atomic_json(path,{'revision':2})
    assert read_json(path)=={'revision':1}
    assert not list(project.glob('*.tmp'))


@pytest.mark.parametrize('reason',[None,42,{}])
def test_invalid_handoff_reason_is_structured_error(project,reason):
    with pytest.raises(ShotError,match='reason'):
        acquire(project,'s1','user',reason=reason)


def test_checkpoint_symlink_cannot_initialize_from_outside(project,tmp_path):
    from lib.shot_spec import _artifact
    (project/'artifacts/scene_plan.json').unlink()
    outside=tmp_path/'outside-checkpoint.json'
    outside.write_text(json.dumps({'artifacts':{'scene_plan':{'scenes':[{'id':'outside'}]}}}))
    (project/'checkpoint_scene_plan.json').symlink_to(outside)
    with pytest.raises(ShotError,match='escapes') as error:
        _artifact(project,'scene_plan')
    assert error.value.status_code==403
    assert json.loads(outside.read_text())['artifacts']['scene_plan']['scenes'][0]['id']=='outside'


@pytest.mark.parametrize('checkpoint',[[],{'artifacts':[]},{'artifacts':{'scene_plan':'artifacts/bad.json'}}])
def test_invalid_checkpoint_shape_fails_without_overwriting(project,checkpoint):
    from lib.shot_spec import _artifact
    (project/'artifacts/scene_plan.json').unlink()
    path=project/'checkpoint_scene_plan.json';path.write_text(json.dumps(checkpoint))
    (project/'artifacts/bad.json').write_text('[]')
    with pytest.raises(ShotError,match='object'):
        _artifact(project,'scene_plan')
    assert json.loads(path.read_text())==checkpoint
