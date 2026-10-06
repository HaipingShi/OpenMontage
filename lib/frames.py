"""Exact decoded last-frame extraction with source provenance (no seek)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def extract_last_frame(clip, output_path=None):
    clip = Path(clip).resolve()
    output = Path(output_path).resolve() if output_path else clip.with_name(clip.stem + '.last.png')
    if not clip.is_file() or output == clip or output.with_suffix('.json') == clip:
        raise ValueError('clip must exist and output must be separate')
    source_hash = file_sha256(clip)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
        '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(clip)],
        capture_output=True, text=True, check=True, timeout=120)
    count = int(json.loads(probe.stdout)['streams'][0]['nb_read_frames'])
    if count < 1:
        raise ValueError('clip has no decoded frames')
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(clip), '-map', '0:v:0',
        '-vf', f'select=eq(n\\,{count-1})', '-fps_mode', 'passthrough', '-frames:v', '1', str(output)],
        check=True, capture_output=True, timeout=120)
    if not output.is_file() or source_hash != file_sha256(clip):
        raise ValueError('frame missing or source changed during extraction')
    provenance = {'path': str(output), 'source_clip': str(clip), 'source_sha256': source_hash,
                  'frame_number': count-1, 'frame_count': count, 'method': 'decoded_frame_index'}
    output.with_suffix('.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return provenance
