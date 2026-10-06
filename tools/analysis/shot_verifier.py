"""Layered candidate verification; deterministic evidence precedes paid review."""
from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path

import jsonschema

from lib.frames import file_sha256
from tools.analysis._review import SKILLS, call_reviewer, score, select_reviewer, write_result
from tools.analysis.frame_sampler import FrameSampler
from tools.base_tool import (BaseTool, Determinism, ExecutionMode, ResourceProfile,
                             ToolResult, ToolRuntime, ToolStability, ToolStatus, ToolTier)


def select_candidate(candidates):
    """No regeneration loop. Empty selection means the director revises the shot."""
    eligible = [(index, item) for index, item in enumerate(candidates)
                if item.get('verification', {}).get('eligible') is True
                and item['verification'].get('status') == 'pass']
    if not eligible:
        return {'selected_candidate': None, 'action': 'revise_shot'}
    index, _ = max(eligible, key=lambda pair: (pair[1]['verification']['score'], -pair[0]))
    return {'selected_candidate': index, 'action': 'use_candidate'}


def candidate_budget(kind, count=None, *, generation_unit_usd, review_unit_usd=0):
    count = count if count is not None else (2 if kind == 'clip' else 1)
    if kind not in ('clip', 'keyframe') or not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 3:
        raise ValueError('candidate count must be 1–3 (clips default to 2)')
    if any(not math.isfinite(value) or value < 0 for value in (generation_unit_usd, review_unit_usd)):
        raise ValueError('candidate costs must be finite and non-negative')
    return {'candidate_count': count, 'generation_usd': count * generation_unit_usd,
            'review_usd': count * review_unit_usd,
            'total_usd': count * (generation_unit_usd + review_unit_usd)}


