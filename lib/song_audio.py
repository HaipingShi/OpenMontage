"""Derived lip-sync audio slices; the locked master is always read-only."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path


def vocal_slice(song_map, start_seconds, end_seconds, output_path, *, handles_s=0):
    audio = song_map['audio']
    stem = audio.get('stems', {}).get('vocals') or audio.get('vocal_stem_path')
    source = Path(stem) if stem and Path(stem).is_file() else Path(audio['path'])
    output = Path(output_path)
    if output.resolve() in {source.resolve(), Path(audio['path']).resolve()}:
        raise ValueError('vocal slice must not overwrite the stem or locked master')
    if not all(math.isfinite(value) for value in (start_seconds, end_seconds)) or not 0 <= start_seconds < end_seconds <= audio['duration_seconds']:
        raise ValueError('vocal window must lie inside the locked song')
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=sample_rate', '-of', 'json', str(source)],
        check=True, capture_output=True, text=True, timeout=30)
    rate = int(json.loads(probe.stdout)['streams'][0]['sample_rate'])
    if not math.isfinite(handles_s) or handles_s < 0:
        raise ValueError('handles must be finite and non-negative')
    frame_zero = start_seconds - handles_s
    first = max(0, round(frame_zero * rate))
    last = min(round(audio['duration_seconds'] * rate), round((end_seconds + handles_s) * rate))
    leading = max(0, -round(frame_zero * rate))
    total = round((end_seconds - start_seconds + 2 * handles_s) * rate)
    filters = f'atrim=start_sample={first}:end_sample={last},asetpts=PTS-STARTPTS'
    if leading:
        filters += f',adelay={leading}S:all=1'
    if handles_s:
        filters += f',apad,atrim=end_sample={total}'
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-af',
        filters,
        '-c:a', 'pcm_f32le', str(output)], check=True, capture_output=True, timeout=120)
    return {'path': str(output), 'vocal_source': str(source), 'vocal_source_kind': 'vocals' if stem and source == Path(stem) else 'master',
            'start_sample': first, 'end_sample': last, 'sample_rate': rate, 'frame_0_seconds': frame_zero, 'handles_seconds': handles_s, 'leading_padding_samples': leading,
            'generation_summary': f'vocal_source={source}; vocal_in={start_seconds}; vocal_out={end_seconds}; frame_0={frame_zero}; handles_seconds={handles_s}'}
