"""Shared optional reviewer routing. Never select a substitute after failure."""
from __future__ import annotations

import json
import math
from pathlib import Path

from tools.base_tool import ToolStatus
from tools.provider_routing import filter_explicit_route

SKILLS = Path(__file__).resolve().parents[2] / 'skills' / 'meta'


def select_reviewer(registry, inputs, *, full_media=False, video=False):
    registry.ensure_discovered()
    candidates = [tool for tool in registry.get_by_capability('multimodal_review')
                  if tool.get_status() != ToolStatus.UNAVAILABLE]
    if video:
        candidates = [tool for tool in candidates if tool.supports.get('video_input')]
    if full_media:
        candidates = [tool for tool in candidates if tool.supports.get('video_input') and tool.supports.get('audio_input')]
    preferred_provider = inputs.get('preferred_provider')
    if preferred_provider:
        candidates = [tool for tool in candidates if tool.provider == preferred_provider]
    candidates = filter_explicit_route(inputs, candidates)
    return candidates[0] if candidates else None


def score(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 1 <= value <= 5:
        raise ValueError('review score must be a finite number from 1 to 5')
    return float(value)


def call_reviewer(tool, inputs, request):
    """A host must announce and reserve every paid call before using this helper.

    No implicit retries or provider fallback. The approved envelope must cover
    this call. The director reconciles actual costs, including failed calls.
    """
    params = {**request, **{key: inputs[key] for key in ('model', 'model_id', 'model_name') if key in inputs}}
    # Transports declaring their own paid guard receive the already announced envelope.
    props = tool.input_schema.get('properties', {})
    params.update({key:inputs[key] for key in ('paid_call_approved','approved_cost_usd')
                   if key in props and key in inputs})
    estimate = tool.estimate_cost(params)
    if estimate is None or not math.isfinite(estimate) or estimate < 0:
        raise ValueError('reviewer needs a valid cost quote')
    if estimate > 0:
        envelope = float(inputs.get('approved_cost_usd', 0))
        if not inputs.get('paid_call_approved') or not math.isfinite(envelope) or envelope < estimate:
            raise ValueError('paid review requires an announced, reserved cost envelope')
    result = tool.execute(params)
    if result.cost_usd is None:
        result.error = result.error or 'Review cost unknown; reconcile billing before continuing'
        return result, None
    if not result.success:
        return result, None
    content = result.data.get('review', result.data)
    try:
        if isinstance(content, str):
            content = json.loads(content)
        if not isinstance(content, dict):
            raise ValueError('review response must be a JSON object')
    except (ValueError, TypeError) as exc:
        # Retain charged usage even when the provider returned malformed JSON.
        result.error = str(exc)
        return result, None
    return result, content


def write_result(output_path, data, protected_paths=()):
    if not output_path:
        return []
    output = Path(output_path)
    if output.resolve() in {Path(path).resolve() for path in protected_paths}:
        raise ValueError('review output must not overwrite input media')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return [str(output)]
