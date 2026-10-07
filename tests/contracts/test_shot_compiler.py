"""Offline guarantees for structure-driven shot compilation (WP2)."""
from __future__ import annotations

import copy
import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def compiler():
    # Missing implementation is the intended RED signal, not test collection setup.
    return importlib.import_module("lib.shot_compiler").compile


def spec(**changes):
    value = {
        "shot_id": "shot-a", "scene_id": "scene-a", "revision": 1,
        "updated_by": "agent", "intent": "A quiet room with a fully shut drawer",
        "duration_seconds": 5, "aspect_ratio": "16:9", "target_resolution": "720p",
        "framing": {"shot_size": "wide", "lens_mm": 35, "camera_height": 1.5,
                    "camera_angle": "eye level"},
        "movement": {"type": "static", "intensity": 0},
        "subjects": [], "references": [],
        "environment": {"location": "linen workshop", "time": "afternoon",
                        "lighting": "natural", "palette": ["soft amber"]},
        "exclusions": [], "notes": "", "route": "t2v",
    }
    value.update(changes)
    return value


def context(**changes):
    value = {"capability": "video_generation", "estimated_cost": 0.2,
             "tool_options": {"model": "offline-model"}, "limits": {
                 "max_reference_images": 9, "max_prompt_chars": 50000,
                 "negative_prompt_field": "negative_prompt", "durations": [5, 8, 10],
                 "first_last_frame": True, "control_inputs": [],
                 "routes": ["t2v", "i2v", "first_last", "multi_ref"],
             }}
    value.update(changes)
    return value


def ref(name, role="identity", subject="fati"):
    return {"asset_path": f"assets/refs/{name}.png", "sha256": "a" * 64,
            "role": role, "subject_ref": subject}


def artist(parts=None, action="sings gently"):
    return {"kind": "artist", "ref_id": "fati", "placement": "center midground",
            "action": action, "visible_parts": parts or ["face", "upper_body"],
            "look": "road"}


def identity():
    return {"fati": {"name": "Fati", "prompt_core": "Fati has an oval face and dark eyes.",
                     "negative_prompt": "nose ring, tattoos", "wardrobe": "white linen shirt",
                     "locked_traits": ["Oval face", "Right wrist brass compass bracelet", "Short dark hair"],
                     "visible_traits": {"hands": ["Right wrist brass compass bracelet"],
                                        "back": ["Short dark hair"]}}}


def codes(result):
    return {lint["code"] for lint in result["lint"]}


def test_five_layers_and_notes_are_compiled(compiler):
    result = compiler(spec(notes="Keep the opening still."), "mock-video", context())
    assert "35mm lens" in result["prompt"]
    assert "wide shot" in result["prompt"]
    assert "linen workshop" in result["prompt"]
    assert result["prompt"].endswith("Keep the opening still.")
    assert result["estimated_cost"] == 0.2
    assert result["generatable"]


def test_reference_role_order_and_actual_map_after_truncation(compiler):
    refs = [ref("style", "style"), ref("place", "environment"), ref("look", "wardrobe"),
            ref("start", "first_frame", ""), ref("fati"), ref("amor", "identity", "amor")]
    cfg = context(); cfg["limits"]["max_reference_images"] = 3
    result = compiler(spec(route="multi_ref", subjects=[artist()], references=refs), "mock", cfg)
    actual = result["payload"]["references"]
    assert [r["asset_path"] for r in actual] == [refs[4]["asset_path"], refs[5]["asset_path"], refs[3]["asset_path"]]
    assert result["payload"]["reference_map"] == {"fati": [refs[4]["asset_path"]], "amor": [refs[5]["asset_path"]]}
    dropped = next(l for l in result["lint"] if l["code"] == "REFERENCES_TRUNCATED")
    assert all(refs[i]["asset_path"] in dropped["message"] for i in [0, 1, 2])


def test_truncated_artist_gets_specific_warning(compiler):
    cfg = context(); cfg["limits"]["max_reference_images"] = 1
    subjects = [artist(), {**artist(), "ref_id": "amor"}]
    result = compiler(spec(route="multi_ref", subjects=subjects, references=[ref("a"), ref("b", subject="amor")]), "mock", cfg)
    assert "SUBJECT_IDENTITY_DROPPED" in codes(result)
    assert "amor" in next(l["message"] for l in result["lint"] if l["code"] == "SUBJECT_IDENTITY_DROPPED")


