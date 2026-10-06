"""AutoMV's twelve criteria, reported as advisory evidence only."""
from __future__ import annotations

from pathlib import Path

import jsonschema

from tools.analysis._review import SKILLS, call_reviewer, score, select_reviewer, write_result
from tools.base_tool import (BaseTool, Determinism, ExecutionMode, ResourceProfile,
                             ToolResult, ToolRuntime, ToolStability, ToolStatus, ToolTier)

CRITERIA = {
    'technical': ['character_consistency', 'physical_authenticity', 'lip_sync_accuracy', 'visual_style_harmony'],
    'post_production': ['shot_continuity', 'audio_visual_correlation'],
    'content': ['musical_theme_relevance', 'storytelling', 'emotional_expression'],
    'art': ['visual_composition_quality', 'creativity', 'ai_novelty'],
}
WEIGHTS = {'technical': 0.2, 'post_production': 0.2, 'content': 0.3, 'art': 0.3}


def aggregate_scores(ratings, include_ai_novelty=False):
    checked = {key: score(ratings[key]) for group in CRITERIA.values() for key in group}
    categories = {}
    for group, keys in CRITERIA.items():
        included = [key for key in keys if include_ai_novelty or key != 'ai_novelty']
        categories[group] = sum(checked[key] for key in included)/len(included)
    return {'criteria': checked, 'categories': categories, 'weights': WEIGHTS,
            'total_score': sum(categories[key]*weight for key, weight in WEIGHTS.items()),
            'ai_novelty': checked['ai_novelty'], 'ai_novelty_in_total': include_ai_novelty}


class MvJudge(BaseTool):
    name = 'mv_judge'
    version = '0.1.0'
    tier = ToolTier.ANALYZE
    capability = 'analysis'
    provider = 'selector'
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.HYBRID
    dependencies = []
    install_instructions = 'Optional: register a multimodal_review provider declaring video_input and audio_input.'
    agent_skills = ['video-understand', 'music-to-video']
    capabilities = ['advisory_mv_review']
    supports = {'advisory_only': True, 'never_publish_gate': True}
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, disk_mb=100)
    idempotency_key_fields = ['video_path', 'audio_path', 'model', 'include_ai_novelty']
    side_effects = ['approved full-video/audio review may incur provider fees', 'writes optional judge report']
    input_schema = {'type': 'object', 'required': ['video_path', 'audio_path'], 'properties': {
        'video_path': {'type': 'string'}, 'audio_path': {'type': 'string'},
        'output_path': {'type': 'string'}, 'include_ai_novelty': {'type': 'boolean'},
        'preferred_tool': {'type': 'string'}, 'preferred_provider': {'type': 'string'},
        'hosting_provider': {'type': 'string'}, 'model': {'type': 'string'},
        'paid_call_approved': {'type': 'boolean'}, 'approved_cost_usd': {'type': 'number', 'minimum': 0}},
        'additionalProperties': False}

    def __init__(self, registry=None):
        if registry is None:
            from tools.tool_registry import registry as default
            registry = default
        self.registry = registry

    def get_status(self):
        try:
            return ToolStatus.AVAILABLE if select_reviewer(self.registry, {}, full_media=True) else ToolStatus.UNAVAILABLE
        except Exception:
            return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs):
        tool = select_reviewer(self.registry, inputs, full_media=True)
        return 0.0 if tool is None else tool.estimate_cost({'operation': 'mv_judge', **inputs})

    def execute(self, inputs):
        cost = 0.0
        judge = {'advisory_only': True, 'notice': '仅供参考', 'publish_gate': False,
                 'status': 'unavailable', 'model': None}
        try:
            jsonschema.validate(inputs, self.input_schema)
            protected = [inputs['video_path'], inputs['audio_path']]
            if inputs.get('output_path') and Path(inputs['output_path']).resolve() in {Path(p).resolve() for p in protected}:
                raise ValueError('judge output must not overwrite media')
            if any(not Path(p).is_file() for p in protected):
                raise ValueError('final video and locked master must exist')
            tool = select_reviewer(self.registry, inputs, full_media=True)
            if tool is None:
                judge['reason'] = 'Optional reviewer with full video and audio input unavailable'
            else:
                judge.update(provider=tool.provider, reviewer_tool=tool.name, model=inputs.get('model'))
                result, ratings = call_reviewer(tool, inputs, {'operation': 'mv_judge',
                    'prompt': (SKILLS/'mv-judge.md').read_text(), 'video_path': inputs['video_path'],
                    'audio_path': inputs['audio_path'], 'criteria': CRITERIA})
                cost = result.cost_usd
                judge['model'] = result.model or result.data.get('model') or judge['model']
                if ratings is None:
                    judge.update(status='failed', reason=result.error)
                else:
                    if not judge['model']:
                        raise ValueError('reviewer must report actual model')
                    judge.update(aggregate_scores(ratings.get('criteria', ratings), inputs.get('include_ai_novelty', False)), status='scored')
            artifacts = write_result(inputs.get('output_path'), judge, protected)
            return ToolResult(success=True, data={'judge': judge}, artifacts=artifacts, cost_usd=cost, model=judge.get('model'))
        except Exception as exc:
            # Advisory failures remain inspectable and must not halt compose/publish.
            judge.update(status='failed', reason=str(exc))
            return ToolResult(success=True, data={'judge': judge}, cost_usd=cost)
