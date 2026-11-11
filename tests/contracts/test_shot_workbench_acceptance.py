"""User-requested Fati scene regressions with synthetic identities and PNGs.

Scene descriptions remain historical acceptance fixtures; every artist asset
is template-derived alpha/beta data in the temporary project, not a real profile.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.shot_compiler import compile as compile_shot
from lib.shot_spec import from_scene, load, save
from lib.shot_workbench import acquire, initialize, release
from tests.fixtures.artist_factory import write_library

FIXTURE = Path(__file__).resolve().parents[1]/'fixtures/shot_workbench/fati_scenes.json'
CONTEXT = {'capability':'video_generation','estimated_cost':0.1,
    'tool_options':{'model':'offline-fati'},'limits':{'max_reference_images':9,
    'negative_prompt_field':'negative_prompt','durations':{'minimum':.1,'maximum':15},
    'routes':['t2v','i2v','multi_ref','first_last'],'first_last_frame':True,'control_inputs':[]}}


@pytest.fixture
def film(tmp_path, monkeypatch):
    from lib import artist_profile
    monkeypatch.setattr(artist_profile, 'ARTISTS_DIR', tmp_path/'unused-global-catalog')
    film=tmp_path/'fati-snapshot';(film/'artifacts').mkdir(parents=True)
    (film/'project.json').write_text(json.dumps({'pipeline_type':'music-video'}))
    plan = json.loads(FIXTURE.read_text())
    for scene in plan['scenes']:
        for action in scene.get('character_actions', []):
            if action['character_id'] == 'fati':
                action['character_id'] = 'beta'
    (film/'artifacts/scene_plan.json').write_text(json.dumps(plan))
    write_library(film/'assets/artists', pending_alpha=True, provider_bound=True)
    return film


def codes(result):
    return {item['code'] for item in result['lint']}


def test_fati_empty_drawer_edit_lint_and_handoff(film):
    shot='sh-s01-01';initialize(film,shot)
    lock=acquire(film,shot,'agent',reason='offline acceptance')
    draft=load(film,shot)
    draft['movement']={'type':'push_in','intensity':1,'cause':''}
    draft['notes']='no people; drawer opens automatically'
    failed=compile_shot(draft,'offline-fati',CONTEXT)
    assert {'POSITIVE_NEGATION','MISSING_MOVEMENT_CAUSE','MOTION_WITHOUT_PERSON'} <= codes(failed)
    assert not failed['generatable']
    draft['movement']={'type':'static','intensity':0}
    draft['notes']='The drawer remains fully shut in an empty unoccupied room.'
    draft['exclusions']=['people','drawer opens automatically']
    saved=save(film,draft,holder='agent',lease_id=lock['lease_id'])
    valid=compile_shot(saved,'offline-fati',CONTEXT)
    assert valid['generatable']
    assert 'drawer opens automatically' in valid['negative_prompt']
    assert 'drawer opens automatically' not in valid['prompt']
    handoff=release(film,shot,'agent',lock['lease_id'])
    assert handoff['revision']==1 and any(c['path']=='/notes' for c in handoff['changes'])


def test_fati_sewing_closeup_warns_and_crops_face(film):
    scene=next(s for s in json.loads((film/'artifacts/scene_plan.json').read_text())['scenes'] if s['id']=='sh-s05-02')
    draft=from_scene(scene,film)
    result=compile_shot(draft,'offline-fati',CONTEXT)
    assert 'FINE_HAND_ACTION' in codes(result)
    # Reproduce the Fati wrist insert failure using the approved face snapshot.
    singing=next(s for s in json.loads((film/'artifacts/scene_plan.json').read_text())['scenes'] if s['id']=='sh-s06-04')
    identity=from_scene(singing,film)['identity_context']
    draft['subjects'][0]['ref_id']='beta';draft['subjects'][0]['kind']='artist'
    draft['identity_context']=identity
    cropped=compile_shot(draft,'offline-fati',CONTEXT)
    assert 'FINE_HAND_ACTION' in codes(cropped)
    assert 'neutral features' not in cropped['prompt'].lower()
    assert 'test wristband' in cropped['prompt'].lower()


def test_fati_singing_uses_only_current_canon_and_stable_roles(film):
    initialize(film,'sh-s06-04')
    draft=load(film,'sh-s06-04')
    assert draft['subjects'][0]['ref_id']=='beta'
    identities=[r for r in draft['references'] if r['role']=='identity']
    assert len(identities)==1
    assert identities[0]['parent_asset'].endswith('beta_main_front.png')
    assert all('_candidate' not in r.get('parent_asset','') for r in draft['references'])
    draft['references'] += [{'asset_path':f'assets/{role}.png','sha256':'a'*64,'role':role}
                            for role in ['style','environment','first_frame']]
    draft['route']='multi_ref'
    cfg=copy.deepcopy(CONTEXT);cfg['limits']['max_reference_images']=2
    result=compile_shot(draft,'offline-fati',cfg)
    assert [r['role'] for r in result['payload']['references']]==['identity','first_frame']
    assert 'REFERENCES_TRUNCATED' in codes(result)
    assert 'beta' in result['payload']['reference_map']
    assert 'neutral features' in result['prompt'].lower() and 'synthetic short hair' in result['prompt'].lower()
    assert result['generatable'],result['lint']
    assert result['compiled_hash']==compile_shot(draft,'offline-fati',cfg)['compiled_hash']


def test_synthetic_pending_candidates_are_not_promoted_to_canon(film):
    scene={'id':'duet','type':'generated','description':'Two synthetic fixture performers on a rooftop',
        'start_seconds':0,'end_seconds':5,'render_route':'cast_multi_ref',
        'generation_context':{'artist_ids':['alpha','beta']}}
    draft=from_scene(scene,film)
    assert any('alpha' in text and 'canonical' in text for text in draft['initialization_warnings'])
    assert not any(ref.get('subject_ref')=='alpha' for ref in draft['references'])
    assert any(ref.get('subject_ref')=='beta' for ref in draft['references'])