def test_full_face_keeps_identity_and_wardrobe(compiler):
    result = compiler(spec(subjects=[artist()], identity_context=identity()), "mock", context())
    assert "oval face" in result["prompt"] and "dark eyes" in result["prompt"]
    assert "white linen shirt" in result["prompt"]
    assert "nose ring" in result["negative_prompt"]


@pytest.mark.parametrize("parts,expected", [(["hands"], "Right wrist brass compass bracelet"), (["back"], "Short dark hair")])
def test_hands_and_back_crop_full_face(compiler, parts, expected):
    result = compiler(spec(subjects=[artist(parts)], identity_context=identity()), "mock", context())
    assert expected in result["prompt"]
    assert "white linen shirt" in result["prompt"]
    assert "oval face" not in result["prompt"] and "dark eyes" not in result["prompt"]


def test_complete_profile_snapshot_uses_selected_look(compiler):
    profile = {"names": {"stage": "Fati"}, "identity": {
        "prompt_core": "Oval face and dark eyes.", "negative_prompt": "tattoos",
        "locked_traits": ["Right wrist compass"],
        "looks": [{"id": "other", "wardrobe": "blue coat"}, {"id": "road", "wardrobe": "linen shirt"}]}}
    result = compiler(spec(subjects=[artist()], identity_context={"fati": profile}), "mock", context())
    assert "linen shirt" in result["prompt"] and "blue coat" not in result["prompt"]


def test_real_fati_negative_canon_normalizes_without_losing_positive_identity(compiler):
    canon = identity()
    canon["fati"]["prompt_core"] = (
        "FATI, fictional adult woman age 26. Natural light beige skin with neutral soft pink undertones, "
        "no bronze tan. Clean unpierced nose, NO nose ring or nose stud. "
        "Short soft bob. Adult artist composite; not a factual prediction of anyone’s future appearance.")
    result = compiler(spec(subjects=[artist()], identity_context=canon), "mock", context())
    assert result["generatable"]
    assert "Natural light beige skin" in result["prompt"] and "Clean unpierced nose" in result["prompt"]
    assert "bronze tan" in result["negative_prompt"]
    assert "NO nose ring" not in result["prompt"]


def test_multi_artist_descriptions_preserve_distinct_names_and_refs(compiler):
    canon = identity(); canon["amor"] = {**canon["fati"], "name": "Amor", "prompt_core": "Warm oval face, light stubble."}
    result = compiler(spec(route="multi_ref", subjects=[artist(), {**artist(), "ref_id": "amor"}],
                           references=[ref("f"), ref("a", subject="amor")], identity_context=canon), "mock", context())
    assert "Fati" in result["prompt"] and "Amor" in result["prompt"]
    assert "distinct" in result["prompt"]
    assert result["generatable"]


def test_exclusions_go_only_to_negative(compiler):
    result = compiler(spec(exclusions=["people", "drawer opens automatically"]), "mock", context())
    assert "people" in result["negative_prompt"]
    assert "drawer opens automatically" not in result["prompt"]


def test_exclusions_rewrite_or_warn_for_no_negative_tool(compiler):
    cfg = context(); cfg["limits"]["negative_prompt_field"] = None
    result = compiler(spec(exclusions=["open drawer", "watermark", "peculiar unknown restriction"]), "mock", cfg)
    assert "drawer fully shut" in result["prompt"]
    assert "peculiar unknown restriction" not in result["prompt"]
    assert "EXCLUSION_DROPPED" in codes(result)
    assert result["negative_prompt"] == ""


@pytest.mark.parametrize("bad", ["no people", "without people", "not a person", "没有人物", "不出现人物"])
def test_final_positive_negation_is_an_error(compiler, bad):
    result = compiler(spec(notes=bad), "mock", context())
    assert "POSITIVE_NEGATION" in codes(result)
    assert not result["generatable"]


def test_negation_does_not_match_word_fragments(compiler):
    result = compiler(spec(notes="notable light in a notebook, North window"), "mock", context())
    assert "POSITIVE_NEGATION" not in codes(result)


@pytest.mark.parametrize("cause", [None, "", " "])
def test_nonstatic_movement_requires_cause(compiler, cause):
    movement = {"type": "dolly_in", "intensity": 1}
    if cause is not None: movement["cause"] = cause
    result = compiler(spec(movement=movement), "mock", context())
    assert "MISSING_MOVEMENT_CAUSE" in codes(result) and not result["generatable"]


