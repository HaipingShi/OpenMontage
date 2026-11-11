"""Local Shot Workbench routes; browser requests contain no provider secrets."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import math
import warnings
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image, UnidentifiedImageError

from lib import shot_execution as execution
from lib import shot_spec
from lib import shot_workbench as leases
from lib.shot_spec import ShotConflict, ShotError, safe_path, transaction

MAX_BODY_BYTES = 24 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
_ROLES = {"identity", "wardrobe", "environment", "style", "composition", "first_frame", "last_frame", "depth", "pose"}


async def _json(request: Request) -> dict:
    host = request.url.hostname
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        raise HTTPException(403, "Local workbench writes require a loopback host")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin workbench write denied")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Workbench request exceeds size limit")
        data.extend(chunk)
    try:
        body = json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object")
        return body
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "Expected finite JSON object data") from exc


def _revision(spec: dict, body: dict) -> None:
    if type(body.get("revision")) is not int or body["revision"] != spec["revision"]:
        raise ShotConflict()


def _image(value: str, *, png_only: bool = False) -> tuple[bytes, str]:
    if not isinstance(value, str) or len(value) > (MAX_IMAGE_BYTES * 4 // 3 + 200):
        raise ShotError("IMAGE_SIZE", "Image exceeds size limit", 413)
    try:
        header, encoded = value.split(",", 1)
        types = {"data:image/png;base64": "PNG", "data:image/jpeg;base64": "JPEG",
                 "data:image/jpg;base64": "JPEG", "data:image/webp;base64": "WEBP"}
        expected = types.get(header)
        if expected is None or (png_only and expected != "PNG"):
            raise ValueError("Only PNG, JPEG and WebP images are accepted")
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ShotError("IMAGE_SIZE", "Image exceeds size limit", 413)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Image type or pixel size does not match accepted limits")
                image.verify()
        return data, {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[expected]
    except ShotError:
        raise
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ShotError("INVALID_IMAGE", "Image content is invalid or its type is unsupported") from exc


def _store_image(project: Path, data: bytes, extension: str, role: str, **metadata) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    relative = f"assets/refs/{digest}{extension}"
    path = safe_path(project, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ShotError("REFERENCE_CHANGED", "Existing hashed reference content is corrupt", 409)
    else:
        import uuid
        temp = safe_path(project, f"assets/refs/.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(data)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    return {"asset_path": relative, "sha256": digest, "role": role, **metadata}


def _writable(project: Path, shot: str, body: dict) -> dict:
    leases.require_lock(project, shot, "user", body.get("lease_id"))
    spec = shot_spec.load(project, shot)
    _revision(spec, body)
    return spec


def _protect_canon(previous: dict, draft: dict) -> None:
    canonical = {ref["asset_path"]: ref for ref in previous.get("references", []) if ref.get("read_only")}
    for ref in draft.get("references", []):
        if ref["asset_path"] in canonical or ref.get("read_only") or ref.get("source_artist"):
            if canonical.get(ref["asset_path"]) != ref:
                raise ShotError("READ_ONLY_REFERENCE", "Official artist references are read-only", 403)
    # The browser never authors identity or tool-contract snapshots.
    draft["identity_context"] = copy.deepcopy(previous.get("identity_context", {}))
    context = draft.get("compile_context") or {}
    # Variant selection is a legitimate structured input. Hydration validates
    # this allowlist against the concrete tool and discards client contracts,
    # prices, paths, and final prompt overrides.
    if isinstance(context, dict):
        draft["compile_context"] = {"tool_options": copy.deepcopy(context.get("tool_options", {}))}
    else:
        draft.pop("compile_context", None)


def _vector(value):
    return tuple(float(component) for component in value)


def _sub(left, right):
    return tuple(a - b for a, b in zip(left, right))


def _dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def _unit(vector):
    magnitude = math.sqrt(_dot(vector, vector))
    if magnitude < 1e-10:
        raise ShotError("INVALID_STAGE", "Camera must have a valid, nonvertical viewing direction")
    return tuple(component / magnitude for component in vector)


def _cross(left, right):
    x, y, z = left
    a, b, c = right
    return (y*c-z*b, z*a-x*c, x*b-y*a)


def stage_placements(stage: dict, aspect_ratio: str) -> dict[str, str]:
    """Recompute subject placement in camera space instead of trusting JS."""
    camera = stage["camera"]
    position = _vector(camera["position"])
    forward = _unit(_sub(_vector(camera["target"]), position))
    cross = _cross(forward, (0, 1, 0))
    right = _unit(cross if _dot(cross, cross) > 1e-10 else _cross(forward, (0, 0, 1)))
    width, height = (float(part) for part in aspect_ratio.split(":"))
    aspect = width / height
    sensor_height = min(camera.get("sensor_height_mm", 24), camera.get("sensor_width_mm", 36)/aspect)
    tangent = sensor_height / (2 * camera["lens_mm"])
    subjects = [obj for obj in stage["objects"] if obj.get("subject_ref")]
    distances = [_dot(_sub(_vector(obj["position"]), position), forward) for obj in subjects]
    nearest, farthest = (min(distances), max(distances)) if distances else (0, 0)
    result = {}
    for obj, depth in zip(subjects, distances):
        delta = _sub(_vector(obj["position"]), position)
        x = _dot(delta, right)/(depth*tangent*aspect) if depth > camera.get("near", .1) else 0
        horizontal = "左" if x < -1/3 else "右" if x > 1/3 else "中"
        normalized = (depth-nearest)/(farthest-nearest) if farthest-nearest > 1e-10 else .5
        layer = "前景" if normalized < 1/3 else "后景" if normalized > 2/3 else "中景"
        # Euler XYZ applied to local +Z (Three.js' humanoid front direction).
        rx, ry, rz = _vector(obj.get("rotation", [0, 0, 0]))
        # XYZ Euler is R_x * R_y * R_z, so local +Z is independent of z.
        facing = (math.sin(ry), -math.sin(rx)*math.cos(ry), math.cos(rx)*math.cos(ry))
        toward = _unit(_sub(position, _vector(obj["position"])))
        score = _dot(facing, toward)
        orientation = "朝向镜头" if score > .5 else "背向镜头" if score < -.5 else "侧身"
        result[obj["subject_ref"]] = f"画面{horizontal}，{layer}，{orientation}"
    return result


def _stage_export(project: Path, shot: str, body: dict) -> dict:
    with transaction(project, f"shot-{shot}"):
        spec = _writable(project, shot, body)
        stage = copy.deepcopy(body.get("stage"))
        draft = {**spec, "stage": stage}
        shot_spec.validate(draft)
        width, height = [float(part) for part in spec["target_resolution"].split("x")]
        aw, ah = [float(part) for part in spec["aspect_ratio"].split(":")]
        if abs(width/height - aw/ah) > .001:
            raise ShotError("FRAME_ASPECT_CONFLICT", "Target resolution and aspect ratio must match before stage export")
        images = body.get("images")
        if not isinstance(images, dict) or set(images) != {"composition", "depth", "pose"}:
            raise ShotError("INVALID_STAGE_EXPORT", "Export composition, depth and pose PNGs together")
        decoded = {role: _image(value, png_only=True) for role, value in images.items()}
        pose = body.get("pose")
        if not isinstance(pose, dict) or len(json.dumps(pose, ensure_ascii=False)) > 512_000:
            raise ShotError("INVALID_POSE", "Expected bounded pose keypoint JSON")
        # Replace prior generated stage refs while preserving identity and
        # other board references, then save once to advance one revision.
        refs = [ref for ref in spec["references"] if ref["role"] not in ("composition", "depth", "pose")]
        camera = stage["camera"]
        metadata_path = safe_path(project, f"shots/{shot}.stage/{spec['revision']+1}.json")
        shot_spec.atomic_json(metadata_path, {"pose": pose, "camera": camera})
        new_refs = [_store_image(project, data, ext, role, parent_asset=metadata_path.relative_to(project).as_posix())
                    for role, (data, ext) in decoded.items()]
        draft["references"] = refs + new_refs
        placements = stage_placements(stage, spec["aspect_ratio"])
        for subject in draft["subjects"]:
            if subject["ref_id"] in placements:
                subject["placement"] = placements[subject["ref_id"]]
        draft["framing"].update(lens_mm=camera["lens_mm"], camera_height=camera["position"][1])
        direction = _sub(_vector(camera["target"]), _vector(camera["position"]))
        draft["framing"]["camera_angle"] = round(math.degrees(math.atan2(direction[1], math.hypot(direction[0], direction[2]))), 3)
        saved = shot_spec.save(project, draft, expected_revision=body["revision"], holder="user", lease_id=body["lease_id"])
        return {"spec": saved, "references": new_refs, "pose_path": str(metadata_path.relative_to(project))}


def install(app, project_resolver, hub) -> None:
    router = APIRouter()

    @app.exception_handler(ShotError)
    async def shot_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": str(exc)}})

    def project(identifier: str) -> Path:
        directory = project_resolver(identifier)
        if directory.is_symlink():
            raise ShotError("UNSAFE_PROJECT", "Project aliases must not be symlinks", 403)
        # The board resolver predates writes, so explicitly validate realpath
        # against its configured projects root before any new operation.
        from backlot import server
        if not directory.resolve().is_relative_to(server.PROJECTS_DIR.resolve()):
            raise ShotError("UNSAFE_PROJECT", "Project symlink escapes projects root", 403)
        return directory.resolve()

    def notify(project_id: str):
        from backlot import server
        server._invalidate_summary(project_id)
        hub.publish(project_id)

    @router.get("/api/project/{project_id}/assets/bindings")
    async def asset_bindings(project_id: str):
        from lib.project_assets import list_bindings
        bindings = await asyncio.to_thread(list_bindings, project(project_id))
        return {"version": "1.0", "bindings": bindings}

    @router.post("/api/project/{project_id}/assets/import")
    async def import_project_asset(project_id: str, request: Request):
        from lib.project_assets import import_asset
        body = await _json(request)
        if "library_id" not in body:
            raise ShotError("LIBRARY_REQUIRED", "Import requires an explicit library identity")
        shot_spec.identifier(body["library_id"])
        binding = await asyncio.to_thread(import_asset, project(project_id), body.get("asset_id"),
            body.get("version"), body.get("alias"), body.get("kind", "artist"),
            expected_library_id=body["library_id"])
        notify(project_id)
        return {"binding": binding}

    @router.post("/api/project/{project_id}/assets/fork")
    async def fork_project_asset(project_id: str, request: Request):
        from lib.project_assets import fork_asset
        body = await _json(request)
        binding = await asyncio.to_thread(fork_asset, project(project_id), body.get("alias"), body.get("new_alias"))
        notify(project_id)
        return {"binding": binding}

    @router.get("/w/{project_id}/{shot}", response_class=HTMLResponse)
    async def page(project_id: str, shot: str):
        project(project_id)
        shot_spec.identifier(shot)
        path = Path(__file__).with_name("ui") / "workbench.html"
        return HTMLResponse(path.read_text(encoding="utf-8") if path.exists() else
                            '<!doctype html><html lang="zh"><title>镜头工作台</title><body>镜头工作台</body></html>')

    @router.get("/api/shot/{project_id}/{shot}")
    async def get_shot(project_id: str, shot: str):
        directory = project(project_id)
        await asyncio.to_thread(leases.initialize, directory, shot)
        data = await asyncio.to_thread(leases.status, directory, shot, holder="user")
        lock = copy.deepcopy(data.get("lock"))
        if lock and lock["holder"] != "user":
            lock.pop("lease_id", None)
        spec = data["spec"]
        artists = await asyncio.to_thread(execution.artists, directory, spec)
        return {"spec": spec, "lock": lock, "expired": data.get("expired", False),
                "read_only": data["read_only"], "tools": await asyncio.to_thread(execution.catalog),
                "candidates": await asyncio.to_thread(execution.candidates, directory, shot), "artists": artists,
                "requests": await asyncio.to_thread(execution.requests, directory, shot)}

    @router.put("/api/shot/{project_id}/{shot}")
    async def save_shot(project_id: str, shot: str, request: Request):
        body = await _json(request)
        directory = project(project_id)
        def save():
            with transaction(directory, f"shot-{shot}"):
                previous = _writable(directory, shot, body)
                draft = copy.deepcopy(body.get("spec"))
                if not isinstance(draft, dict) or draft.get("shot_id") != shot:
                    raise ShotError("INVALID_SPEC", "Shot id must match the requested shot")
                shot_spec.validate(draft)
                _protect_canon(previous, draft)
                shot_spec.validate(draft)
                execution.validate_references(directory, draft)
                return shot_spec.save(directory, draft, expected_revision=body["revision"], holder="user", lease_id=body.get("lease_id"))
        result = await asyncio.to_thread(save)
        notify(project_id)
        return {"spec": result}

    @router.post("/api/shot/{project_id}/{shot}/references")
    async def upload_reference(project_id: str, shot: str, request: Request):
        body = await _json(request)
        directory = project(project_id)
        def upload():
            with transaction(directory, f"shot-{shot}"):
                spec = _writable(directory, shot, body)
                role = body.get("role")
                if not isinstance(role, str) or role not in _ROLES:
                    raise ShotError("INVALID_ROLE", "Choose a supported reference role")
                metadata = {}
                for key in ("subject_ref", "parent_asset", "weight"):
                    if key in body and body[key] is not None:
                        metadata[key] = body[key]
                if any(key in metadata and not isinstance(metadata[key], str) for key in ("subject_ref", "parent_asset")):
                    raise ShotError("INVALID_REFERENCE", "Subject and source paths must be strings")
                if metadata.get("parent_asset"):
                    safe_path(directory, metadata["parent_asset"])
                if metadata.get("subject_ref") and metadata["subject_ref"] not in {subject["ref_id"] for subject in spec["subjects"]}:
                    raise ShotError("INVALID_SUBJECT", "Reference subject does not belong to the shot")
                data, extension = _image(body.get("data"))
                ref = _store_image(directory, data, extension, role, **metadata)
                identity = (ref["sha256"], ref["role"], ref.get("subject_ref"))
                if any(previous.get("read_only") and
                       (previous["sha256"], previous["role"], previous.get("subject_ref")) == identity
                       for previous in spec["references"]):
                    raise ShotError("READ_ONLY_REFERENCE", "Official artist references are read-only", 403)
                # Same content may serve different roles/subjects. Exact
                # duplicate metadata is replaced, not duplicated endlessly.
                spec["references"] = [previous for previous in spec["references"]
                    if (previous["sha256"], previous["role"], previous.get("subject_ref")) !=
                       (ref["sha256"], ref["role"], ref.get("subject_ref"))] + [ref]
                saved = shot_spec.save(directory, spec, expected_revision=body["revision"], holder="user", lease_id=body["lease_id"])
                return {"spec": saved, "reference": ref}
        result = await asyncio.to_thread(upload)
        notify(project_id)
        return result

    @router.post("/api/shot/{project_id}/{shot}/compile")
    async def compile_shot(project_id: str, shot: str, request: Request):
        body = await _json(request)
        directory = project(project_id)
        def compile():
            with transaction(directory, f"shot-{shot}"):
                previous = shot_spec.load(directory, shot)
                _revision(previous, body)
                # Compiling is available while read-only, but a draft requires
                # the current user lease; it cannot generate until saved.
                draft = body.get("spec")
                if draft is not None:
                    leases.require_lock(directory, shot, "user", body.get("lease_id"))
                    draft = copy.deepcopy(draft)
                    if not isinstance(draft, dict) or draft.get("shot_id") != shot:
                        raise ShotError("INVALID_SPEC", "Draft must match the shot")
                    shot_spec.validate(draft)
                    _protect_canon(previous, draft)
                return execution.preview(draft or previous, body.get("tool_name"), project=directory)
        return await asyncio.to_thread(compile)

    @router.post("/api/shot/{project_id}/{shot}/render")
    async def render_shot(project_id: str, shot: str, request: Request):
        body = await _json(request)
        directory = project(project_id)
        for key in ("revision", "compiled_hash", "tool_name", "lease_id"):
            if key not in body:
                raise ShotError("MISSING_RENDER_FIELD", f"Render requires {key}")
        result = await asyncio.to_thread(execution.render, directory, shot,
            **{key: body.get(key) for key in ("revision", "compiled_hash", "tool_name", "lease_id", "confirmation_token", "request_id")})
        notify(project_id)
        return result

    @router.post("/api/shot/{project_id}/{shot}/stage-export")
    async def stage_export(project_id: str, shot: str, request: Request):
        body = await _json(request)
        result = await asyncio.to_thread(_stage_export, project(project_id), shot, body)
        notify(project_id)
        return result

    @router.post("/api/shot/{project_id}/{shot}/acquire")
    async def acquire(project_id: str, shot: str, request: Request):
        body = await _json(request)
        directory = project(project_id)
        await asyncio.to_thread(leases.initialize, directory, shot)
        lock = await asyncio.to_thread(leases.acquire, directory, shot, "user", reason=body.get("reason", "用户接管镜头"))
        notify(project_id)
        return {"lock": lock}

    @router.post("/api/shot/{project_id}/{shot}/heartbeat")
    async def heartbeat(project_id: str, shot: str, request: Request):
        body = await _json(request)
        lock = await asyncio.to_thread(leases.heartbeat, project(project_id), shot, "user", body.get("lease_id"))
        notify(project_id)
        return {"lock": lock}

    @router.post("/api/shot/{project_id}/{shot}/release")
    async def release(project_id: str, shot: str, request: Request):
        body = await _json(request)
        result = await asyncio.to_thread(leases.release, project(project_id), shot, "user", body.get("lease_id"))
        notify(project_id)
        return result

    app.include_router(router)