class ShotVerifier(BaseTool):
    name = 'shot_verifier'
    version = '0.1.0'
    tier = ToolTier.ANALYZE
    capability = 'analysis'
    provider = 'selector'
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.HYBRID
    dependencies = ['cmd:ffmpeg', 'cmd:ffprobe']
    install_instructions = 'Optional: register an approved multimodal_review BaseTool provider; install FFmpeg for free probes.'
    agent_skills = ['ffmpeg', 'ai-video-gen']
    capabilities = ['shot_candidate_verification']
    supports = {'optional_reviewer': True, 'per_artist_scores': True, 'early_rejection': True}
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, disk_mb=200)
    idempotency_key_fields = ['input_path', 'shot', 'reference_map', 'generation_log', 'person_count_evidence', 'model']
    side_effects = ['samples candidate frames', 'writes optional verification report', 'approved reviewer may charge per call']
    input_schema = {'type': 'object', 'required': ['input_path', 'shot'], 'properties': {
        'input_path': {'type': 'string'}, 'shot': {'type': 'object'},
        'kind': {'type': 'string', 'enum': ['clip', 'keyframe']},
        'fps': {'type': 'number', 'exclusiveMinimum': 0},
        'reference_map': {'type': 'object'}, 'locked_traits': {'type': 'object'},
        'generation_log': {'type': 'object'}, 'expected_people': {'type': 'integer', 'minimum': 0},
        'person_count_evidence': {'type': 'object'}, 'output_path': {'type': 'string'},
        'sample_dir': {'type': 'string'}, 'preferred_tool': {'type': 'string'},
        'preferred_provider': {'type': 'string'}, 'hosting_provider': {'type': 'string'},
        'model': {'type': 'string'}, 'paid_call_approved': {'type': 'boolean'},
        'approved_cost_usd': {'type': 'number', 'minimum': 0}}, 'additionalProperties': False}

    def __init__(self, registry=None):
        if registry is None:
            from tools.tool_registry import registry as default
            registry = default
        self.registry = registry

    def get_status(self):
        try:
            if super().get_status() == ToolStatus.UNAVAILABLE:
                return ToolStatus.UNAVAILABLE
            return ToolStatus.AVAILABLE if select_reviewer(self.registry, {}) else ToolStatus.UNAVAILABLE
        except Exception:
            return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs):
        tool = select_reviewer(self.registry, inputs)
        return 0.0 if tool is None else 2 * tool.estimate_cost({'operation': 'score', **inputs})

    def _probe(self, path):
        data = json.loads(self.run_command(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=avg_frame_rate,duration:format=duration', '-of', 'json', str(path)]).stdout)
        stream = data['streams'][0]
        return {'fps': float(Fraction(stream['avg_frame_rate'])),
                'duration': float(stream.get('duration') or data['format']['duration'])}

    def _deterministic(self, inputs):
        path, shot = Path(inputs['input_path']), inputs['shot']
        reasons = []
        if not path.is_file() or path.stat().st_size == 0:
            return {'pass': False, 'reasons': ['candidate file missing or empty']}
        digest = file_sha256(path)
        if inputs.get('kind', 'clip') == 'clip':
            probe = self._probe(path)
            handles = float(shot.get('generation_context', {}).get('handles_seconds', 0.5))
            required = float(shot['end_seconds']) - float(shot['start_seconds']) + 2 * handles
            if not math.isfinite(handles) or handles < 0 or not math.isfinite(required) or required <= 0 or not math.isfinite(probe['duration']) or probe['duration'] + 1e-6 < required:
                reasons.append('clip shorter than shot plus handles')
            if not math.isfinite(inputs.get('fps', 24)) or not math.isfinite(probe['fps']) or abs(probe['fps'] - inputs.get('fps', 24)) > 0.01:
                reasons.append('non-compliant fps')
        reference_map = inputs.get('reference_map', {})
        required_cast = shot.get('generation_context', {}).get('artist_ids', [])
        if any(not reference_map.get(artist) for artist in required_cast):
            reasons.append('missing official reference_map for cast')
        log = inputs.get('generation_log', {})
        actual_map = log.get('reference_map', {})
        actual = {str(Path(p).resolve()) for p in log.get('submitted_reference_images', [])}
        for artist, refs in reference_map.items():
            expected = {str(Path(p).resolve()) for p in refs}
            logged = {str(Path(p).resolve()) for p in actual_map.get(artist, [])}
            if not expected or not all(Path(p).is_file() for p in refs) or not expected <= logged or not expected <= actual:
                reasons.append(f'official identity references not submitted: {artist}')
        evidence = inputs.get('person_count_evidence', {})
        counts = evidence.get('counts', [])
        if (evidence.get('source_sha256') != digest or not evidence.get('producer') or not counts
                or any(type(count) is not int or count < 0 for count in counts)):
            reasons.append('person-count evidence missing or not bound to candidate hash')
        elif any(count != inputs.get('expected_people', len(reference_map)) for count in counts):
            reasons.append('person count differs from shot description')
        return {'pass': not reasons, 'reasons': reasons, 'source_sha256': digest,
                'pending_manual': reasons == ['person-count evidence missing or not bound to candidate hash']}

    def execute(self, inputs):
        cost = 0.0
        report = {'status': 'fail', 'eligible': False, 'score': None, 'layers': {}, 'action': 'revise_shot'}
        try:
            jsonschema.validate(inputs, self.input_schema)
            if inputs.get('output_path') and Path(inputs['output_path']).resolve() == Path(inputs['input_path']).resolve():
                raise ValueError('review output must not overwrite candidate')
            deterministic = self._deterministic(inputs)
            report['layers']['deterministic'] = deterministic
            if not deterministic['pass']:
                if deterministic.get('pending_manual'):
                    report.update(status='unreviewed', action='manual_review')
                return self._result(inputs, report, cost)
            tool = select_reviewer(self.registry, inputs, video=inputs.get('kind', 'clip') == 'clip')
            if tool is None:
                report.update(status='unreviewed', action='manual_review', reason='optional multimodal reviewer unavailable')
                return self._result(inputs, report, cost)
            if inputs.get('kind', 'clip') == 'clip':
                samples = FrameSampler().execute({'input_path': inputs['input_path'], 'strategy': 'count', 'count': 6,
                    'output_dir': inputs.get('sample_dir') or str(Path(inputs['input_path']).parent / 'review_frames' / deterministic['source_sha256'][:16])})
                if not samples.success or not samples.data['frames']:
                    raise ValueError(samples.error or 'no review frames')
                frames = samples.data['frames']
            else:
                frames = [{'path': inputs['input_path'], 'index': 0}]
            prompt = (SKILLS / 'shot-verifier.md').read_text()
            request = {'operation': 'physical', 'prompt': prompt, 'frames': frames,
                'video_path': inputs['input_path'] if inputs.get('kind', 'clip') == 'clip' else None,
                'shot': inputs['shot'], 'reference_map': inputs.get('reference_map', {}),
                'locked_traits': inputs.get('locked_traits', {})}
            report.update(provider=tool.provider, model=inputs.get('model'), reviewer_tool=tool.name)
            result, physical = call_reviewer(tool, inputs, request)
            cost += result.cost_usd or 0.0
            report['model'] = result.model or result.data.get('model') or report['model']
            if physical is None:
                report.update(status='unreviewed', action='manual_review', reason=result.error)
                return self._result(inputs, report, cost)
            if type(physical.get('pass')) is not bool or not isinstance(physical.get('reasons'), list):
                raise ValueError('physical response requires boolean pass and reasons array')
            if not report['model']:
                raise ValueError('reviewer must report actual model')
            report['layers']['physical'] = {**physical, 'model': report['model']}
            if not physical['pass']:
                return self._result(inputs, report, cost)
            remaining = {**inputs, 'approved_cost_usd': max(0.0, inputs.get('approved_cost_usd', 0)-cost)}
            result, ratings = call_reviewer(tool, remaining, {**request, 'operation': 'score'})
            cost += result.cost_usd or 0.0
            if ratings is None:
                report.update(status='unreviewed', action='manual_review', reason=result.error)
                return self._result(inputs, report, cost)
            scoring_model = result.model or result.data.get('model') or inputs.get('model')
            if not scoring_model:
                raise ValueError('reviewer must report actual scoring model')
            values = {'script_lyrics': score(ratings['script_lyrics']),
                      'locked_traits': score(ratings['locked_traits'])}
            identities = ratings.get('identity', {})
            if inputs.get('reference_map'):
                values['identity'] = min(score(identities[artist]) for artist in inputs['reference_map'])
            sync = inputs['shot'].get('render_route') == 'performance_lipsync' or inputs['shot'].get('generation_context', {}).get('lip_sync')
            if sync:
                values['singing'] = score(ratings['singing'])
            report['layers']['scoring'] = {**ratings, 'dimension_scores': values, 'model': scoring_model}
            report.update(status='pass', eligible=True, score=sum(values.values())/len(values), action='use_candidate')
            return self._result(inputs, report, cost)
        except Exception as exc:
            report.update(status='unreviewed', eligible=False, action='manual_review', reason=str(exc))
            return ToolResult(success=False, data={'verification': report}, error=str(exc), cost_usd=cost)

    def _result(self, inputs, report, cost):
        artifacts = write_result(inputs.get('output_path'), report, [inputs['input_path']])
        return ToolResult(success=True, data={'verification': report}, artifacts=artifacts, cost_usd=cost, model=report.get('model'))
