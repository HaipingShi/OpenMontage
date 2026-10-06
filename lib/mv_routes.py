"""Deterministic sizing of an approved shot plan; no provider or creative swaps."""
from __future__ import annotations

import copy
import math

from lib.timeline import quantize

ROUTES = ('story_i2v', 'performance_lipsync', 'continuation', 'cast_multi_ref')


def plan_render_routes(scene_plan, song_map, *, fps=24, max_clip_s=8,
                       handles_s=0.5, lip_sync_scope='hooks_only', performance_scene_ids=()):
    """Split generation shots without conflating narrative segments and cut units.

    Per-shot generation_context can name cast, lip_sync, and continuation_of.
    Directors still choose shot intent and confirm the resulting plan.
    """
    if lip_sync_scope not in ('none', 'hooks_only', 'all_sung_closeups'):
        raise ValueError('invalid lip_sync_scope')
    if fps <= 0 or not math.isfinite(fps) or handles_s < 0 or not math.isfinite(handles_s):
        raise ValueError('invalid fps/handles')
    if not math.isfinite(max_clip_s):
        raise ValueError('provider duration must be finite')
    capacity = math.floor((max_clip_s - 2 * handles_s) * fps)
    if capacity < 1:
        raise ValueError('provider duration cannot fit handles and one frame')
    result = copy.deepcopy(scene_plan)
    shots = []
    for scene in result['scenes']:
        if scene.get('type') != 'generated':
            shots.append(scene)
            continue
        if not scene.get('segment_id'):
            raise ValueError('every scene needs a segment_id before routing')
        context = scene.get('generation_context', {})
        cast = context.get('artist_ids', [a['character_id'] for a in scene.get('character_actions', [])])
        performance = scene['id'] in performance_scene_ids or context.get('performance', False)
        at_entry = any(abs(scene['start_seconds']-section['start_seconds']) < 1/fps for section in song_map['sections'])
        at_hook = any(line.get('hook') and line.get('start_seconds') is not None
                      and scene['start_seconds'] <= line['start_seconds'] < scene['end_seconds']
                      for line in song_map.get('lyrics', {}).get('lines', []))
        sync = performance and lip_sync_scope != 'none' and (lip_sync_scope == 'all_sung_closeups' or at_entry or at_hook or scene.get('hero_moment', False))
        route = 'performance_lipsync' if sync else 'cast_multi_ref' if len(cast) > 1 else 'story_i2v'
        if context.get('continuation_of'):
            route = 'continuation'
        first = round(quantize(scene['start_seconds'], fps) * fps)
        last = round(quantize(scene['end_seconds'], fps) * fps)
        if last <= first:
            raise ValueError('scene has no frames')
        count = math.ceil((last-first) / capacity)
        for part in range(count):
            shot = copy.deepcopy(scene)
            shot['id'] = scene['id'] if count == 1 else f"{scene['id']}-p{part+1:02d}"
            shot['start_seconds'] = (first + part * capacity) / fps
            shot['end_seconds'] = min(last, first+(part+1)*capacity) / fps
            shot['render_route'] = route if part == 0 else 'continuation'
            shot['generation_context'] = {**context, 'artist_ids': list(cast), 'lip_sync': sync,
                'handles_seconds': handles_s,
                'generation_duration_seconds': shot['end_seconds']-shot['start_seconds']+2*handles_s}
            if part > 0:
                shot['generation_context']['continuation_of'] = shots[-1]['id']
            if sync:
                shot['generation_context'].update(vocal_in=shot['start_seconds'], vocal_out=shot['end_seconds'])
            shots.append(shot)
    result['scenes'] = shots
    return result


def verified_continuation_frame(previous_candidate, output_path):
    """Only a selected, fully verified prior clip may seed continuation."""
    from lib.frames import extract_last_frame, file_sha256
    verification = previous_candidate.get('verification', {})
    if verification.get('status') != 'pass' or not verification.get('eligible', False):
        raise ValueError('continuation requires a verified previous clip')
    expected = verification.get('layers', {}).get('deterministic', {}).get('source_sha256')
    if not expected or expected != file_sha256(previous_candidate['path']):
        raise ValueError('verified previous clip hash is missing or changed')
    return extract_last_frame(previous_candidate['path'], output_path)
