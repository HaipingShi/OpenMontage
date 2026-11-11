"""Workbench integration is discoverable and only applies to generated shots."""
from pathlib import Path

import pytest

from lib.pipeline_loader import load_pipeline

PIPELINES = ['cinematic','character-animation','animation','music-video','hybrid','animated-explainer']
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', PIPELINES)
def test_complex_shot_contract_is_in_pipeline(name):
    pipeline = load_pipeline(name)
    assert 'meta/shot-workbench' in pipeline['required_skills']
    for stage_name in ['scene_plan','assets']:
        stage = next(s for s in pipeline['stages'] if s['name'] == stage_name)
        assert 'shot_compiler' in stage['tools_available']
        assert 'shot_compiler' in stage['required_tools']
        assert any('shot spec' in item.lower() and 'lint' in item.lower() for item in stage['review_focus'])
        text = (ROOT/'skills'/f"{stage['skill']}.md").read_text()
        assert 'meta/shot-workbench.md' in text


def test_meta_skill_requires_handoff_and_offline_contract():
    text = (ROOT/'skills/meta/shot-workbench.md').read_text()
    for value in ['release','compile','dry-run','revision','lint','references','single_action_approval_usd']:
        assert value in text
    assert 'source footage' in text.lower()