def test_empty_drawer_motion_is_warned_and_reason_is_compiled(compiler):
    result = compiler(spec(movement={"type": "pan_left", "intensity": 1, "cause": "editing rhythm"}), "mock", context())
    assert "MOTION_WITHOUT_PERSON" in codes(result)
    assert "editing rhythm" in result["prompt"]


def test_prop_is_not_a_person_for_motion(compiler):
    result = compiler(spec(subjects=[{"kind": "prop", "ref_id": "drawer", "action": "holds a needle"}],
                           movement={"type": "dolly_in", "intensity": 2, "cause": "camera rail"}), "mock", context())
    assert "MOTION_WITHOUT_PERSON" in codes(result)


def test_insert_identity_reference_warns(compiler):
    result = compiler(spec(framing={"shot_size": "insert"}, subjects=[artist(["hands"])], references=[ref("face")]), "mock", context())
    assert "IDENTITY_REFERENCE_OUT_OF_FRAME" in codes(result)


@pytest.mark.parametrize("intent,environment,subjects", [
    ("灯亮，灯不亮", {}, []),
    ("the lamp is on, the lamp is off", {}, []),
    ("没有人物", {}, [artist()]),
    ("no people", {}, [artist()]),
    ("Quiet room", {"time": "midnight", "palette": ["bright daylight golden sun"]}, []),
    ("安静房间", {"time": "深夜", "palette": ["正午阳光金色"]}, []),
])
def test_contradictions_block_generation(compiler, intent, environment, subjects):
    result = compiler(spec(intent=intent, environment=environment, subjects=subjects), "mock", context())
    assert "CONTRADICTORY_DESCRIPTION" in codes(result) and not result["generatable"]


def test_warm_night_interior_is_not_false_palette_conflict(compiler):
    result = compiler(spec(environment={"time": "night", "lighting": "tungsten_warm", "palette": ["warm amber"]}), "mock", context())
    assert "CONTRADICTORY_DESCRIPTION" not in codes(result)


@pytest.mark.parametrize("action", ["缝补", "线穿过针眼，手指收拢", "write with a pen", "playing piano", "sewing linen"])
def test_fine_hand_action_warns_for_insert(compiler, action):
    result = compiler(spec(framing={"shot_size": "insert"}, subjects=[artist(["hands"], action)]), "mock", context())
    assert "FINE_HAND_ACTION" in codes(result)


def test_fine_hand_warning_not_needed_for_wide(compiler):
    result = compiler(spec(subjects=[artist(action="sewing linen")]), "mock", context())
    assert "FINE_HAND_ACTION" not in codes(result)


def test_missing_limits_are_conservative(compiler):
    result = compiler(spec(references=[ref("one"), ref("two"), ref("end", "last_frame")]), "legacy", {"estimated_cost": 1})
    assert len(result["payload"]["reference_images"]) == 1
    assert result["payload"]["first_frame"] is None and result["payload"]["last_frame"] is None
    assert "MISSING_TOOL_LIMITS" in codes(result)


def test_variant_limit_is_not_global_maximum(compiler):
    cfg = context(); cfg["limits"].update(max_reference_images=9, variants={"2.5": {"max_reference_images": 30}})
    refs = [ref(str(i)) for i in range(11)]
    old = compiler(spec(route="multi_ref", references=refs), "seedance_video", cfg)
    cfg["tool_options"]["model_version"] = "2.5"
    new = compiler(spec(route="multi_ref", references=refs), "seedance_video", cfg)
    assert len(old["payload"]["reference_images"]) == 9
    assert len(new["payload"]["reference_images"]) == 11


def test_first_last_frames_route(compiler):
    refs = [ref("last", "last_frame", ""), ref("first", "first_frame", "")]
    result = compiler(spec(route="first_last", references=refs), "mock", context())
    assert result["payload"]["first_frame"] == refs[1]["asset_path"]
    assert result["payload"]["last_frame"] == refs[0]["asset_path"]
    assert result["generatable"]


@pytest.mark.parametrize("route,refs", [("first_last", [ref("first", "first_frame")]), ("i2v", [])])
def test_required_route_inputs_are_errors(compiler, route, refs):
    result = compiler(spec(route=route, references=refs), "mock", context())
    assert "MISSING_ROUTE_REFERENCE" in codes(result) and not result["generatable"]


