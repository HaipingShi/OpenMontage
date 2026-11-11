"""Optional htdemucs separation. Cached by master bytes, never alters the master."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import jsonschema

from tools.base_tool import (BaseTool, Determinism, ExecutionMode, ResourceProfile,
                             ToolResult, ToolRuntime, ToolStability, ToolStatus, ToolTier)


class StemSeparator(BaseTool):
    name = 'stem_separator'
    version = '0.1.0'
    tier = ToolTier.ANALYZE
    capability = 'analysis'
    provider = 'demucs'
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL
    dependencies = ['python:demucs', 'cmd:ffmpeg', 'cmd:ffprobe']
    install_instructions = 'Optional: install demucs in the project Python environment and FFmpeg.'
    agent_skills = ['music-to-video', 'ffmpeg']
    capabilities = ['vocal_separation', 'accompaniment_separation']
    supports = {'optional': True, 'master_unchanged': True, 'content_hash_cache': True}
    resource_profile = ResourceProfile(cpu_cores=4, ram_mb=8192, disk_mb=2000)
    idempotency_key_fields = ['input_sha256', 'model']
    side_effects = ['writes cached vocals.wav and accompaniment.wav under output_dir']
    input_schema = {
        'type': 'object', 'required': ['input_path', 'output_dir'],
        'properties': {'input_path': {'type': 'string'}, 'output_dir': {'type': 'string'},
                       'model': {'type': 'string', 'enum': ['htdemucs']},
                       'timeout_seconds': {'type': 'integer', 'minimum': 1}},
        'additionalProperties': False,
    }

    def get_status(self):
        try:
            available = importlib.util.find_spec('demucs') is not None
        except (ImportError, ValueError):
            available = False
        return ToolStatus.AVAILABLE if available and shutil.which('ffmpeg') and shutil.which('ffprobe') else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs):
        return 0.0

    def idempotency_key(self, inputs):
        digest = hashlib.sha256()
        with Path(inputs['input_path']).open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return super().idempotency_key({'input_sha256': digest.hexdigest(), 'model': inputs.get('model', 'htdemucs')})

    def execute(self, inputs):
        started = time.monotonic()
        try:
            jsonschema.validate(inputs, self.input_schema)
            master = Path(inputs['input_path']).resolve()
            if not master.is_file():
                raise ValueError(f'Master not found: {master}')
            cache = Path(inputs['output_dir']).resolve() / self.idempotency_key(inputs)
            # Never permit an output to overwrite the master, including on a cache hit.
            if master.is_relative_to(cache):
                raise ValueError('Stem cache must be separate from the master')
            paths = {name: cache / f'{name}.wav' for name in ('vocals', 'accompaniment')}
            marker = cache / 'stems.json'
            if marker.is_file() and all(p.is_file() and p.stat().st_size > 44 for p in paths.values()):
                saved = json.loads(marker.read_text())
                if saved.get('cache_key') == cache.name:
                    return ToolResult(success=True, data={**saved, 'cache_hit': True}, artifacts=[str(p) for p in paths.values()], model='htdemucs')
            if self.get_status() == ToolStatus.UNAVAILABLE:
                return ToolResult(success=False, data={'status': 'unavailable', 'fallback': 'master'}, error='Optional demucs unavailable; use master for vocal analysis')
            probe = json.loads(self.run_command(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                '-show_entries', 'stream=sample_rate,channels', '-of', 'json', str(master)]).stdout)
            stream = probe['streams'][0]
            rate, channels = int(stream['sample_rate']), int(stream['channels'])
            cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cache.parent) as temporary:
                work = Path(temporary)
                self.run_command([sys.executable, '-m', 'demucs.separate', '-n', 'htdemucs',
                    '--two-stems', 'vocals', '--shifts', '0', '--float32', '-o', str(work), str(master)],
                    timeout=inputs.get('timeout_seconds', 1800))
                raw = work / 'htdemucs' / master.stem
                for name, filename in [('vocals', 'vocals.wav'), ('accompaniment', 'no_vocals.wav')]:
                    self.run_command(['ffmpeg', '-y', '-v', 'error', '-i', str(raw / filename),
                        '-ar', str(rate), '-ac', str(channels), '-c:a', 'pcm_f32le', str(work / f'{name}.wav')])
                cache.mkdir(parents=True, exist_ok=True)
                for name, destination in paths.items():
                    (work / f'{name}.wav').replace(destination)
            data = {**{k: str(v) for k, v in paths.items()}, 'model': 'htdemucs',
                    'sample_rate': rate, 'cache_key': cache.name, 'cache_hit': False}
            marker.write_text(json.dumps(data, indent=2) + '\n')
            return ToolResult(success=True, data=data, artifacts=[str(p) for p in paths.values()],
                              cost_usd=0.0, model='htdemucs', duration_seconds=time.monotonic()-started)
        except Exception as exc:
            return ToolResult(success=False, error=f'Stem separation failed: {exc}')
