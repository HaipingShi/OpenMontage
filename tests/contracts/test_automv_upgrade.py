"""AutoMV upgrade contracts: all model/inference calls are mocked, no network."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.frames import extract_last_frame, file_sha256
from lib.mv_routes import plan_render_routes, verified_continuation_frame
from lib.song_audio import vocal_slice
from lib.timeline import build_segments, quantize, validate_timeline
from schemas.artifacts import validate_artifact
from tools.analysis.mv_judge import CRITERIA, MvJudge, aggregate_scores
from tools.analysis.shot_verifier import ShotVerifier, candidate_budget, select_candidate
from tools.analysis.song_beatmap import SongBeatmap, build_song_map
from tools.analysis.stem_separator import StemSeparator
from tools.base_tool import BaseTool, ToolResult, ToolStatus


def audiomap(duration=30):
    return {'audio': {'duration_sec': duration}, 'tempo': {'bpm': 120, 'beats_per_bar': 4},
            'grid': {'beats_sec': [i/2 for i in range(int(duration*2))],
                     'downbeats_sec': list(range(0, int(duration), 2))},
            'energy_phases': [{'start': 0, 'end': duration, 'level': 'MEDIUM', 'energy': 0.5}],
            'phrases': [{'index': i, 'start': t, 'end': min(duration, t+8), 'bars': 4}
                        for i, t in enumerate(range(0, int(duration), 8))],
            'stats': {'grid_counts': {'strong': 80, 'weak': 10, 'off-grid': 1}}}


def registry_for(tool=None):
    return SimpleNamespace(ensure_discovered=lambda: None,
                           get_by_capability=lambda capability: [tool] if tool else [])


class MockReview(BaseTool):
    name = 'mock_review'
    capability = 'multimodal_review'
    provider = 'mock'
    supports = {'video_input': True, 'audio_input': True}
    input_schema = {'properties': {'model': {'type': 'string'}}}

    def __init__(self, physical=True):
        self.calls = []
        self.physical = physical

    def execute(self, inputs):
        self.calls.append(inputs)
        if inputs['operation'] == 'physical':
            content = {'pass': self.physical, 'reasons': ['mock physical review']}
        elif inputs['operation'] == 'score':
            content = {'script_lyrics': 4, 'identity': {'amor': 5, 'fati': 2}, 'locked_traits': 4, 'singing': 4}
        else:
            content = {key: 4 for keys in CRITERIA.values() for key in keys}
        return ToolResult(success=True, data={'review': content}, model='mock-vision')


@pytest.fixture
def candidate(tmp_path):
    path = tmp_path/'clip.mp4'; path.write_bytes(b'candidate video')
    refs = {key: [str(tmp_path/f'{key}.png')] for key in ('amor', 'fati')}
    for paths in refs.values():
        Path(paths[0]).write_bytes(b'reference')
    return {'input_path': str(path), 'shot': {'id': 'shot1', 'start_seconds': 0, 'end_seconds': 4,
        'render_route': 'cast_multi_ref', 'generation_context': {'artist_ids': ['amor','fati']}},
        'reference_map': refs, 'locked_traits': {'amor':['short hair'], 'fati':['no nose ring']},
        'generation_log': {'reference_map': refs, 'submitted_reference_images': [p for paths in refs.values() for p in paths]},
        'person_count_evidence': {'source_sha256': file_sha256(path), 'producer': 'mock local detector', 'counts': [2,2,2]},
        'sample_dir': str(tmp_path/'frames')}


def verifier(tool, monkeypatch):
    instance = ShotVerifier(registry_for(tool))
    monkeypatch.setattr(instance, '_probe', lambda path: {'duration': 5, 'fps': 24})
    monkeypatch.setattr('tools.analysis.shot_verifier.FrameSampler.execute', lambda self, inputs:
                        ToolResult(success=True, data={'frames':[{'path':'mock-frame.png'}]}))
    return instance


def test_demucs_missing_degrades(tmp_path, monkeypatch):
    master = tmp_path/'master.wav'; master.write_bytes(b'master')
    monkeypatch.setattr('tools.analysis.stem_separator.importlib.util.find_spec', lambda name: None)
    tool = StemSeparator()
    assert tool.get_status() == ToolStatus.UNAVAILABLE
    result = tool.execute({'input_path':str(master),'output_dir':str(tmp_path/'stems')})
    assert not result.success and result.data['fallback'] == 'master'
    assert master.read_bytes() == b'master'


def test_stem_paths_sample_rate_content_cache(tmp_path, monkeypatch):
    master = tmp_path/'master.wav'; master.write_bytes(b'master-v1')
    tool = StemSeparator()
    monkeypatch.setattr(tool, 'get_status', lambda: ToolStatus.AVAILABLE)
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == 'ffprobe':
            return SimpleNamespace(stdout=json.dumps({'streams':[{'sample_rate':'48000','channels':2}]}))
        if '-m' in cmd:
            out = Path(cmd[cmd.index('-o')+1])/'htdemucs'/master.stem
            out.mkdir(parents=True)
            for name in ['vocals.wav','no_vocals.wav']:
                (out/name).write_bytes(b'x'*100)
        else:
            Path(cmd[-1]).write_bytes(b'x'*100)
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(tool, 'run_command', run)
    params = {'input_path':str(master),'output_dir':str(tmp_path/'stems')}
    result = tool.execute(params)
    assert result.success, result.error
    assert result.data['sample_rate'] == 48000
    assert Path(result.data['vocals']).name == 'vocals.wav'
    assert Path(result.data['accompaniment']).name == 'accompaniment.wav'
    assert all(cmd[cmd.index('-ar')+1]=='48000' for cmd in calls if '-ar' in cmd)
    before = len(calls)
    same_bytes = tmp_path/'renamed.wav'; same_bytes.write_bytes(master.read_bytes())
    hit = tool.execute({**params, 'input_path': str(same_bytes)})
    assert hit.success and hit.data['cache_hit'] and len(calls)==before
    master.write_bytes(b'master-v2')
    assert tool.execute(params).data['cache_key'] != result.data['cache_key']


def test_lyric_precedence_confidence_singers():
    transcript = {'word_timestamps': [{'word':'hold','start':0,'end':1}, {'word':'on','start':1,'end':2},
        {'word':'walk','start':3,'end':4}]}
    song = build_song_map(audiomap(), audio_path='master.wav', transcript=transcript,
        suno_lyrics_text='[Verse 1: Amor]\nHold on\n[Verse 2: Fati]\nwalk away from here', lyrics_text='ignored')
    validate_artifact('song_map', song)
    assert song['lyrics']['source']=='suno_record'
    lines = song['lyrics']['lines']
    assert [line['singer'] for line in lines]==['Amor','Fati']
    assert lines[0]['section_tag']=='[Verse 1: Amor]'
    assert lines[0]['confidence']==1
    assert lines[1]['confidence']==0.25
    assert song['review_flags'][0]['line_id']==lines[1]['id']
    assert build_song_map(audiomap(), audio_path='a', lyrics_text='user')['lyrics']['source']=='user_supplied'
    transcription = build_song_map(audiomap(), audio_path='a', transcript={'segments':[{'text':'hi','start':0,'end':1}]})
    assert transcription['lyrics']['source']=='transcribed'
    assert transcription['lyrics']['lines'][0]['confidence']==0


def test_lyric_threshold_customization_and_old_schemas():
    song = build_song_map(audiomap(), audio_path='a', lyrics_text='a b c d',
        transcript={'word_timestamps':[{'word':'a b','start':0,'end':1}]}, lyrics_confidence_threshold=0.4)
    assert song['lyrics']['lines'][0]['confidence']==0.5 and not song['review_flags']
    old = copy.deepcopy(song)
    old.pop('review_flags'); old['lyrics'].pop('confidence_threshold')
    for line in old['lyrics']['lines']:
        for key in ['confidence','singer','section_tag']: line.pop(key,None)
    validate_artifact('song_map', old)
    from tests.contracts.test_phase0_contracts import sample_artifact
    for kind in ['scene_plan','asset_manifest']:
        validate_artifact(kind,sample_artifact(kind))
    validate_artifact('final_review', {'version':'1.0','output_path':'legacy.mp4','status':'pass',
        'checks': {key:{} for key in ['technical_probe','visual_spotcheck','audio_spotcheck','promise_preservation','subtitle_check']}})


def test_segments_prioritize_sections_and_lyrics():
    song = build_song_map(audiomap(), audio_path='a')
    song['sections'] = [{'id':'a','start_seconds':0,'end_seconds':10},{'id':'b','start_seconds':10,'end_seconds':30}]
    song['lyrics']['lines']=[{'id':'line1','start_seconds':0,'end_seconds':10}, {'id':'line2','start_seconds':10,'end_seconds':20}]
    segments=build_segments(song)
    assert [(s['start_seconds'],s['end_seconds']) for s in segments]==[(0,10),(10,20),(20,30)]
    assert all(3<=s['end_seconds']-s['start_seconds']<=15 for s in segments)
    assert validate_timeline({'scenes':segments},song,24)['total_frames']==720
    song['sections'][0]['end_seconds']=2; song['sections'][1]['start_seconds']=2
    assert build_segments(song)[0]['constraint_exception']=='section_shorter_than_min_s'


@pytest.mark.parametrize('kind', ['gap','overlap','off_grid','wrong_total','zero'])
def test_timeline_invalid(kind):
    song = {'audio':{'duration_seconds':30}}
    scenes = [{'id':'a','start_seconds':0,'end_seconds':15},{'id':'b','start_seconds':15,'end_seconds':30}]
    if kind=='gap': scenes[1]['start_seconds']=16
    elif kind=='overlap': scenes[1]['start_seconds']=14
    elif kind=='off_grid': scenes[1]['start_seconds']=15.01
    elif kind=='wrong_total': scenes[1]['end_seconds']=29
    else: scenes[1]['end_seconds']=15
    with pytest.raises(ValueError): validate_timeline({'scenes':scenes},song,24)
    assert quantize(0.1,24)==pytest.approx(2/24)


def test_route_split_performance_and_cast():
    song = {'sections':[{'start_seconds':0}], 'lyrics':{'lines':[]}}
    scenes=[{'id':'a','segment_id':'seg1','type':'generated','description':'performance','start_seconds':0,'end_seconds':15,
             'generation_context':{'artist_ids':['amor'], 'performance':True}},
            {'id':'b','segment_id':'seg2','type':'generated','description':'two singers','start_seconds':15,'end_seconds':30,
             'generation_context':{'artist_ids':['amor','fati']}}]
    result=plan_render_routes({'version':'1.0','scenes':scenes},song,max_clip_s=8)
    validate_artifact('scene_plan',result)
    assert result['scenes'][0]['render_route']=='performance_lipsync'
    assert result['scenes'][1]['render_route']=='continuation'
    assert result['scenes'][1]['generation_context']['lip_sync']
    assert result['scenes'][1]['generation_context']['continuation_of']==result['scenes'][0]['id']
    assert result['scenes'][3]['render_route']=='cast_multi_ref'
    assert all(s['generation_context']['generation_duration_seconds']<=8 for s in result['scenes'])
    assert validate_timeline(result,{'audio':{'duration_seconds':30}},24)['valid']


def test_reference_missing_short_circuits(candidate, monkeypatch):
    tool=MockReview(); instance=verifier(tool, monkeypatch)
    candidate['generation_log']['submitted_reference_images']=[]
    result=instance.execute(candidate)
    assert result.success and result.data['verification']['status']=='fail'
    assert not tool.calls
    assert 'physical' not in result.data['verification']['layers']


def test_physical_failure_short_circuits_score(candidate,monkeypatch):
    tool=MockReview(physical=False)
    result=verifier(tool,monkeypatch).execute(candidate)
    assert [call['operation'] for call in tool.calls]==['physical']
    assert not result.data['verification']['eligible']
    assert 'scoring' not in result.data['verification']['layers']
    assert select_candidate([{'path':'a','verification':result.data['verification']}])['action']=='revise_shot'


def test_per_artist_min_and_best_selection(candidate,monkeypatch):
    result=verifier(MockReview(),monkeypatch).execute(candidate)
    verification=result.data['verification']
    assert verification['layers']['scoring']['dimension_scores']['identity']==2
    assert verification['score']==pytest.approx(10/3)
    assert select_candidate([{'verification':verification},{'verification':{**verification,'score':4}}])['selected_candidate']==1


def test_unavailable_reviewers_and_missing_evidence(candidate,monkeypatch,tmp_path):
    instance=verifier(None,monkeypatch)
    assert instance.get_status()==ToolStatus.UNAVAILABLE
    result=instance.execute(candidate)
    assert result.success and result.data['verification']['status']=='unreviewed'
    assert not result.data['verification']['eligible']
    assert MvJudge(registry_for()).get_status()==ToolStatus.UNAVAILABLE
    audio=tmp_path/'master.wav';audio.write_bytes(b'master')
    judge=MvJudge(registry_for()).execute({'video_path':candidate['input_path'],'audio_path':str(audio)})
    assert judge.success and judge.data['judge']['advisory_only']
    assert judge.data['judge']['status']=='unavailable'


def test_person_count_wrong_hash_or_number(candidate,monkeypatch):
    tool=MockReview(); instance=verifier(tool,monkeypatch)
    candidate['person_count_evidence']['source_sha256']='wrong'
    assert instance.execute(candidate).data['verification']['status']=='unreviewed'
    candidate['person_count_evidence']['source_sha256']=file_sha256(candidate['input_path'])
    candidate['person_count_evidence']['counts']=[1,2]
    assert instance.execute(candidate).data['verification']['status']=='fail'
    assert not tool.calls


def test_paid_review_requires_approval_and_no_provider_fallback(candidate,monkeypatch):
    tool=MockReview(); tool.estimate_cost=lambda inputs:0.25
    result=verifier(tool,monkeypatch).execute(candidate)
    assert not result.success and not tool.calls
    tool.execute=Mock(return_value=ToolResult(success=False,error='remote error',cost_usd=0.25))
    result=verifier(tool,monkeypatch).execute({**candidate,'paid_call_approved':True,'approved_cost_usd':0.5})
    assert result.success and result.cost_usd==0.25 and result.data['verification']['status']=='unreviewed'
    assert tool.execute.call_count==1


def test_candidate_limits():
    assert candidate_budget('clip',generation_unit_usd=1)['candidate_count']==2
    assert candidate_budget('keyframe',3,generation_unit_usd=1,review_unit_usd=0.5)['total_usd']==4.5
    with pytest.raises(ValueError): candidate_budget('clip',4,generation_unit_usd=1)


def test_judge_weights_novelty_and_malformed():
    scores={key:4 for keys in CRITERIA.values() for key in keys}
    scores['ai_novelty']=1
    result=aggregate_scores(scores)
    assert result['total_score']==pytest.approx(4) and result['ai_novelty']==1
    assert result['weights']=={'technical':0.2,'post_production':0.2,'content':0.3,'art':0.3}
    assert aggregate_scores(scores,True)['total_score']==pytest.approx(3.7)
    scores['storytelling']=float('nan')
    with pytest.raises(ValueError): aggregate_scores(scores)


def test_judge_records_model_never_mutates_review(candidate,tmp_path):
    audio=tmp_path/'master.wav';audio.write_bytes(b'master')
    final={'status':'pass','metadata':{}}
    result=MvJudge(registry_for(MockReview())).execute({'video_path':candidate['input_path'],'audio_path':str(audio)})
    final['metadata']['judge']=result.data['judge']
    assert final['status']=='pass' and final['metadata']['judge']['model']=='mock-vision'
    assert not final['metadata']['judge']['publish_gate']


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='local FFmpeg required')
def test_exact_last_frame_and_continuation(tmp_path):
    # Last frame is green, preceding frames red. An approximate seek can return red.
    clip=tmp_path/'three.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=red:s=64x64:r=24:d=0.083333333',
        '-f','lavfi','-i','color=green:s=64x64:r=24:d=0.041666667','-filter_complex',
        '[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-pix_fmt','yuv420p',str(clip)],check=True)
    info=extract_last_frame(clip,tmp_path/'last.png')
    assert info['frame_number']==info['frame_count']-1
    from PIL import Image
    pixel=Image.open(info['path']).convert('RGB').getpixel((32,32))
    assert pixel[1]>pixel[0]
    with pytest.raises(ValueError): verified_continuation_frame({'path':str(clip),'verification':{'status':'unreviewed'}},tmp_path/'bad.png')
    verified=verified_continuation_frame({'path':str(clip),'verification':{'status':'pass','eligible':True,'layers':{'deterministic':{'source_sha256':file_sha256(clip)}}}},tmp_path/'approved.png')
    assert verified['source_clip']==str(clip.resolve())


def test_master_stem_slice_selection(tmp_path,monkeypatch):
    master=tmp_path/'master.wav';master.write_bytes(b'master')
    stem=tmp_path/'vocals.wav';stem.write_bytes(b'stem')
    commands=[]
    def run(cmd,**kwargs):
        commands.append(cmd)
        return SimpleNamespace(stdout=json.dumps({'streams':[{'sample_rate':'48000'}]}))
    monkeypatch.setattr('lib.song_audio.subprocess.run',run)
    song={'audio':{'path':str(master),'duration_seconds':30,'stems':{'vocals':str(stem)}}}
    result=vocal_slice(song,1.25,2.5,tmp_path/'slice.wav')
    assert result['vocal_source_kind']=='vocals' and result['start_sample']==60000
    stem.unlink()
    assert vocal_slice(song,1.25,2.5,tmp_path/'slice.wav')['vocal_source_kind']=='master'
    assert master.read_bytes()==b'master'
    with pytest.raises(ValueError): vocal_slice(song,1,2,master)


def test_malformed_paid_response_keeps_charged_cost(candidate, monkeypatch):
    tool = MockReview()
    tool.estimate_cost = lambda inputs: 0.25
    tool.execute = Mock(return_value=ToolResult(success=True, data={'review':'invalid JSON'}, cost_usd=0.25))
    result = verifier(tool, monkeypatch).execute({**candidate, 'paid_call_approved':True, 'approved_cost_usd':0.5})
    assert result.cost_usd == 0.25
    assert result.data['verification']['status'] == 'unreviewed'
    assert tool.execute.call_count == 1

def test_stale_verified_predecessor_is_rejected(tmp_path):
    clip = tmp_path/'previous.mp4'
    clip.write_bytes(b'original')
    candidate = {'path':str(clip), 'verification':{'status':'pass','eligible':True,
        'layers':{'deterministic':{'source_sha256':file_sha256(clip)}}}}
    clip.write_bytes(b'replaced')
    with pytest.raises(ValueError, match='changed'):
        verified_continuation_frame(candidate, tmp_path/'first.png')


def test_nonfinite_probe_and_budget_do_not_pass(candidate, monkeypatch):
    tool = MockReview()
    instance = verifier(tool, monkeypatch)
    monkeypatch.setattr(instance, '_probe', lambda path:{'duration':float('nan'),'fps':24})
    assert instance.execute(candidate).data['verification']['status']=='fail'
    assert not tool.calls
    tool.estimate_cost=lambda inputs:0.25
    result=verifier(tool,monkeypatch).execute({**candidate,'paid_call_approved':True,'approved_cost_usd':float('nan')})
    assert not result.success and not tool.calls


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_offline_30_second_acceptance(tmp_path):
    from scripts.automv_dry_run import run
    report=run(tmp_path/'dry-run')
    assert report['timeline']['valid'] and report['timeline']['total_frames']==720
    assert report['master_unchanged'] and report['external_calls']==0
    assert report['judge']['model']=='mock-vision' and not report['judge']['publish_gate']
    manifest=json.loads((tmp_path/'dry-run'/'asset_manifest.json').read_text())
    plan=json.loads((tmp_path/'dry-run'/'scene_plan.json').read_text())
    assert all(shot['render_route'] for shot in plan['scenes'])
    assert all(asset['candidates'][asset['selected_candidate']]['verification']['eligible'] for asset in manifest['assets'])
    for frame in report['continuations']:
        assert frame['frame_number']==frame['frame_count']-1
        assert frame['source_sha256']==file_sha256(frame['source_clip'])
    # The first singing window includes 0.5s leading padding, with sample-accurate length.
    path=next((tmp_path/'dry-run').glob('*-vocal.wav'))
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
        'stream=sample_rate:format=duration','-of','json',str(path)],text=True))
    assert data['streams'][0]['sample_rate']=='48000'
    shot=next(s for s in plan['scenes'] if s['generation_context']['lip_sync'])
    assert float(data['format']['duration'])==pytest.approx(shot['generation_context']['generation_duration_seconds'])