def test_unsupported_route_is_not_silently_changed(compiler):
    cfg = context(); cfg["limits"]["routes"] = ["t2v", "i2v"]
    result = compiler(spec(route="multi_ref", references=[ref("f")]), "kling_video", cfg)
    assert "UNSUPPORTED_ROUTE" in codes(result) and not result["generatable"]
    assert result["payload"]["route"] == "multi_ref"


def test_composition_fallback_and_control_routing(compiler):
    refs = [ref("d", "depth", ""), ref("p", "pose", ""), ref("c", "composition", "")]
    base = compiler(spec(route="i2v", references=refs), "mock", context())
    assert base["payload"]["reference_images"] == [refs[2]["asset_path"]]
    assert base["payload"]["first_frame"] == refs[2]["asset_path"]
    assert base["payload"]["control_inputs"] == {}
    assert "UNSUPPORTED_CONTROL_INPUT" in codes(base)
    cfg = context(); cfg["limits"]["control_inputs"] = ["depth", "pose"]
    controlled = compiler(spec(route="i2v", references=refs), "workflow", cfg)
    assert controlled["payload"]["control_inputs"] == {"depth": refs[0]["asset_path"], "pose": refs[1]["asset_path"]}


def test_duration_constraints_and_prompt_limit_block_generation(compiler):
    cfg = context(); cfg["limits"]["durations_by_route"] = {"multi_ref": [8]}
    result = compiler(spec(route="multi_ref", references=[ref("a")]), "veo_video", cfg)
    assert "UNSUPPORTED_DURATION" in codes(result)
    cfg = context(); cfg["limits"]["max_prompt_chars"] = 10
    assert "PROMPT_TOO_LONG" in codes(compiler(spec(), "mock", cfg))


def test_unknown_cost_is_null_and_never_free(compiler):
    cfg = context(estimated_cost=None)
    result = compiler(spec(), "h3_max_video", cfg)
    assert result["estimated_cost"] is None and "PRICE_QUOTE_REQUIRED" in codes(result)


def test_hash_stable_without_mutating_or_accessing_external_state(compiler, monkeypatch):
    value = spec(subjects=[artist()], identity_context=identity(), compile_context=context())
    original = copy.deepcopy(value)
    def forbidden(*a, **kw): raise AssertionError("compiler touched external state")
    # Imports happened in compiler fixture before blocking filesystem/env helpers.
    with monkeypatch.context() as m:
        m.setattr(Path, "read_text", forbidden)
        m.setattr(Path, "read_bytes", forbidden)
        m.setattr(os, "getenv", forbidden)
        first = compiler(value, "mock")
        second = compiler(copy.deepcopy(value), "mock")
    assert first == second and value == original
    assert len(first["compiled_hash"]) == 64


def test_hash_tracks_actual_inputs_but_excludes_revision_and_quote(compiler):
    value = spec(references=[ref("a")], compile_context=context())
    original = compiler(value, "mock")["compiled_hash"]
    value["revision"] += 1; value["updated_by"] = "user"
    value["compile_context"]["estimated_cost"] = 2.5
    assert compiler(value, "mock")["compiled_hash"] == original
    value["references"][0]["sha256"] = "b" * 64
    assert compiler(value, "mock")["compiled_hash"] != original


def test_registry_wrapper_only_calls_pure_compiler(compiler):
    module = importlib.import_module("tools.analysis.shot_compiler")
    result = module.ShotCompiler().execute({"spec": spec(), "tool_name": "mock", "context": context()})
    assert result.success and result.data["generatable"] and result.cost_usd == 0


def test_tool_metadata_limits_expose_real_supported_inputs():
    from tools.video.seedance_video import SeedanceVideo
    from tools.video.kling_video import KlingVideo
    from tools.video.h3_max_video import H3MaxVideo
    from tools.graphics.flux_image import FluxImage
    from tools.graphics.openai_image import OpenAIImage
    assert SeedanceVideo.limits["max_reference_images"] == 9
    assert SeedanceVideo.limits["variants"]["2.5"]["max_reference_images"] == 30
    assert KlingVideo.limits["first_last_frame"] is False
    assert H3MaxVideo.limits["max_reference_images"] == 9
    assert FluxImage.limits["max_reference_images"] == 0
    assert OpenAIImage.limits["max_reference_images"] == 16


