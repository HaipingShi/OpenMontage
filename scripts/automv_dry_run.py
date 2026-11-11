#!/usr/bin/env python3
"""Offline acceptance fixture, NOT a production pipeline or model-quality test.

Mocks inference only; real FFmpeg produces and probes every media file.
Run: python scripts/automv_dry_run.py [--output /path/to/workspace]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.frames import file_sha256
from lib.mv_routes import plan_render_routes, verified_continuation_frame
from lib.paths import PROJECTS_DIR
from lib.song_audio import vocal_slice
from lib.timeline import build_segments, validate_timeline
from schemas.artifacts import validate_artifact
from tools.analysis.mv_judge import CRITERIA, MvJudge
from tools.analysis.shot_verifier import ShotVerifier, select_candidate
from tools.analysis.song_beatmap import SongBeatmap
from tools.analysis.stem_separator import StemSeparator
from tools.base_tool import BaseTool, ToolResult, ToolStatus


def command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)


def ffmpeg(*args):
    return command(['ffmpeg', '-y', '-v', 'error', *map(str, args)])


class FixtureReviewer(BaseTool):
    name = 'fixture_review'
    provider = 'mock'
    capability = 'multimodal_review'
    supports = {'video_input': True, 'audio_input': True}
    def execute(self, inputs):
        if inputs['operation'] == 'physical':
            review = {'pass': True, 'reasons': ['Mock review; not evidence of physical quality']}
        elif inputs['operation'] == 'score':
            review = {'script_lyrics': 4, 'locked_traits': 4, 'singing': 4,
                      'identity': {artist:4 for artist in inputs['reference_map']}}
        else:
            review = {key:4 for group in CRITERIA.values() for key in group}
        return ToolResult(success=True, data={'review':review}, model='mock-vision', cost_usd=0)


class FixtureGenerator(BaseTool):
    name = 'fixture_video'
    provider = 'mock'
    capability = 'video_generation'
    def execute(self, inputs):
        path = Path(inputs['output_path'])
        if inputs.get('first_frame'):
            source = ['-loop', '1', '-framerate', '24', '-i', inputs['first_frame']]
        else:
            source = ['-f', 'lavfi', '-i', 'testsrc2=s=160x96:r=24']
        ffmpeg(*source, '-t', inputs['duration'], '-c:v', 'libx264', '-preset', 'ultrafast',
               '-pix_fmt', 'yuv420p', path)
        # These are the ACTUAL parameters received by the mock generation call.
        log = {'reference_map':inputs['reference_map'],
               'submitted_reference_images':inputs['reference_images'], 'first_frame':inputs.get('first_frame')}
        return ToolResult(success=True, data={'generation_log':log}, artifacts=[str(path)], model='mock-video')


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    master = output/'master.wav'
    ffmpeg('-f','lavfi','-i','sine=frequency=220:sample_rate=48000:duration=30','-c:a','pcm_s16le', master)
    master_hash = file_sha256(master)
    separator = StemSeparator()
    original_command = separator.run_command
    def mock_demucs(args, **kwargs):
        if '-m' in args and 'demucs.separate' in args:
            raw = Path(args[args.index('-o')+1])/'htdemucs'/master.stem
            raw.mkdir(parents=True)
            for name in ('vocals.wav','no_vocals.wav'):
                shutil.copyfile(master, raw/name)
            return SimpleNamespace(stdout='Mock demucs: fixture stems identical to source')
        return original_command(args, **kwargs)
    with patch.object(separator,'get_status',return_value=ToolStatus.AVAILABLE), patch.object(separator,'run_command',side_effect=mock_demucs):
        stems = separator.execute({'input_path':str(master),'output_dir':str(output/'stems')})
    assert stems.success, stems.error
    lyrics = '[Verse 1: Amor]\nHold on\n[Chorus: Amor & Fati]\nWalk away'
    transcript = {'word_timestamps':[{'word':word,'start':start,'end':start+1}
        for word,start in [('hold',0),('on',1),('walk',15),('away',16)]]}
    (output/'transcript.json').write_text(json.dumps(transcript))
    audiomap = {'audio':{'duration_sec':30},'tempo':{'bpm':120,'beats_per_bar':4},
        'grid':{'beats_sec':[i/2 for i in range(60)],'downbeats_sec':list(range(0,30,2))},
        'energy_phases':[{'start':0,'end':30,'level':'MEDIUM','energy':0.5}],
        'phrases':[{'index':i,'start':t,'end':min(30,t+8),'bars':4} for i,t in enumerate(range(0,30,8))],
        'stats':{'grid_counts':{'strong':80,'weak':10,'off-grid':1}}}
    with patch('tools.analysis.song_beatmap._load_analyzer', return_value=SimpleNamespace(analyze=lambda *a,**k:audiomap)):
        result = SongBeatmap().execute({'input_path':str(master),'output_path':str(output/'song_map.json'),
            'transcript_path':str(output/'transcript.json'),'suno_lyrics_text':lyrics,
            'stems':{key:stems.data[key] for key in ('vocals','accompaniment','model')}})
    assert result.success, result.error
    song = result.data['song_map']
    segments = build_segments(song)
    plan = {'version':'1.0','scenes':[{'id':f'scene-{i}', 'segment_id':seg['id'],'type':'generated',
        'description':'Fixture singing' if i==0 else 'Fixture duet',
        'start_seconds':seg['start_seconds'],'end_seconds':seg['end_seconds'],
        'generation_context':{'artist_ids':['amor'] if i==0 else ['amor','fati'],'performance':i==0}}
        for i,seg in enumerate(segments)], 'metadata':{'segments':segments,'mock_run':True}}
    plan = plan_render_routes(plan,song,max_clip_s=8)
    timeline = validate_timeline(plan,song,24)
    refs = {}
    for artist in ('amor','fati'):
        path = output/f'{artist}-fixture-reference.png'
        ffmpeg('-f','lavfi','-i','color=blue:s=160x96:r=24','-frames:v','1',path)
        refs[artist]=[str(path)]
    reviewer = FixtureReviewer()
    registry = SimpleNamespace(ensure_discovered=lambda:None,get_by_capability=lambda c:[reviewer])
    verifier, generator = ShotVerifier(registry), FixtureGenerator()
    manifest = {'version':'1.0','assets':[], 'metadata':{'mock_run':True,'cost_usd':0}}
    selected = {}
    edits = []
    continuation = []
    for shot in plan['scenes']:
        context = shot['generation_context']
        first_frame = None
        if shot['render_route']=='continuation':
            first_frame = verified_continuation_frame(selected[context['continuation_of']],output/f"{shot['id']}-first.png")
            context['first_frame']=first_frame
            continuation.append(first_frame)
        source = None
        if context['lip_sync']:
            source = vocal_slice(song,shot['start_seconds'],shot['end_seconds'],output/f"{shot['id']}-vocal.wav",handles_s=context['handles_seconds'])
        reference_map = {artist:refs[artist] for artist in context['artist_ids']}
        candidates = []
        for attempt in (1,2):
            clip = output/f"{shot['id']}-c{attempt}.mp4"
            generated = generator.execute({'output_path':str(clip),'duration':context['generation_duration_seconds'],
                'first_frame':first_frame['path'] if first_frame else None,'reference_map':reference_map,
                'reference_images':[p for paths in reference_map.values() for p in paths]})
            assert generated.success
            verified = verifier.execute({'input_path':str(clip),'shot':shot,'reference_map':reference_map,
                'generation_log':generated.data['generation_log'],
                'person_count_evidence':{'source_sha256':file_sha256(clip),'producer':'mock test evidence (not real people detection)',
                    'counts':[len(reference_map)]*6},'sample_dir':str(output/f"{shot['id']}-c{attempt}-frames")})
            assert verified.success and verified.data['verification']['eligible'], verified
            candidates.append({'path':str(clip),'attempt':attempt,'verification':verified.data['verification'],
                'cost_usd':0,'generation_log':generated.data['generation_log'], **({'first_frame':first_frame} if first_frame else {})})
        selection = select_candidate(candidates)
        chosen = candidates[selection['selected_candidate']]
        selected[shot['id']]=chosen
        manifest['assets'].append({'id':f"asset-{shot['id']}",'scene_id':shot['id'],'type':'video',
            'path':chosen['path'],'source_tool':generator.name,'model':'mock-video','cost_usd':0,
            'generation_summary':source['generation_summary'] if source else 'Offline mock video',
            'candidates':candidates,'selected_candidate':selection['selected_candidate']})
        edit = output/f"{shot['id']}-edit.mp4"
        frames = round((shot['end_seconds']-shot['start_seconds'])*24)
        ffmpeg('-i',chosen['path'],'-vf',f'trim=start_frame=12:end_frame={12+frames},setpts=PTS-STARTPTS',
            '-frames:v',frames,'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',edit)
        edits.append(edit)
    assert continuation, 'fixture must exercise continuation'
    concat = output/'edits.txt'
    concat.write_text(''.join(f"file '{path.as_posix()}'\n" for path in edits))
    final = output/'mock-film.mp4'
    ffmpeg('-f','concat','-safe','0','-i',concat,'-i',master,'-map','0:v:0','-map','1:a:0',
        '-c:v','copy','-c:a','aac',final)
    judge = MvJudge(registry).execute({'video_path':str(final),'audio_path':str(master)})
    probe = json.loads(command(['ffprobe','-v','error','-select_streams','v:0','-count_frames',
        '-show_entries','stream=nb_read_frames:format=duration','-of','json',str(final)]).stdout)
    assert int(probe['streams'][0]['nb_read_frames'])==720
    final_review = {'version':'1.0','output_path':str(final),'status':'pass',
        'checks':{key:{} for key in ('technical_probe','visual_spotcheck','audio_spotcheck','promise_preservation','subtitle_check')},
        'metadata':{'mock_run':True,'judge':judge.data['judge']}}
    assert master_hash == file_sha256(master)
    for name,artifact in [('song_map',song),('scene_plan',plan),('asset_manifest',manifest),('final_review',final_review)]:
        validate_artifact(name,artifact)
        (output/f'{name}.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
    report = {'mock_run':True,'external_calls':0,'cost_usd':0,'duration_seconds':30,'master_unchanged':True,
        'timeline':timeline,'shots':len(plan['scenes']),'candidates':sum(len(a['candidates']) for a in manifest['assets']),
        'continuations':continuation,'stems':stems.data,'judge':judge.data['judge'],
        'limitations':['Demucs, beat analysis, generation, counts and model judgements are fixtures; no production quality claim.']}
    (output/'dry_run_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='Workspace override (default: configured projects root / automv-upgrade-dry-run)')
    output = parser.parse_args(argv).output
    if output is None:
        output = PROJECTS_DIR / 'automv-upgrade-dry-run'
        path = output
        while path != PROJECTS_DIR:
            if path.is_symlink():
                parser.error(f'default workspace contains a symlink: {path}')
            path = path.parent
        for path in output.rglob('*'):
            if path.is_symlink():
                parser.error(f'default workspace contains a symlink: {path}')
    report=run(output)
    print(json.dumps({key:report[key] for key in ('duration_seconds','timeline','shots','candidates','external_calls','master_unchanged')},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
