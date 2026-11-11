"""Shot compile/quote/execute boundary shared by Backlot and the headless CLI.

All provider selection, file access and estimates happen here. The compiler
only receives explicit snapshots. Requests are persisted before a provider
call; a running/unknown request is never automatically submitted again.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import math
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from lib.config_model import BudgetMode, OpenMontageConfig
from lib.frames import file_sha256
from lib.shot_spec import ShotConflict, ShotError, atomic_json, identifier, load, read_json, safe_path, transaction
from schemas.artifacts import validate_artifact
from tools.base_tool import ToolStatus
from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError, CostTracker

TOKEN_TTL_SECONDS = 10 * 60


def get_registry():
    from tools.tool_registry import registry
    registry.ensure_discovered()
    return registry


def get_budget(project: Path):
    return OpenMontageConfig.load().budget


def catalog() -> list[dict]:
    registry = get_registry()
    entries = []
    for capability in ("video_generation", "image_generation"):
        for tool in registry.get_by_capability(capability):
            if tool.name.endswith("_selector"):
                continue
            try:
                info = tool.get_info()
                entries.append({**{key: info.get(key) for key in (
                    "name", "provider", "capability", "status", "runtime", "supports", "limits", "best_for")},
                    "available": info.get("status") == "available"})
            except Exception:
                entries.append({"name": tool.name, "provider": tool.provider,
                                "capability": capability, "status": "unavailable", "limits": {}, "available": False})
    return entries


def resolve_tool(spec: dict, tool_name: str | None = None):
    registry = get_registry()
    if tool_name is not None and not isinstance(tool_name, str):
        raise ShotError("UNKNOWN_TOOL", "Tool name must be a string")
    name = tool_name or spec.get("target_tool")
    if name:
        identifier(name)
        tool = registry.get(name)
        if tool is None or tool.capability not in ("image_generation", "video_generation"):
            raise ShotError("UNKNOWN_TOOL", "Choose an existing image or video generation tool")
        return tool
    # Use the selector's current filtering/scoring policy, then freeze the
    # concrete provider for both the quote and eventual execution.
    from tools.video.video_selector import VideoSelector
    selector = VideoSelector()
    inputs = {"operation": {"multi_ref": "reference_to_video", "i2v": "image_to_video",
                           "first_last": "first_last_frame_to_video"}.get(spec.get("route"), "text_to_video"),
              "duration": str(spec.get("duration_seconds", 5)), "aspect_ratio": spec.get("aspect_ratio", "16:9")}
    tools = [tool for tool in registry.get_by_capability("video_generation")
             if tool.name != selector.name and tool.get_status() == ToolStatus.AVAILABLE]
    candidates = selector._filter_candidates(inputs, tools)
    chosen, _ = selector._select_best_tool(inputs, candidates, selector._prepare_task_context(inputs)) if candidates else (None, None)
    if chosen is None:
        raise ShotError("NO_TOOL", "No available provider supports this shot route", 409)
    return chosen


def _identities(spec: dict, project: Path | None) -> dict:
    from lib.project_assets import load_project_artist, binding_descriptor
    identities = {}
    characters = read_json(safe_path(project, "artifacts/character_design.json"), {}) if project else {}
    for subject in spec.get("subjects", []):
        ref_id = subject.get("ref_id")
        if subject.get("kind") == "artist":
            try:
                profile = load_project_artist(project, ref_id)
            except FileNotFoundError as exc:
                raise ShotError("ARTIST_IMPORT_REQUIRED", "Import or create the artist alias inside this project before compiling", 409) from exc
            identities[ref_id] = {key: copy.deepcopy(value) for key, value in profile.items() if not key.startswith("_")}
            identities[ref_id]["source_binding"] = binding_descriptor(project, ref_id)
        elif subject.get("kind") == "character":
            for character in characters.get("characters", []):
                if character.get("id") == ref_id:
                    identities[ref_id] = {"name": character.get("display_name", ref_id),
                        "prompt_core": character.get("identity_prompt", character.get("silhouette_notes", "")),
                        "wardrobe": character.get("wardrobe", ""), "locked_traits": character.get("locked_traits", []),
                        "visible_traits": character.get("visible_traits", {})}
    return identities


def _defaults(tool) -> dict:
    # Only defaults in the actual tool contract can affect provider/model.
    # Client compile_context and provider_params are never used here.
    return {key: copy.deepcopy(prop["default"]) for key, prop in tool.input_schema.get("properties", {}).items()
            if isinstance(prop, dict) and "default" in prop and key in (
                "model", "model_version", "model_variant", "backend", "quality", "mode", "generate_audio",
                "output_format", "number_of_images", "num_images", "n", "enable_safety_checker")}


def _operation(tool, route: str) -> str:
    operation = {"multi_ref": "reference_to_video", "i2v": "image_to_video",
                 "first_last": "first_last_frame_to_video"}.get(route, "text_to_video")
    if route == "first_last" and tool.name in {"seedance_video", "h3_max_video"}:
        operation = "image_to_video"
    return operation


def _properties(tool, route: str = "t2v", options: dict | None = None) -> dict:
    props = copy.deepcopy(tool.input_schema.get("properties", {}))
    # FalMedia's discovery schema merges descriptions from several endpoints
    # and loses types/enums. The shipped endpoint contracts are the precise
    # source for adapting numeric durations, uppercase resolution, and refs.
    from tools.fal_media import CONTRACTS, FalMedia
    if isinstance(tool, FalMedia):
        model = (options or {}).get("model") or next(iter(tool.routes))
        endpoint = tool.routes.get(model, {}).get(_operation(tool, route))
        if endpoint:
            props.update(copy.deepcopy(CONTRACTS[endpoint]["input_schema"]["properties"]))
    return props


def _options(spec: dict, tool) -> dict:
    options = _defaults(tool)
    context = spec.get("compile_context") or {}
    submitted = context.get("tool_options") or {} if isinstance(context, dict) else {}
    if not isinstance(submitted, dict):
        raise ShotError("INVALID_TOOL_OPTIONS", "Tool options must be a structured object")
    allowed = {"model", "model_version", "model_variant", "backend", "quality", "mode", "generate_audio",
               "resolution", "output_format", "number_of_images", "num_images", "n", "enable_safety_checker"}
    properties = _properties(tool, spec.get("route", "t2v"), {**options, **submitted})
    for key, value in submitted.items():
        if key not in allowed:
            continue
        if key not in properties:
            raise ShotError("INVALID_TOOL_OPTIONS", f"The selected tool does not support {key}")
        try:
            json.dumps(value, allow_nan=False)
            jsonschema.validate(value, properties[key])
        except (ValueError, jsonschema.ValidationError) as exc:
            raise ShotError("INVALID_TOOL_OPTIONS", f"Invalid {key} for the selected tool") from exc
        if isinstance(value, str) and len(value) > 300:
            raise ShotError("INVALID_TOOL_OPTIONS", f"Option {key} exceeds the length limit")
        options[key] = copy.deepcopy(value)
    if tool.name == "veo_video" and options.get("backend", "auto") == "auto":
        options["backend"] = "google" if tool._get_google_credentials_status() else "fal" if tool._get_fal_api_key() else "google"
    return options


def _continuation(spec: dict, project: Path) -> None:
    previous_id = spec.get("continuation_of") or spec.get("generation_context", {}).get("continuation_of")
    if not previous_id:
        return
    identifier(previous_id)
    source = next((asset for asset in reversed(candidates(project, previous_id))
                   if type(asset.get("selected_candidate")) is int), None)
    if source is None:
        raise ShotError("UNVERIFIED_CONTINUATION", "Choose a verified predecessor candidate before continuing", 409)
    index = source["selected_candidate"]
    choices = source.get("candidates", [])
    if not 0 <= index < len(choices):
        raise ShotError("UNVERIFIED_CONTINUATION", "Predecessor candidate selection is invalid", 409)
    selected = copy.deepcopy(choices[index])
    raw = selected["path"]
    if Path(raw).is_absolute():
        try:
            raw = Path(raw).resolve().relative_to(project.resolve()).as_posix()
        except ValueError as exc:
            raise ShotError("UNSAFE_REFERENCE", "Predecessor source is outside this project", 403) from exc
    path = safe_path(project, raw)
    if not path.is_file():
        raise ShotError("UNVERIFIED_CONTINUATION", "Predecessor video file is missing", 409)
    selected["path"] = str(path)
    digest = file_sha256(path)
    from lib.mv_routes import verified_continuation_frame
    target = safe_path(project, f"assets/refs/{digest}-last.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame = verified_continuation_frame(selected, target)
    except (ValueError, OSError) as exc:
        raise ShotError("UNVERIFIED_CONTINUATION", str(exc), 409) from exc
    reference = {"asset_path": target.relative_to(project).as_posix(), "sha256": file_sha256(target),
                 "role": "first_frame", "parent_asset": raw}
    spec["references"] = [ref for ref in spec.get("references", []) if ref["role"] != "first_frame"] + [reference]
    spec["generation_context"] = {**spec.get("generation_context", {}), "first_frame": frame}


def artists(project: Path, spec: dict) -> list[dict]:
    """Read-only canon/candidate browser; candidates never enter the shot automatically."""
    from lib.artist_profile import bible_assets
    from lib.project_assets import load_project_artist, binding_descriptor
    from lib.shot_spec import _reference
    result = []
    for subject in spec.get("subjects", []):
        if subject.get("kind") != "artist":
            continue
        try:
            profile = load_project_artist(project, subject["ref_id"])
        except FileNotFoundError:
            continue
        refs = []
        for source in bible_assets(profile, canonical_only=False):
            path = Path(source["abs_path"])
            if not path.is_file():
                continue
            ref = _reference(project, path, "identity" if source.get("reference_role", "identity") == "identity" else "wardrobe",
                             subject["ref_id"], path.relative_to(project).as_posix(), True)
            refs.append({**ref, "canonical": source["canonical"], "candidate": not source["canonical"],
                         "view": source.get("view"), "look": source.get("look"), "source_artist": subject["ref_id"],
                         "review_status": source.get("review_status"), "reference_role": source.get("reference_role", "identity")})
        result.append({"id": subject["ref_id"], "names": profile.get("names", {}),
                       "locked_traits": profile["identity"].get("locked_traits", []),
                       "looks": profile["identity"].get("looks", []), "references": refs,
                       "asset_binding": binding_descriptor(project, subject["ref_id"])})
    return result


def _resolution(spec: dict, tool) -> str | None:
    prop = _properties(tool, spec.get("route", "t2v"), (spec.get("compile_context") or {}).get("tool_options", {})).get("resolution")
    if prop is None:
        return None
    width, height = [int(part) for part in spec.get("target_resolution", "1920x1080").split("x")]
    enum = prop.get("enum", [])
    tier = "4k" if max(width, height) >= 3840 else "1080p" if min(width, height) >= 1080 else "720p" if min(width, height) >= 720 else "480p"
    image_tier = "4K" if max(width, height) >= 3840 else "2K" if max(width, height) >= 2048 else "1K"
    if image_tier in enum:
        return image_tier
    if not enum or tier in enum:
        return tier
    equivalent = next((value for value in enum if value.casefold() == tier.casefold()), None)
    if equivalent:
        return equivalent
    return prop.get("default", enum[-1])


def _data_uri(path: Path) -> str:
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".png": "image/png"}.get(path.suffix.lower())
    if mime is None:
        raise ShotError("INVALID_REFERENCE", "Only local PNG, JPEG and WebP references are supported")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def validate_references(project: Path, spec: dict) -> list[dict]:
    from lib.project_assets import bindings_for_paths
    bindings = bindings_for_paths(project, [path for ref in spec.get("references", [])
        for path in (ref["asset_path"], ref.get("parent_asset")) if path])
    for reference in spec.get("references", []):
        path = safe_path(project, reference["asset_path"])
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ShotError("INVALID_REFERENCE", "Reference image is missing or has an unsupported type")
        if path.stat().st_size > 12 * 1024 * 1024:
            raise ShotError("INVALID_REFERENCE", "Reference exceeds the image size limit")
        if file_sha256(path) != reference["sha256"]:
            raise ShotError("REFERENCE_CHANGED", "A reference image changed; reimport it before compiling", 409)
        parent = reference.get("parent_asset")
        if parent:
            safe_path(project, parent)
    return bindings


def tool_inputs(compiled: dict, tool, project: Path | None = None) -> dict:
    """Adapt stable payload to existing provider inputs without changing tools."""
    payload = compiled["payload"]
    options = copy.deepcopy(payload.get("tool_options") or {})
    route = payload.get("route", "t2v")
    properties = _properties(tool, route, options)
    inputs = {**options, "prompt": compiled["prompt"]}
    from lib.shot_compiler import resolve_limits
    limits = resolve_limits(getattr(tool, "limits", {}), options)
    negative_field = limits.get("negative_prompt_field")
    if compiled.get("negative_prompt") and negative_field:
        inputs[negative_field] = compiled["negative_prompt"]
    duration = payload.get("duration_seconds", 5)
    if "duration" in properties:
        text = str(int(duration)) if float(duration).is_integer() else str(duration)
        dtype = properties["duration"].get("type")
        inputs["duration"] = int(duration) if dtype == "integer" else float(duration) if dtype == "number" else text + "s" if tool.name == "veo_video" else text
    if "duration_seconds" in properties:
        inputs["duration_seconds"] = duration
    if "aspect_ratio" in properties:
        inputs["aspect_ratio"] = payload.get("aspect_ratio", "16:9")
    resolution = payload.get("target_resolution", "1920x1080")
    width, height = [int(part) for part in resolution.split("x")]
    if "resolution" in properties:
        enum = properties["resolution"].get("enum", [])
        wanted = "4k" if max(width, height) >= 3840 else "1080p" if min(width, height) >= 1080 else "720p" if min(width, height) >= 720 else "480p"
        image_size = "4K" if max(width, height) >= 3840 else "2K" if max(width, height) >= 2048 else "1K"
        inputs["resolution"] = options.get("resolution") or (image_size if image_size in enum else wanted)
    if tool.name == "veo_video":
        inputs["auto_fix"] = False
    if "size" in properties:
        enum = properties["size"].get("enum", [])
        size = "1536x1024" if width > height else "1024x1536" if width < height else "1024x1024"
        inputs["size"] = size if size in enum else properties["size"].get("default", "auto")
    for key, value in (("width", width), ("height", height)):
        if key in properties:
            inputs[key] = value
    paths = [str(safe_path(project, path)) if project else path for path in payload.get("reference_images", [])]
    first = payload.get("first_frame")
    last = payload.get("last_frame")
    if project:
        first = str(safe_path(project, first)) if first else None
        last = str(safe_path(project, last)) if last else None
    if tool.capability == "video_generation":
        inputs["operation"] = _operation(tool, route)
        if route == "multi_ref" and paths:
            from tools.fal_media import FalMedia
            if isinstance(tool, FalMedia) and "reference_image_urls" in properties:
                inputs["reference_image_urls"] = [_data_uri(Path(path)) for path in paths]
            elif "reference_image_paths" in properties:
                inputs["reference_image_paths"] = paths
            elif "image_paths" in properties:
                inputs["image_paths"] = paths
            elif "reference_image_urls" in properties:
                inputs["reference_image_urls"] = [_data_uri(Path(path)) for path in paths]
            elif "image_urls" in properties:
                inputs["image_urls"] = [_data_uri(Path(path)) for path in paths]
        elif route in ("i2v", "first_last"):
            first = first or (paths[0] if paths else None)
            if first:
                if route == "first_last" and "first_frame_path" in properties:
                    inputs["first_frame_path"] = first
                    if last:
                        inputs["last_frame_path"] = last
                elif "image_path" in properties:
                    inputs["image_path"] = first
                elif "image_url" in properties:
                    inputs["image_url"] = _data_uri(Path(first))
                if last and "end_image_url" in properties:
                    inputs["end_image_url"] = _data_uri(Path(last))
                if tool.name == "seedance_video":
                    inputs["operation"] = "image_to_video"
    elif paths:
        if "image_paths" in properties:
            inputs["image_paths"] = paths
        elif "image_path" in properties:
            inputs["image_path"] = paths[0]
        elif "image_urls" in properties:
            inputs["image_urls"] = [_data_uri(Path(path)) for path in paths]
        if "generation_mode" in properties:
            inputs["generation_mode"] = "edit"
    # Do not pass unsupported control images into tools that do not consume
    # them. Compiler metadata only advertises explicit workflow mappings.
    controls = payload.get("control_inputs", {})
    mappings = limits.get("control_input_fields", {})
    for role, path in controls.items():
        field = mappings.get(role)
        if field and field in properties:
            inputs[field] = str(safe_path(project, path)) if project else path
    return inputs


def _finite_price(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _unmapped_references(compiled: dict, tool, inputs: dict, project: Path | None) -> list[str]:
    fields = {"image", "images", "image_path", "image_paths", "image_url", "image_urls",
              "reference_image_path", "reference_image_paths", "reference_image_url", "reference_image_urls",
              "first_frame_path", "first_frame_url", "last_frame_path", "last_frame_url", "end_image_url"}
    fields.update(getattr(tool, "limits", {}).get("control_input_fields", {}).values())
    values = set()
    for field in fields:
        value = inputs.get(field)
        if isinstance(value, str):
            values.add(value)
        elif isinstance(value, list):
            values.update(item for item in value if isinstance(item, str))
    missing = []
    for ref in compiled["payload"].get("references", []):
        relative = ref["asset_path"]
        path = safe_path(project, relative) if project else Path(relative)
        if str(path) in values:
            continue
        if path.is_file() and _data_uri(path) in values:
            continue
        missing.append(relative)
    return missing


def preview(spec: dict, tool_name: str | None = None, project: Path | None = None) -> dict:
    from lib.shot_compiler import compile as compile_shot
    tool = resolve_tool(spec, tool_name)
    hydrated = copy.deepcopy(spec)
    hydrated["identity_context"] = _identities(spec, project) if project else copy.deepcopy(spec.get("identity_context", {}))
    context = {"limits": copy.deepcopy(getattr(tool, "limits", {})), "tool_options": _options(spec, tool),
               "capability": tool.capability, "estimated_cost": None}
    try:
        resolution = _resolution(spec, tool)
        if resolution and "resolution" not in context["tool_options"]:
            context["tool_options"]["resolution"] = resolution
    except (ValueError, TypeError):
        # Compiler reports invalid structured fields without attempting to
        # guess a provider resolution for an unfinished browser draft.
        pass
    hydrated["compile_context"] = context
    if project:
        _continuation(hydrated, project)
        validate_references(project, hydrated)
    identities = [copy.deepcopy(identity["source_binding"])
        for identity in hydrated["identity_context"].values() if isinstance(identity, dict) and identity.get("source_binding")]
    hydrated["asset_bindings"] = identities
    compiled = compile_shot(hydrated, tool.name, context=context)
    if project:
        from lib.project_assets import bindings_for_paths
        reference_bindings = bindings_for_paths(project, [path for ref in compiled["payload"].get("references", [])
            for path in (ref["asset_path"], ref.get("parent_asset")) if path])
        hydrated["asset_bindings"] = list({binding["alias"]: binding for binding in [*identities, *reference_bindings]}.values())
        compiled = compile_shot(hydrated, tool.name, context=context)
    mapping_errors = []
    try:
        inputs = tool_inputs(compiled, tool, project)
        mapping_errors = _unmapped_references(compiled, tool, inputs, project)
        quote = tool.dry_run(inputs)
        cost = _finite_price(quote.get("estimated_cost_usd"))
        quote["estimated_cost_usd"] = cost
        if cost is None:
            quote["cost_status"] = "quote_required"
    except Exception as exc:
        quote = {"estimated_cost_usd": None, "cost_status": "quote_required", "quote_reason": str(exc)[:300]}
        cost = None
    context["estimated_cost"] = cost
    compiled = compile_shot(hydrated, tool.name, context=context)
    if mapping_errors:
        compiled["lint"].append({"level": "error", "code": "UNMAPPED_REFERENCE",
            "message": "The tool input adapter cannot submit these declared references: " + ", ".join(mapping_errors)})
        compiled["generatable"] = False
    compiled.update(tool_name=tool.name, provider=tool.provider, dry_run=quote, estimated_cost=cost)
    compiled["asset_bindings"] = copy.deepcopy(hydrated["asset_bindings"])
    return compiled


def _manifest(project: Path) -> tuple[Path, dict]:
    path = safe_path(project, "artifacts/asset_manifest.json")
    value = read_json(path)
    if value is None:
        value = read_json(safe_path(project, "asset_manifest.json"), {"version": "1.0", "assets": [], "total_cost_usd": 0})
    try:
        validate_artifact("asset_manifest", value)
    except Exception as exc:
        raise ShotError("INVALID_MANIFEST", "Refusing to overwrite an invalid asset manifest", 409) from exc
    return path, value


def candidates(project: Path, shot_id: str) -> list[dict]:
    _, manifest = _manifest(project)
    return [copy.deepcopy(asset) for asset in manifest.get("assets", [])
            if asset.get("scene_id") == shot_id or asset.get("shot_id") == shot_id]


def _tracker(project: Path) -> CostTracker:
    budget = get_budget(project)
    path = safe_path(project, "cost_log.json")
    if path.exists():
        value = read_json(path)
        try:
            validate_artifact("cost_log", value)
        except (jsonschema.ValidationError, TypeError) as exc:
            raise ShotError("INVALID_COST_LOG", "Refusing to overwrite an invalid project cost log", 409) from exc
    return CostTracker(budget_total_usd=budget.total_usd, reserve_pct=budget.reserve_pct,
                       single_action_approval_usd=budget.single_action_approval_usd,
                       require_approval_for_new_paid_tool=budget.require_approval_for_new_paid_tool,
                       mode=budget.mode, cost_log_path=path)


def _binding(spec: dict, compiled: dict) -> dict:
    return {"shot_id": spec["shot_id"], "revision": spec["revision"],
            "compiled_hash": compiled["compiled_hash"], "tool_name": compiled["tool_name"],
            "estimated_cost": compiled["estimated_cost"],
            "asset_bindings": copy.deepcopy(compiled.get("asset_bindings", [])),
            "references": [{"asset_path": ref["asset_path"], "sha256": ref["sha256"]}
                           for ref in spec.get("references", [])]}


def _request_path(project: Path, shot_id: str, request_id: str) -> Path:
    return safe_path(project, f"shots/{identifier(shot_id)}.requests/{identifier(request_id)}.json")


def _public_request(record: dict) -> dict:
    return {key: copy.deepcopy(record[key]) for key in (
        "request_id", "status", "shot_id", "revision", "compiled_hash", "tool_name", "estimated_cost",
        "actual_cost", "error", "asset", "cost_entry_id", "ledger_errors", "asset_bindings") if key in record}


def requests(project: Path, shot_id: str) -> list[dict]:
    directory = safe_path(project, f"shots/{identifier(shot_id)}.requests")
    return [_public_request(read_json(safe_path(project, path.relative_to(project).as_posix())))
            for path in sorted(directory.glob("*.json"))] if directory.exists() else []


def _freeze_references(project: Path, shot_id: str, request_id: str, compiled: dict) -> tuple[dict, list[dict]]:
    """Capture the confirmed bytes before reserving funds or releasing locks.

    Original paths remain in asset/cost provenance. Only the temporary adapter
    payload points at request-private snapshots, so subsequent source edits
    cannot change bytes uploaded by a provider.
    """
    frozen = copy.deepcopy(compiled)
    payload = frozen["payload"]
    expected = {ref["asset_path"]: ref["sha256"] for ref in payload.get("references", [])}
    paths = [*payload.get("reference_images", []), payload.get("first_frame"), payload.get("last_frame"),
             *(payload.get("control_inputs") or {}).values()]
    replacements, snapshots = {}, []
    for relative in dict.fromkeys(path for path in paths if path):
        source = safe_path(project, relative)
        try:
            with source.open("rb") as stream:
                data = stream.read(12 * 1024 * 1024 + 1)
        except OSError as exc:
            raise ShotError("INVALID_REFERENCE", "Confirmed reference is unavailable", 409) from exc
        if len(data) > 12 * 1024 * 1024:
            raise ShotError("INVALID_REFERENCE", "Confirmed reference is missing or too large", 409)
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected.get(relative):
            raise ShotError("REFERENCE_CHANGED", "Confirmed reference bytes changed before execution", 409)
        target = f"shots/{identifier(shot_id)}.requests/{identifier(request_id)}.refs/{digest}{source.suffix.lower()}"
        path = safe_path(project, target)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ShotError("REFERENCE_CHANGED", "Existing request snapshot is corrupt", 409)
        else:
            temp = safe_path(project, f"shots/{shot_id}.requests/{request_id}.refs/.{uuid.uuid4().hex}.tmp")
            try:
                temp.write_bytes(data)
                temp.chmod(0o400)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        replacements[relative] = target
        snapshots.append({"asset_path": relative, "sha256": digest, "snapshot_path": target})
    payload["reference_images"] = [replacements[path] for path in payload.get("reference_images", [])]
    for field in ("first_frame", "last_frame"):
        if payload.get(field):
            payload[field] = replacements[payload[field]]
    payload["control_inputs"] = {key: replacements[path] for key, path in (payload.get("control_inputs") or {}).items()}
    return frozen, snapshots


def _approval(project: Path, path: Path, record: dict) -> dict:
    token = secrets.token_urlsafe(32)
    record.update(status="pending", token_hash=hashlib.sha256(token.encode()).hexdigest(),
                  expires_at=time.time() + TOKEN_TTL_SECONDS)
    atomic_json(path, record)
    return {**_public_request(record), "needs_confirmation": True, "confirmation_token": token,
            "confirmation_reasons": record.get("confirmation_reasons", []), "expires_at": record["expires_at"]}


def _record_asset(project: Path, spec: dict, compiled: dict, tool, result, request: dict, actual: float) -> dict | None:
    if not result.success:
        return None
    output = safe_path(project, request["output_path"])
    if not output.is_file():
        raise ShotError("MISSING_OUTPUT", "The tool reported success without writing its expected output", 409)
    _, manifest = _manifest(project)
    provenance = {"shot_spec_revision": spec["revision"], "compiled_hash": compiled["compiled_hash"],
                  "references": copy.deepcopy(compiled["payload"].get("references", [])),
                  "asset_bindings": copy.deepcopy(compiled.get("asset_bindings", [])),
                  "request_id": request["request_id"]}
    parents = [ref["parent_asset"] for ref in provenance["references"] if ref.get("parent_asset")]
    if parents:
        provenance["parent_asset"] = parents[0]
    log = {"reference_map": copy.deepcopy(compiled["payload"].get("reference_map", {})),
           "submitted_reference_images": copy.deepcopy(compiled["payload"].get("reference_images", [])),
           "tool": tool.name, "model": result.model, **provenance}
    candidate = {"path": request["output_path"], "attempt": 1, "cost_usd": actual,
                 "verification": {"status": "unreviewed", "eligible": False},
                 "generation_log": log, **provenance}
    group = next((asset for asset in reversed(manifest["assets"])
                  if asset.get("scene_id") == spec["scene_id"] and asset.get("compiled_hash") == compiled["compiled_hash"]
                  and asset.get("shot_spec_revision") == spec["revision"]
                  and len(asset.get("candidates", [])) < 3 and asset.get("selected_candidate") is None), None)
    if group:
        candidate["attempt"] = len(group["candidates"]) + 1
        group["candidates"].append(candidate)
        group.update(path=request["output_path"], cost_usd=sum(c.get("cost_usd", 0) for c in group["candidates"]))
        asset = group
    else:
        asset = {"id": f"shot-{spec['shot_id']}-{request['request_id'][:12]}", "scene_id": spec["scene_id"],
                 "type": "image" if tool.capability == "image_generation" else "video", "path": request["output_path"],
                 "source_tool": tool.name, "provider": tool.provider, "prompt": compiled["prompt"],
                 "cost_usd": actual, "candidates": [candidate], "selected_candidate": None, **provenance}
        if result.model:
            asset["model"] = result.model
        manifest["assets"].append(asset)
    manifest["total_cost_usd"] = sum(asset.get("cost_usd", 0) for asset in manifest["assets"])
    validate_artifact("asset_manifest", manifest)
    atomic_json(safe_path(project, "artifacts/asset_manifest.json"), manifest)
    return asset


def _ledger(project: Path, spec: dict, compiled: dict, tool, record: dict, actual: float | None, success: bool) -> list[str]:
    from lib.project_assets import record_project_artist_ledger
    failures = []
    for subject in spec.get("subjects", []):
        if subject.get("kind") != "artist":
            continue
        try:
            record_project_artist_ledger(project, subject["ref_id"], {"asset": record["output_path"], "tool": tool.name,
                "human_input": {"source": "shot_workbench", "shot_id": spec["shot_id"],
                                "shot_spec_revision": spec["revision"], "compiled_hash": compiled["compiled_hash"],
                                "asset_bindings": copy.deepcopy(compiled.get("asset_bindings", []))},
                "request_id": record["request_id"], "cost_usd": actual, "success": success,
                "references": compiled["payload"].get("reference_map", {}).get(subject["ref_id"], [])})
        except Exception as exc:
            failures.append(f"{subject['ref_id']}: {str(exc)[:200]}")
    return failures


def render(project: Path, shot_id: str, *, revision: int, compiled_hash: str,
           tool_name: str, lease_id: str, confirmation_token: str | None = None,
           request_id: str | None = None, holder: str = "user") -> dict:
    from lib.shot_workbench import require_lock
    shot_id = identifier(shot_id)
    if confirmation_token is not None and (not isinstance(confirmation_token, str) or len(confirmation_token) > 200):
        raise ShotError("INVALID_CONFIRMATION", "Confirmation token must be a bounded string")
    with transaction(project, f"shot-{shot_id}"), transaction(project):
        require_lock(project, shot_id, holder, lease_id)
        spec = load(project, shot_id)
        if type(revision) is not int or revision != spec["revision"]:
            raise ShotConflict()
        compiled = preview(spec, tool_name, project)
        if not compiled.get("generatable") or any(lint["level"] == "error" for lint in compiled["lint"]):
            raise ShotError("LINT_ERROR", "Resolve compiler errors before generating")
        if compiled["compiled_hash"] != compiled_hash:
            raise ShotError("COMPILE_CHANGED", "The compiled shot changed; preview it again", 409)
        cost = _finite_price(compiled.get("estimated_cost"))
        if cost is None:
            raise ShotError("QUOTE_REQUIRED", "A finite current provider quote is required before generating", 409)
        tool = resolve_tool(spec, compiled["tool_name"])
        if tool.get_status() != ToolStatus.AVAILABLE:
            raise ShotError("TOOL_UNAVAILABLE", "The selected provider is unavailable", 409)
        if tool.name == "veo_video":
            backend = compiled["payload"]["tool_options"]["backend"]
            if (backend == "google" and not tool._get_google_credentials_status()) or (backend == "fal" and not tool._get_fal_api_key()):
                raise ShotError("TOOL_UNAVAILABLE", "The confirmed Veo backend is unavailable", 409)
        binding = _binding(spec, compiled)
        tracker = _tracker(project)
        # One shot may have several completed candidates, but two payable
        # calls cannot overlap and an uncertain remote submission must be
        # reconciled before a fresh request id can initiate another charge.
        for active in requests(project, shot_id):
            if active["status"] in ("running", "unknown") and active["request_id"] != request_id:
                return {**active, "needs_confirmation": False, "replayed": True}
        request = None
        if request_id:
            path = _request_path(project, shot_id, request_id)
            request = read_json(path)
        else:
            directory = safe_path(project, f"shots/{shot_id}.requests")
            for existing in sorted(directory.glob("*.json")) if directory.exists() else []:
                value = read_json(safe_path(project, existing.relative_to(project).as_posix()))
                if value and value.get("binding") == binding:
                    request, request_id = value, value["request_id"]
                    break
            request_id = request_id or uuid.uuid4().hex
            path = _request_path(project, shot_id, request_id)
        if request:
            if request.get("binding") != binding:
                raise ShotError("CONFIRMATION_CHANGED", "The request's shot, references, tool or quote changed", 409)
            if request["status"] in ("running", "unknown", "succeeded", "failed"):
                return {**_public_request(request), "needs_confirmation": False, "replayed": True}
        elif confirmation_token:
            raise ShotError("INVALID_CONFIRMATION", "The confirmation request does not exist", 409)
        if tracker.mode == BudgetMode.CAP and cost > tracker.usable_budget_usd:
            raise ShotError("BUDGET_CAP", "This action exceeds the remaining project budget", 409)
        reasons = []
        budget = get_budget(project)
        if cost > budget.single_action_approval_usd:
            reasons.append("single_action_threshold")
        if cost > 0 and budget.require_approval_for_new_paid_tool and tool.name not in tracker._approved_tools:
            reasons.append("new_paid_tool")
        if request is None:
            extension = ".png" if tool.capability == "image_generation" else ".mp4"
            output_key = hashlib.sha256(f"{shot_id}:{request_id}".encode()).hexdigest()[:32]
            basename = f"{shot_id[:48]}-{output_key}{extension}"
            output = f"assets/{'images' if extension == '.png' else 'video'}/{basename}"
            request = {**binding, "binding": binding, "request_id": request_id, "status": "pending",
                       "created_at": datetime.now(timezone.utc).isoformat(), "output_path": output,
                       "confirmation_reasons": reasons}
        if reasons:
            if not confirmation_token:
                return _approval(project, path, request)
            expected = request.get("token_hash", "")
            if time.time() >= request.get("expires_at", 0) or not expected or not hmac.compare_digest(
                    expected, hashlib.sha256(confirmation_token.encode()).hexdigest()):
                raise ShotError("INVALID_CONFIRMATION", "Confirmation expired or does not match this action", 409)
        # Consume confirmation and persist the request before the provider can
        # be called. A second process sees running, not another payable call.
        frozen, snapshots = _freeze_references(project, shot_id, request_id, compiled)
        inputs = tool_inputs(frozen, tool, project)
        request["reference_snapshots"] = snapshots
        inputs.update(project_dir=str(project), scene_id=spec["scene_id"],
                      output_path=str(safe_path(project, request["output_path"])), external_task_id=request_id)
        if cost > 0:
            tracker.approve_tool(tool.name)
        entry_id = tracker.estimate(tool.name, f"shot:{shot_id}", cost)
        try:
            tracker.reserve(entry_id, action_approved=bool(confirmation_token))
        except (ApprovalRequiredError, BudgetExceededError) as exc:
            tracker.refund(entry_id)
            raise ShotError("BUDGET_APPROVAL", str(exc), 409) from exc
        entry = tracker._find(entry_id)
        entry["shot_spec_revision"] = spec["revision"]
        entry["compiled_hash"] = compiled["compiled_hash"]
        entry["request_id"] = request_id
        entry["references"] = copy.deepcopy(compiled["payload"].get("references", []))
        entry["asset_bindings"] = copy.deepcopy(compiled.get("asset_bindings", []))
        tracker._save()
        request.update(status="running", cost_entry_id=entry_id, token_hash=None, started_at=time.time())
        atomic_json(path, request)
    try:
        result = tool.execute(inputs)
    except Exception as exc:
        with transaction(project):
            request.update(status="unknown", error=str(exc)[:400], finished_at=time.time())
            ledger_errors = _ledger(project, spec, compiled, tool, request, None, False)
            if ledger_errors:
                request["ledger_errors"] = ledger_errors
            atomic_json(path, request)
        return {**_public_request(request), "needs_confirmation": False}
    actual = _finite_price(getattr(result, "cost_usd", None))
    # ToolResult defaults cost_usd to zero, including when adapters swallow
    # polling/transport errors after a remote submission. Zero is therefore
    # not a final receipt for an operation that carried a positive quote.
    if cost > 0 and actual == 0:
        actual = None
    with transaction(project):
        tracker = _tracker(project)
        if actual is None:
            detail = str(result.error)[:300] if result.error else "Provider did not report a confirmed actual charge"
            request.update(status="unknown", error=detail)
            ledger_errors = _ledger(project, spec, compiled, tool, request, None, False)
            if ledger_errors:
                request["ledger_errors"] = ledger_errors
        else:
            tracker.reconcile(entry_id, actual, success=bool(result.success))
            request.update(status="succeeded" if result.success else "failed", actual_cost=actual,
                           error=str(result.error)[:400] if result.error else None)
            try:
                asset = _record_asset(project, spec, compiled, tool, result, request, actual)
                if asset:
                    request["asset"] = asset
            except Exception as exc:
                # The charge remains reconciled even if disk/provenance
                # persistence fails. This request is never auto-resubmitted.
                request.update(status="failed", error=f"Result persistence failed: {str(exc)[:400]}")
            ledger_errors = _ledger(project, spec, compiled, tool, request, actual, bool(result.success))
            if ledger_errors:
                request["ledger_errors"] = ledger_errors
        request["finished_at"] = time.time()
        atomic_json(path, request)
    return {**_public_request(request), "needs_confirmation": False}