def test_root_schema_palette_string_and_numeric_angle_compile_readably(compiler):
    value = spec(environment={"location": "workshop", "time": "afternoon", "palette": "muted amber"},
                 framing={"shot_size": "wide", "lens_mm": 50, "camera_height": 1.2, "camera_angle": 15})
    result = compiler(value, "mock", context())
    assert "muted amber" in result["prompt"]
    assert "camera angle 15" in result["prompt"]
    assert "m, u, t, e, d" not in result["prompt"]


def test_nested_tools_descriptor_and_visible_text_traits(compiler):
    canon = identity(); canon["fati"]["visible_traits"]["hands"] = "Brass compass on the right wrist"
    value = spec(subjects=[artist(["hands"])], identity_context=canon)
    result = compiler(value, "mock", {"tools": {"mock": context()}})
    assert "Brass compass on the right wrist" in result["prompt"]


@pytest.mark.parametrize("changes,code", [
    ({"route": "unknown"}, "UNSUPPORTED_ROUTE"),
    ({"framing": "flat"}, "INVALID_STRUCTURE"),
    ({"subjects": "person"}, "INVALID_STRUCTURE"),
    ({"references": "image.png"}, "INVALID_STRUCTURE"),
    ({"references": [{"asset_path": "x", "role": "bogus"}]}, "INVALID_REFERENCE"),
    ({"notes": "x" * 4001}, "NOTES_TOO_LONG"),
])
def test_bad_drafts_preview_errors_without_generation(compiler, changes, code):
    result = compiler(spec(**changes), "mock", context())
    assert code in codes(result) and not result["generatable"]


def test_invalid_tool_options_and_reference_limit(compiler):
    cfg = context(tool_options="arbitrary provider payload")
    cfg["limits"]["max_reference_images"] = -1
    result = compiler(spec(), "mock", cfg)
    assert "INVALID_TOOL_OPTIONS" in codes(result) and "INVALID_TOOL_LIMIT" in codes(result)


def test_first_last_unsupported_and_unseen_identity_not_transmitted(compiler):
    cfg = context(); cfg["limits"].update(first_last_frame=False, max_reference_images=0)
    result = compiler(spec(route="first_last", references=[ref("a", "first_frame"), ref("b", "last_frame")]), "mock", cfg)
    assert "UNSUPPORTED_FIRST_LAST" in codes(result)
    assert result["payload"]["first_frame"] is None and result["payload"]["last_frame"] is None


def test_day_palette_with_midnight_moonlight_is_conflicting(compiler):
    result = compiler(spec(environment={"time": "noon", "palette": ["midnight moonlight"]}), "mock", context())
    assert "CONTRADICTORY_DESCRIPTION" in codes(result)


def test_nonfinite_draft_has_lint_and_stable_result(compiler):
    result = compiler(spec(duration_seconds=float("nan")), "mock", context())
    assert "UNSUPPORTED_DURATION" in codes(result) and not result["generatable"]


def test_invalid_spec_wrapper_returns_tool_error(compiler):
    from tools.analysis.shot_compiler import ShotCompiler
    assert not ShotCompiler().execute({"spec": [], "tool_name": "mock"}).success
    assert not ShotCompiler().execute({}).success


def test_metadata_contract_appears_without_changing_execution():
    from tools.video.kling_video import KlingVideo
    from tools.base_tool import ToolStatus
    tool = KlingVideo()
    tool.get_status = lambda: ToolStatus.AVAILABLE
    assert tool.get_info()["limits"] == KlingVideo.limits


def test_image_model_capabilities_are_variant_specific(compiler):
    from tools.graphics.google_imagen import GoogleImagen
    from tools.graphics.atlas_image import AtlasImage
    cfg = context(limits=GoogleImagen.limits, capability="image_generation", tool_options={"model": "imagen-4.0-generate-001"})
    value = spec(route="multi_ref", references=[ref("one")])
    assert not compiler(value, "google_imagen", cfg)["generatable"]
    cfg["tool_options"]["model"] = "gemini-3-pro-image"
    assert compiler(value, "google_imagen", cfg)["generatable"]
    assert AtlasImage.limits["max_reference_images"] == 0


@pytest.mark.parametrize("field,value", [
    ("shot_size", "selfie"), ("lens_mm", -50), ("camera_height", float("inf")),
])
def test_invalid_framing_has_structure_lint(compiler, field, value):
    result = compiler(spec(framing={field: value}), "mock", context())
    assert "INVALID_STRUCTURE" in codes(result) and not result["generatable"]


