"""Explicit reusable file versions and project-owned snapshots.

The library is a local publication store, never an implicit identity lookup.
An imported alias is immutable; a deliberate private fork may change without
changing its source version or another project. All durable paths are relative.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

import yaml

from lib.shot_spec import ShotError, atomic_json, identifier, project_path, read_json, transaction

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILES = 4096
_HISTORY_FILES = {"ledger.jsonl", "releases.jsonl"}


def _root(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ShotError("UNSAFE_PATH", "Asset roots must not be symlinks", 403)
    return path.resolve()


def _safe(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ShotError("UNSAFE_PATH", "Asset paths must be relative", 403)
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts:
        raise ShotError("UNSAFE_PATH", "Asset path escapes its root", 403)
    current = root
    for component in part.parts:
        current = current / component
        if current.is_symlink():
            raise ShotError("UNSAFE_PATH", "Asset symlinks are not portable", 403)
    if not current.resolve().is_relative_to(root.resolve()):
        raise ShotError("UNSAFE_PATH", "Asset path escapes its root", 403)
    return current


def _version(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ShotError("UNSAFE_ID", "Expected a bounded asset version", 400)
    return value


def _alias(value: str, kind: str = "artist") -> str:
    identifier(value)
    if kind == "artist" and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", value):
        raise ShotError("UNSAFE_ID", "Artist aliases must be lowercase directory names", 400)
    return value


def _project(project: str | Path) -> Path:
    if isinstance(project, Path) and project.is_symlink():
        raise ShotError("UNSAFE_PATH", "Project must not be a symlink", 403)
    return project_path(project)


def project_artist_root(project: str | Path) -> Path:
    return _safe(_project(project), "assets/artists")


def _capture(root: Path) -> tuple[list[dict], dict[str, bytes]]:
    if not root.is_dir():
        raise ShotError("ASSET_NOT_FOUND", "Asset source directory does not exist", 404)
    files, captured, total = [], {}, 0
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        path = _safe(root, relative)
        if path.is_dir():
            continue
        if not path.is_file():
            raise ShotError("UNSAFE_PATH", "Only regular asset files may be published", 403)
        if relative in _HISTORY_FILES:
            continue
        if len(files) >= MAX_FILES or path.stat().st_size > MAX_FILE_BYTES:
            raise ShotError("ASSET_SIZE", "Asset exceeds file limits", 413)
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ShotError("ASSET_SIZE", "Asset exceeds size limits", 413)
        files.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
        captured[relative] = data
    if not files:
        raise ShotError("EMPTY_ASSET", "A reusable asset must contain files")
    return files, captured


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _valid_files(files: list) -> None:
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ShotError("INVALID_BINDINGS", "Asset file manifest is invalid", 409)
    seen = set()
    for file in files:
        if not isinstance(file, dict) or set(file) != {"path", "sha256", "size_bytes"}:
            raise ShotError("INVALID_BINDINGS", "Asset file record is invalid", 409)
        path = file["path"]
        if (not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts
                or "\\" in path or "\x00" in path or path in seen
                or not isinstance(file["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", file["sha256"])
                or type(file["size_bytes"]) is not int or not 0 <= file["size_bytes"] <= MAX_FILE_BYTES):
            raise ShotError("INVALID_BINDINGS", "Asset file paths, hashes and sizes must be bounded and relative", 409)
        seen.add(path)


def _check_files(root: Path, expected: list) -> None:
    _valid_files(expected)
    actual, _ = _capture(root)
    if actual != expected:
        raise ShotError("ASSET_CHANGED", "An immutable asset snapshot changed", 409)


def _artist_bytes(captured: dict[str, bytes], alias: str | None = None) -> dict[str, bytes]:
    from schemas.artifacts import validate_artifact
    if "artist.yaml" not in captured:
        raise ShotError("INVALID_ARTIST", "Artist assets require artist.yaml")
    try:
        profile = yaml.safe_load(captured["artist.yaml"])
        validate_artifact("artist_profile", profile)
        json.dumps(profile, allow_nan=False)
        # Bible paths are part of the packaged tree, never external links.
        for ref in profile["identity"]["bible"]:
            path = ref["path"]
            if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts or "\\" in path or path not in captured:
                raise ValueError("Artist bible reference must be a packaged relative file")
        voice_path = profile.get("voice", {}).get("reference_audio")
        if voice_path and (not isinstance(voice_path, str) or voice_path not in captured):
            raise ValueError("Artist voice reference must be a packaged relative file")
        for track in profile.get("voice", {}).get("canonical_tracks", []):
            path = track["path"]
            if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts or "\\" in path or path not in captured:
                raise ValueError("Canonical voice reference must be a packaged relative file")
            if track.get("sha256") and hashlib.sha256(captured[path]).hexdigest() != track["sha256"]:
                raise ValueError("Canonical voice reference checksum changed")
    except Exception as exc:
        raise ShotError("INVALID_ARTIST", "Artist profile or its packaged references are invalid") from exc
    result = dict(captured)
    if alias is not None:
        profile["id"] = alias
        result["artist.yaml"] = yaml.safe_dump(profile, allow_unicode=True, sort_keys=False).encode()
    return result


def _write_tree(target: Path, captured: dict[str, bytes]) -> None:
    target.mkdir(parents=True, exist_ok=False)
    for relative, data in captured.items():
        path = _safe(target, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _library(library: Path | None) -> Path:
    from lib.paths import LIBRARY_DIR
    return _root(library if library is not None else LIBRARY_DIR)


def _library_identity(root: Path, *, create: bool) -> str:
    path = _safe(root, "library.json")
    value = read_json(path)
    if value is None and create:
        value = {"version": "1.0", "library_id": uuid.uuid4().hex}
        atomic_json(path, value)
    if not isinstance(value, dict) or value.get("version") != "1.0":
        raise ShotError("INVALID_LIBRARY", "An explicit initialized asset library is required", 409)
    return identifier(value.get("library_id"))


def _manifest(root: Path, asset_id: str, version: str, kind: str) -> tuple[Path, dict]:
    package = _safe(root, f"versions/{identifier(kind)}/{identifier(asset_id)}/{_version(version)}")
    value = read_json(_safe(package, "manifest.json"))
    if value is None:
        raise ShotError("ASSET_NOT_FOUND", "Published asset version does not exist", 404)
    if not isinstance(value, dict):
        raise ShotError("INVALID_LIBRARY", "Published manifest must be an object", 409)
    base = {key: value.get(key) for key in ("schema_version", "library_id", "asset_id", "version", "kind", "files")}
    if (base["schema_version"] != "1.0" or base["library_id"] != _library_identity(root, create=False)
            or (base["asset_id"], base["version"], base["kind"]) != (asset_id, version, kind)
            or not isinstance(base["files"], list) or value.get("sha256") != _digest(base)):
        raise ShotError("INVALID_LIBRARY", "Published manifest identity or digest is invalid", 409)
    _check_files(_safe(package, "files"), base["files"])
    return package, value


def publish_asset(source_directory: str | Path, asset_id: str, version: str,
                  kind: str = "artist", library: Path | None = None) -> dict:
    identifier(asset_id); identifier(kind); _version(version)
    source, root = _root(source_directory), _library(library)
    if root.is_relative_to(source):
        raise ShotError("UNSAFE_PATH", "Library cannot be nested inside its published source", 403)
    files, captured = _capture(source)
    if kind == "artist":
        _artist_bytes(captured)
    root.mkdir(parents=True, exist_ok=True)
    with transaction(root, "asset-library"):
        library_id = _library_identity(root, create=True)
        manifest = {"schema_version": "1.0", "library_id": library_id, "asset_id": asset_id,
                    "version": version, "kind": kind, "files": files}
        manifest["sha256"] = _digest(manifest)
        package = _safe(root, f"versions/{kind}/{asset_id}/{version}")
        if package.exists():
            _, existing = _manifest(root, asset_id, version, kind)
            if existing != manifest:
                raise ShotError("IMMUTABLE_VERSION", "Publish changes as an explicit new asset version", 409)
            return existing
        package.parent.mkdir(parents=True, exist_ok=True)
        staging = _safe(root, f"versions/{kind}/{asset_id}/.publish-{uuid.uuid4().hex}")
        try:
            _write_tree(staging / "files", captured)
            atomic_json(staging / "manifest.json", manifest)
            staging.replace(package)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return manifest


def _bindings(project: Path) -> tuple[Path, dict]:
    path = _safe(project, "assets/bindings.json")
    value = read_json(path, {"version": "1.0", "bindings": []})
    if not isinstance(value, dict) or value.get("version") != "1.0" or not isinstance(value.get("bindings"), list):
        raise ShotError("INVALID_BINDINGS", "Project bindings manifest is invalid", 409)
    aliases = []
    for binding in value["bindings"]:
        if not isinstance(binding, dict) or not isinstance(binding.get("mode"), str) or binding.get("mode") not in {"imported", "private"}:
            raise ShotError("INVALID_BINDINGS", "Project binding must be an explicit imported or private asset", 409)
        aliases.append(_alias(binding.get("alias"), binding.get("kind")))
        kind = identifier(binding.get("kind"))
        expected_path = f'assets/{"artists" if kind == "artist" else "imports"}/{binding["alias"]}'
        if (binding.get("snapshot_path") != expected_path or not isinstance(binding.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", binding["sha256"])):
            raise ShotError("INVALID_BINDINGS", "Binding snapshot or source digest is invalid", 409)
        _safe(project, expected_path)
        _valid_files(binding.get("files"))
        if binding["mode"] == "imported":
            identifier(binding.get("library_id")); identifier(binding.get("asset_id")); _version(binding.get("version"))
            _valid_files(binding.get("source_files"))
            proof = {"schema_version": "1.0", **{key: binding[key] for key in ("library_id", "asset_id", "version", "kind")},
                     "files": binding["source_files"]}
            if _digest(proof) != binding["sha256"]:
                raise ShotError("INVALID_BINDINGS", "Binding no longer matches its published source identity", 409)
        elif not isinstance(binding.get("source_lineage", {}), dict):
            raise ShotError("INVALID_BINDINGS", "Private asset lineage must be an object", 409)
    if len(aliases) != len(set(aliases)):
        raise ShotError("INVALID_BINDINGS", "Project aliases must be unique", 409)
    return path, value


def get_binding(project: str | Path, alias: str) -> dict | None:
    project = _project(project)
    identifier(alias)
    _, value = _bindings(project)
    return next((copy.deepcopy(binding) for binding in value["bindings"] if binding["alias"] == alias), None)


def verify_binding(project: str | Path, alias: str) -> dict | None:
    project = _project(project)
    binding = get_binding(project, alias)
    if binding and binding["mode"] == "imported":
        _check_files(_safe(project, binding["snapshot_path"]), binding.get("files"))
    elif binding:
        # A private fork may change, while its immutable source remains intact.
        origin = binding.get("source_lineage", {}).get("alias")
        source = get_binding(project, origin) if origin else None
        if source and source["mode"] == "imported":
            _check_files(_safe(project, source["snapshot_path"]), source.get("files"))
    return binding


def _install(project: Path, alias: str, kind: str, captured: dict[str, bytes], binding: dict) -> dict:
    path, value = _bindings(project)
    target = _safe(project, f"assets/artists/{alias}" if kind == "artist" else f"assets/imports/{alias}")
    if target.exists() or any(entry["alias"] == alias for entry in value["bindings"]):
        raise ShotError("ALIAS_EXISTS", "Use a new explicit project alias; existing aliases cannot be overwritten", 409)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = _safe(project, f'{target.parent.relative_to(project).as_posix()}/.import-{uuid.uuid4().hex}')
    try:
        _write_tree(staging, captured)
        local_files, _ = _capture(staging)
        binding.update(alias=alias, snapshot_path=target.relative_to(project).as_posix(), files=local_files)
        staging.replace(target)
        value["bindings"].append(binding)
        try:
            atomic_json(path, value)
        except Exception:
            shutil.rmtree(target)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return copy.deepcopy(binding)


def import_asset(project: str | Path, asset_id: str, version: str, alias: str,
                 kind: str = "artist", library: Path | None = None, *, expected_library_id: str | None = None) -> dict:
    project, root = _project(project), _library(library)
    identifier(asset_id); identifier(kind); _version(version); _alias(alias, kind)
    with transaction(root, "asset-library"), transaction(project, "project-assets"):
        package, manifest = _manifest(root, asset_id, version, kind)
        if expected_library_id is not None and identifier(expected_library_id) != manifest["library_id"]:
            raise ShotError("LIBRARY_MISMATCH", "The selected asset library identity changed", 409)
        existing = get_binding(project, alias)
        identity = {key: manifest[key] for key in ("library_id", "asset_id", "version", "kind", "sha256")}
        if existing:
            if existing.get("mode") != "imported" or any(existing.get(key) != value for key, value in identity.items()):
                raise ShotError("ALIAS_EXISTS", "Import new versions under a new explicit project alias", 409)
            verify_binding(project, alias)
            return existing
        captured_files, captured = _capture(_safe(package, "files"))
        if captured_files != manifest["files"]:
            raise ShotError("ASSET_CHANGED", "Published source changed while importing its bytes", 409)
        if kind == "artist":
            captured = _artist_bytes(captured, alias)
        binding = {**identity, "mode": "imported", "source_files": manifest["files"]}
        return _install(project, alias, kind, captured, binding)


def fork_asset(project: str | Path, alias: str, new_alias: str) -> dict:
    project = _project(project)
    with transaction(project, "project-assets"):
        source = verify_binding(project, alias)
        kind = source["kind"] if source else "artist"
        _alias(alias, kind); _alias(new_alias, kind)
        root = _safe(project, source["snapshot_path"]) if source else _safe(project, f"assets/artists/{alias}")
        files, captured = _capture(root)
        if source and source["mode"] == "imported" and files != source["files"]:
            raise ShotError("ASSET_CHANGED", "Imported source changed while capturing its private fork", 409)
        if kind == "artist":
            captured = _artist_bytes(captured, new_alias)
        lineage = {key: source[key] for key in ("library_id", "asset_id", "version", "kind", "sha256") if source and key in source}
        lineage.update(alias=alias, files=files)
        return _install(project, new_alias, kind, captured, {"mode": "private", "kind": kind,
            "sha256": _digest({"kind": kind, "files": files}), "source_lineage": lineage})


def load_project_artist(project: str | Path, alias: str) -> dict:
    project = _project(project)
    _alias(alias)
    with transaction(project, "project-assets"):
        binding = verify_binding(project, alias)
        root = project_artist_root(project)
        directory = _safe(root, alias)
        if not (directory / "artist.yaml").is_file():
            raise FileNotFoundError(f"Project has no artist alias {alias}; import or create it explicitly")
        if binding and binding.get("kind") != "artist":
            raise ShotError("INVALID_ARTIST", "This project alias is not an artist")
        files, captured = _capture(directory)
        if binding and binding["mode"] == "imported" and files != binding["files"]:
            raise ShotError("ASSET_CHANGED", "Imported artist changed while capturing its profile", 409)
        _artist_bytes(captured)
        profile = yaml.safe_load(captured["artist.yaml"])
        if profile["id"] != alias:
            raise ShotError("INVALID_ARTIST", "Project artist profile id must match its local alias")
        profile["_dir"] = str(directory)
        return profile


def record_project_artist_ledger(project: str | Path, alias: str, event: dict) -> dict:
    from lib.artist_profile import record_ledger
    project = _project(project)
    with transaction(project, "project-assets"):
        load_project_artist(project, alias)
        _safe(project_artist_root(project), f"{alias}/ledger.jsonl")
        return record_ledger(alias, event, root=project_artist_root(project))


def binding_descriptor(project: str | Path, alias: str) -> dict:
    """Small portable provenance record suitable for hashes and cost events."""
    binding = verify_binding(project, alias)
    if binding is None:
        root = _safe(project_artist_root(project), alias)
        return {"alias": alias, "kind": "artist", "mode": "private",
                "snapshot_path": f"assets/artists/{alias}",
                "sha256": hashlib.sha256(_safe(root, "artist.yaml").read_bytes()).hexdigest()}
    result = {key: copy.deepcopy(binding[key]) for key in ("alias", "kind", "mode", "library_id", "asset_id",
        "version", "sha256", "snapshot_path") if key in binding}
    if binding.get("source_lineage"):
        result["source_lineage"] = {key: copy.deepcopy(value) for key, value in binding["source_lineage"].items() if key != "files"}
    return result


def list_bindings(project: str | Path) -> list[dict]:
    project = _project(project)
    with transaction(project, "project-assets"):
        _, value = _bindings(project)
        for binding in value["bindings"]:
            verify_binding(project, binding["alias"])
        return copy.deepcopy(value["bindings"])


def bindings_for_paths(project: str | Path, paths: list[str]) -> list[dict]:
    """Verify every imported asset actually cited by local reference paths."""
    project = _project(project)
    resolved = [_safe(project, path).resolve() for path in paths if path]
    _, value = _bindings(project)
    used = []
    for binding in value["bindings"]:
        root = _safe(project, binding["snapshot_path"]).resolve()
        if any(path.is_relative_to(root) for path in resolved):
            used.append(binding_descriptor(project, binding["alias"]))
    return used


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Explicit local asset publication and project bindings")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("source_directory", type=Path)
    publish.add_argument("asset_id")
    publish.add_argument("version")
    publish.add_argument("--kind", default="artist")
    publish.add_argument("--library", type=Path)
    importing = commands.add_parser("import")
    importing.add_argument("project", type=Path)
    importing.add_argument("asset_id")
    importing.add_argument("version")
    importing.add_argument("alias")
    importing.add_argument("--kind", default="artist")
    importing.add_argument("--library", type=Path)
    fork = commands.add_parser("fork")
    fork.add_argument("project", type=Path)
    fork.add_argument("alias")
    fork.add_argument("new_alias")
    listing = commands.add_parser("list")
    listing.add_argument("project", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "publish":
            value = publish_asset(args.source_directory, args.asset_id, args.version, args.kind, args.library)
        elif args.command == "import":
            value = import_asset(args.project, args.asset_id, args.version, args.alias, args.kind, args.library)
        elif args.command == "fork":
            value = fork_asset(args.project, args.alias, args.new_alias)
        else:
            value = {"version": "1.0", "bindings": list_bindings(args.project)}
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ShotError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "code": getattr(exc, "code", "INVALID_ASSET")}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
