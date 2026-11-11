"""Scene plans preserve focal lengths accepted by the shared ShotSpec."""
import pytest
from jsonschema import ValidationError

from schemas.artifacts import validate_artifact


def scene_plan(lens):
    return {"version": "1.0", "scenes": [{
        "id": "singing-platform", "type": "generated", "description": "Platform performance",
        "start_seconds": 0, "end_seconds": 5, "shot_language": {"lens_mm": lens},
    }]}


@pytest.mark.parametrize("lens", [28, 40, 65, 75, 32.5])
def test_scene_plan_preserves_non_catalog_focal_lengths(lens):
    validate_artifact("scene_plan", scene_plan(lens))


@pytest.mark.parametrize("lens", [0, -35, 2001, "40mm"])
def test_scene_plan_rejects_invalid_focal_lengths(lens):
    with pytest.raises(ValidationError):
        validate_artifact("scene_plan", scene_plan(lens))