@pytest.mark.parametrize("movement", [{"type": "levitate", "intensity": 1, "cause": "wind"},
                                      {"type": "static", "intensity": 4}])
def test_invalid_movement_has_structure_lint(compiler, movement):
    result = compiler(spec(movement=movement), "mock", context())
    assert "INVALID_STRUCTURE" in codes(result) and not result["generatable"]


def test_hydrated_resolution_tier_controls_duration(compiler):
    from tools.video.veo_video import VeoVideo
    cfg = context(limits=VeoVideo.limits, tool_options={"resolution": "1080p"})
    result = compiler(spec(duration_seconds=6, target_resolution="1920x1080"), "veo_video", cfg)
    assert "UNSUPPORTED_DURATION" in codes(result)


def test_initialization_warning_survives_compilation(compiler):
    result = compiler(spec(initialization_warnings=["Amor has no approved canonical reference"]), "mock", context())
    assert "INITIALIZATION_WARNING" in codes(result)
    assert any("Amor" in item["message"] for item in result["lint"])


def test_environment_lighting_free_text_and_lighting_key_are_both_preserved(compiler):
    result = compiler(spec(environment={"lighting": "a single practical lamp above the table", "lighting_key": "low_key"}), "mock", context())
    assert "single practical lamp" in result["prompt"] and "dramatic low-key" in result["prompt"]


def test_generation_duration_handles_keep_timeline_duration_unchanged(compiler):
    value = spec(duration_seconds=3.7, generation_context={"generation_duration_seconds": 5, "handles_seconds": 0.5})
    result = compiler(value, "mock", context())
    assert result["payload"]["duration_seconds"] == 5 and result["generatable"]
    assert value["duration_seconds"] == 3.7


def test_compile_errors_can_be_serialized_as_json(compiler):
    import json
    result = compiler(spec(duration_seconds=float("nan")), "mock", context())
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("tool_module,tool_class,route,duration", [
    ("seedance_video", "SeedanceVideo", "t2v", 5),
    ("seedance_video", "SeedanceVideo", "i2v", 5),
    ("seedance_video", "SeedanceVideo", "first_last", 5),
    ("seedance_video", "SeedanceVideo", "multi_ref", 5),
    ("kling_video", "KlingVideo", "i2v", 5),
    ("veo_video", "VeoVideo", "first_last", 8),
])
def test_real_video_adapter_agrees_with_compiled_reference_provenance(compiler, tmp_path, tool_module, tool_class, route, duration):
    """No network: compare compiler provenance to existing provider inputs."""
    import base64
    from PIL import Image
    from lib.shot_execution import tool_inputs
    tool = getattr(importlib.import_module(f"tools.video.{tool_module}"), tool_class)()
    refs = [ref("identity"), ref("start", "first_frame", ""), ref("end", "last_frame", "")]
    for index, reference in enumerate(refs):
        path = tmp_path / reference["asset_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), ["red", "green", "blue"][index]).save(path)
    cfg = context(limits=tool.limits, tool_options={}, capability="video_generation")
    result = compiler(spec(route=route, references=refs, duration_seconds=duration, target_resolution="1920x1080"), tool.name, cfg)
    inputs = tool_inputs(result, tool, tmp_path)
    supplied = set()
    for field, value in inputs.items():
        if "image" not in field and "frame" not in field:
            continue
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str):
                continue
            if item.startswith("data:"):
                supplied.update(r["asset_path"] for r in refs if (tmp_path / r["asset_path"]).read_bytes() == base64.b64decode(item.split(",", 1)[1]))
            elif Path(item).is_file():
                supplied.add(Path(item).relative_to(tmp_path).as_posix())
    expected = {r["asset_path"] for r in result["payload"]["references"]}
    assert expected == supplied
    if route != "multi_ref":
        assert not result["generatable"]
        assert "ROUTE_IDENTITY_DROPPED" in codes(result)


def test_i2v_same_path_identity_and_composition_are_actually_preserved(compiler):
    refs = [ref("shared"), ref("shared", "composition", "")]
    result = compiler(spec(route="i2v", references=refs), "mock", context())
    assert result["generatable"]
    assert result["payload"]["reference_images"] == [refs[0]["asset_path"]]
    assert result["payload"]["references"] == refs
    assert result["payload"]["reference_map"] == {"fati": [refs[0]["asset_path"]]}


def test_i2v_identity_is_not_a_composition_frame_by_accident(compiler):
    result = compiler(spec(route="i2v", references=[ref("face")]), "mock", context())
    assert not result["generatable"]
    assert result["payload"]["reference_images"] == []
    assert result["payload"]["first_frame"] is None
    assert "MISSING_ROUTE_REFERENCE" in codes(result)


def test_t2v_dropped_style_reference_is_explicit_and_image_edit_keeps_refs(compiler):
    value = spec(references=[ref("style", "style", "")])
    video = compiler(value, "mock", context())
    assert video["payload"]["references"] == []
    assert video["payload"]["reference_images"] == []
    assert "ROUTE_REFERENCE_DROPPED" in codes(video)
    image = compiler(value, "mock_image", context(capability="image_generation"))
    assert image["generatable"]
    assert image["payload"]["reference_images"] == [value["references"][0]["asset_path"]]


def test_actual_frame_and_control_metadata_survive_in_provenance(compiler):
    refs = [ref("last", "last_frame", "amor"), ref("start", "first_frame", "fati"), ref("depth", "depth", "")]
    cfg = context(); cfg["limits"]["control_inputs"] = ["depth"]
    result = compiler(spec(route="first_last", references=refs), "workflow", cfg)
    assert result["generatable"]
    assert {r["asset_path"] for r in result["payload"]["references"]} == {r["asset_path"] for r in refs}
    assert result["payload"]["reference_images"] == [refs[1]["asset_path"], refs[0]["asset_path"]]
    assert result["payload"]["reference_map"] == {"fati": [refs[1]["asset_path"]], "amor": [refs[0]["asset_path"]]}


def test_complete_sentences_and_decimal_times_keep_clean_punctuation(compiler):
    value = spec(intent='An ordinary room.', subjects=[artist(action='At 0.50 seconds she lifts her eyes.')],
                 identity_context=identity(), movement={'type':'static','intensity':0,'cause':'Window daylight illuminates her face.'})
    result = compiler(value, 'mock', context())
    assert '..' not in result['prompt'] and 'Movement cause:' not in result['prompt']
    assert '0.50 seconds' in result['prompt'] and 'Window daylight illuminates' in result['prompt']


def test_specific_candle_light_does_not_get_generic_mixed_light(compiler):
    result = compiler(spec(environment={'location':'rental room','lighting':'a single candle below her left cheek',
                                        'lighting_key':'natural','color_temperature':'mixed'}), 'mock', context())
    assert 'single candle' in result['prompt']
    assert 'natural ambient lighting' not in result['prompt']
    assert 'mixed color temperatures' not in result['prompt']


def test_placeholder_and_missing_visible_identity_trait_block_submission(compiler):
    value = spec(subjects=[artist()], identity_context=identity(), intent='director-defined framing')
    assert 'UNRESOLVED_PROMPT_PLACEHOLDER' in codes(compiler(value,'mock',context()))
    value['intent']='She sits beside a window'
    value['identity_context']['fati']['prompt_required_traits']=['soft bangs']
    assert 'IDENTITY_TRAIT_MISSING' in codes(compiler(value,'mock',context()))
    value['subjects'][0]['visible_parts']=['hands']
    assert 'IDENTITY_TRAIT_MISSING' not in codes(compiler(value,'mock',context()))


def test_duration_sizing_preserves_later_reuse_and_discrete_provider_limits(compiler):
    from lib.shot_compiler import size_generation_duration
    assert size_generation_duration([3.03,6.3], {'minimum':4,'maximum':15}) == 7
    assert size_generation_duration([3.03], [5,8,10]) == 5
    with pytest.raises(ValueError):size_generation_duration([15], {'maximum':15})
    value=spec(generation_context={'duration_policy':'source_out_with_handles','source_outs':[3.03,6.3]})
    result=compiler(value,'mock',context(limits={'durations':{'minimum':4,'maximum':15}}))
    assert result['payload']['duration_seconds']==7
    assert value['duration_seconds']==5


def test_fast_lyrics_warn_instead_of_silently_retiming_audio(compiler):
    value=spec(generation_context={'lyric_windows':[{'clip_in_seconds':2.6,'clip_out_seconds':3.4667,'text':'最后那句叮咛'}]})
    result=compiler(value,'mock',context())
    assert 'LYRIC_RATE_REVIEW' in codes(result) and result['payload']['duration_seconds']==5
