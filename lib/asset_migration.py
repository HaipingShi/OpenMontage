"""Inventory and verified copy-only migration of legacy local production data.

No source is deleted or edited. A destination with different bytes is a
conflict, including on retry. Only explicit, scoped file references are
normalised; their original paths and hashes are retained in the report.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import uuid

import yaml


class MigrationError(ValueError):
    def __init__(self, code: str, message: str, report: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.report = report


_PATH_FIELDS = {"path", "asset", "asset_path", "abs_path", "image_path", "video_path", "audio_path",
    "output_path", "reference_image", "reference_image_path", "reference_images", "reference_image_paths",
    "image_url", "first_frame", "last_frame", "first_frame_path", "last_frame_path", "local_path", "master_path", "project_dir"}
_PROVENANCE_FIELDS = {"source_path", "original_path", "parent_asset", "directory", "_dir"}
_LOG_FILES = {"ledger.jsonl", "releases.jsonl"}
_STRUCTURED = {".json", ".yaml", ".yml"}
_TEXT_REFERENCES = {".js", ".mjs", ".jsx", ".ts", ".tsx", ".html", ".css", ".py", ".md", ".txt", ".toml", ".svg", ".xml"}
_PLAN_KEYS = ("version", "source_root", "data_root", "inventory", "inventory_sha256", "artists", "projects", "bindings", "rewrites", "warnings", "excluded")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _absolute(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or "\\" in str(path) or "\0" in str(path):
        raise MigrationError("UNSAFE_PATH", "Use an absolute path without traversal or backslashes")
    return path


def _parent_fd(path: Path, *, create: bool = False) -> int:
    """Walk using no-follow directory descriptors, including existing parents."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parent.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except OSError as error:
                try:
                    symlink = stat.S_ISLNK(os.stat(component, dir_fd=descriptor, follow_symlinks=False).st_mode)
                except FileNotFoundError:
                    symlink = False
                if symlink:
                    raise MigrationError("SYMLINK", f"Refusing a symlink parent: {path}") from error
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _check_path(path: Path, *, directory: bool = False, missing: bool = False) -> None:
    try:
        descriptor = _parent_fd(path)
    except FileNotFoundError:
        if missing:
            return
        raise MigrationError("MISSING_SOURCE", f"Missing path: {path}") from None
    try:
        try:
            mode = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False).st_mode
        except FileNotFoundError:
            if missing:
                return
            raise MigrationError("MISSING_SOURCE", f"Missing path: {path}") from None
        if stat.S_ISLNK(mode):
            raise MigrationError("SYMLINK", f"Refusing a symlink: {path}")
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            raise MigrationError("FILE_TYPE", f"Unsupported file type: {path}")
    finally:
        os.close(descriptor)


def _read(path: Path, *, content: bool = False) -> tuple[str, int, bytes | None]:
    parent = _parent_fd(path)
    try:
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise MigrationError("SYMLINK", f"Refusing a symlink: {path}") from error
            raise
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise MigrationError("FILE_TYPE", f"Unsupported file type: {path}")
        digest, length, chunks = hashlib.sha256(), 0, []
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            length += len(chunk)
            if content:
                chunks.append(chunk)
        after = os.fstat(stream.fileno())
        if before.st_size != length or before.st_mtime_ns != after.st_mtime_ns:
            raise MigrationError("SOURCE_CHANGED", f"File changed while reading: {path}")
        return digest.hexdigest(), length, b"".join(chunks) if content else None


def _roots(source_repo: str | Path, data_dir: str | Path) -> tuple[Path, Path]:
    source, data = _absolute(source_repo), _absolute(data_dir)
    if source == data or source.is_relative_to(data) or data.is_relative_to(source):
        raise MigrationError("OVERLAPPING_ROOTS", "Source and destination directories must be separate")
    _check_path(source, directory=True)
    _check_path(data, directory=True, missing=True)
    return source, data


def _tree(root: Path) -> list[Path]:
    _check_path(root, directory=True)
    files = []
    for current, directories, names in os.walk(root, followlinks=False):
        directories.sort()
        for name in directories:
            _check_path(Path(current) / name, directory=True)
        for name in sorted(names):
            path = Path(current) / name
            _check_path(path)
            files.append(path)
    return sorted(files)


def _document(path: Path, data: bytes) -> Any:
    try:
        return json.loads(data) if path.suffix == ".json" else yaml.safe_load(data)
    except (ValueError, yaml.YAMLError, UnicodeError) as error:
        raise MigrationError("INVALID_DOCUMENT", f"Cannot safely parse metadata: {path}") from error


def _provider_graph(document: Any) -> bool:
    return isinstance(document, dict) and bool(document) and all(
        isinstance(node_id, str) and node_id.isdigit() and isinstance(node, dict)
        and isinstance(node.get("class_type"), str) and isinstance(node.get("inputs"), dict)
        for node_id, node in document.items())


def _provider_metadata(document: Any) -> bool:
    return isinstance(document, dict) and bool(document) and all(
        isinstance(node, dict) and isinstance(node.get("input"), dict) and isinstance(node.get("output"), list)
        for node in document.values())


def _cast(value: Any, artists: set[str]) -> set[str]:
    selected: set[str] = set()
    if isinstance(value, dict):
        if _provider_graph(value) or _provider_metadata(value):
            return selected
        context = value.get("generation_context")
        if isinstance(context, dict) and "artist_ids" in context:
            ids = context["artist_ids"]
            if not isinstance(ids, list) or any(not isinstance(artist, str) or artist not in artists for artist in ids):
                raise MigrationError("UNKNOWN_ARTIST", "An explicit generation_context artist is absent from the source catalog")
            selected.update(ids)
        actions = value.get("character_actions")
        if isinstance(actions, list):
            selected.update(action["character_id"] for action in actions if isinstance(action, dict)
                            and action.get("character_id") in artists)
        for child in value.values():
            selected.update(_cast(child, artists))
    elif isinstance(value, list):
        for child in value:
            selected.update(_cast(child, artists))
    return selected


def _legacy_text(value: str, source: Path) -> bool:
    return any(str(source) + "/" + bucket + "/" in value for bucket in ("artists", "projects")) or bool(
        re.search(r"(?:^|[\"'\s(=:])(?:artists|projects)/[a-zA-Z0-9][a-zA-Z0-9_-]*/", value))


def _normalise(document: Any, *, source: Path, owner: Path, kind: str, artist_ids: set[str],
               bound: set[str], relative_file: str, rewrites: list[dict], warnings: list[dict]) -> Any:
    def warn(pointer: str) -> None:
        warnings.append({"code": "MANUAL_REVIEW_HISTORICAL_PATH", "file": relative_file, "pointer": pointer,
                         "message": "Historical text/source provenance was preserved; review before reusing it as an input."})

    def map_path(value: str, key: str, pointer: str, group: bool, artist_scope: str | None) -> str:
        expected = key in _PATH_FIELDS or group
        provenance = key in _PROVENANCE_FIELDS
        legacy = value == str(source) or value.startswith((str(source) + "/", "artists/", "projects/"))
        if not expected and not provenance:
            if legacy or _legacy_text(value, source):
                warn(pointer)
            return value
        if re.match(r"^(?:https?|s3|gs)://", value) or value.startswith("data:"):
            return value
        if "\\" in value or ".." in PurePosixPath(value).parts or value.startswith("file:"):
            if provenance:
                warn(pointer)
                return value
            raise MigrationError("UNSAFE_REFERENCE", f"Unsafe reference in {relative_file}{pointer}")
        replacement = value
        target = None
        if value == str(owner) or value.startswith(str(owner) + "/"):
            target = Path(value)
            replacement = target.relative_to(owner).as_posix()
        elif kind == "artist" and value.startswith(f"artists/{owner.name}/"):
            replacement = value[len(f"artists/{owner.name}/"):]
            target = owner / replacement
        elif kind == "project" and value.startswith(f"projects/{owner.name}/"):
            replacement = value[len(f"projects/{owner.name}/"):]
            target = owner / replacement
        else:
            artist_relative = value[len(str(source)) + 1:] if value.startswith(str(source) + "/") else value
            if kind == "project" and artist_relative.startswith("artists/"):
                parts = PurePosixPath(artist_relative).parts
                if len(parts) < 2 or parts[1] not in artist_ids:
                    if provenance:
                        warn(pointer)
                        return value
                    raise MigrationError("UNKNOWN_ARTIST_REFERENCE", f"Unknown artist reference in {relative_file}{pointer}")
                bound.add(parts[1])
                target = source / artist_relative
                replacement = "assets/" + artist_relative
            elif legacy or value.startswith("/"):
                if provenance:
                    warn(pointer)
                    return value
                raise MigrationError("UNMAPPABLE_REFERENCE", f"Reference is outside this asset/project in {relative_file}{pointer}")
            elif kind == "project" and artist_scope and key == "path" and value and not value.startswith(("assets/", "artifacts/", "renders/", "shots/")):
                bound.add(artist_scope)
                target = source / "artists" / artist_scope / value
                replacement = f"assets/artists/{artist_scope}/{value}"
            elif expected and value and (kind == "artist" or key not in {"output_path", "project_dir"}):
                target = owner / value
        if target is not None:
            if target.is_dir():
                _check_path(target, directory=True)
            else:
                try:
                    _check_path(target)
                except MigrationError as error:
                    if provenance:
                        warn(pointer)
                        return value
                    if error.code == "MISSING_SOURCE":
                        raise MigrationError("DANGLING_REFERENCE", f"Missing referenced source in {relative_file}{pointer}") from error
                    raise
        if replacement != value:
            rewrites.append({"file": relative_file, "pointer": pointer, "original_path": value, "destination_path": replacement})
        return replacement

    def visit(value: Any, key: str = "", pointer: str = "", group: bool = False, artist_scope: str | None = None) -> Any:
        if isinstance(value, str):
            return map_path(value, key, pointer, group, artist_scope)
        if isinstance(value, list):
            return [visit(child, key, f"{pointer}/{index}", group, artist_scope) for index, child in enumerate(value)]
        if isinstance(value, dict):
            if kind == "project" and _provider_metadata(value):
                warnings.append({"code": "MANUAL_REVIEW_PROVIDER_METADATA", "file": relative_file, "pointer": pointer,
                                 "message": "Provider schema was preserved; review against the provider before reuse."})
                return value
            if kind == "project" and _provider_graph(value):
                warnings.append({"code": "MANUAL_REVIEW_PROVIDER_GRAPH", "file": relative_file, "pointer": pointer,
                                 "message": "Provider graph was preserved; review node links and provider-local inputs before reuse."})
                return value
            artist_field = next((field for field in ("artist_id", "base_artist_id") if field in value), None)
            if artist_field is None and isinstance(value.get("identity"), dict) and isinstance(value["identity"].get("bible"), list):
                artist_field = "id"
            if artist_field:
                explicit_artist = value[artist_field]
                artist_scope = explicit_artist if isinstance(explicit_artist, str) and explicit_artist in artist_ids else None
            return {name: visit(child, str(name), f"{pointer}/{str(name).replace('~', '~0').replace('/', '~1')}", group or name == "reference_map", artist_scope)
                    for name, child in value.items()}
        return value

    return visit(document)


def _archived_inputs(document: Any, source: Path, relative_file: str, warnings: list[dict]) -> None:
    """Inventory historical inputs without resolving them to today's asset version."""
    def visit(value: Any, key: str = "", pointer: str = "", group: bool = False) -> None:
        if isinstance(value, str):
            if key in _PATH_FIELDS | _PROVENANCE_FIELDS or group or _legacy_text(value, source):
                warnings.append({"code": "MANUAL_REVIEW_ARCHIVED_INPUT", "file": relative_file, "pointer": pointer,
                                 "message": "Archived input was preserved; verify its original asset version before reuse."})
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, key, f"{pointer}/{index}", group)
        elif isinstance(value, dict):
            for name, child in value.items():
                visit(child, str(name), f"{pointer}/{str(name).replace('~', '~0').replace('/', '~1')}", group or name == "reference_map")
    visit(document)


def _prepare(source_repo: str | Path, data_dir: str | Path) -> tuple[dict, dict[str, bytes]]:
    source, data = _roots(source_repo, data_dir)
    source_artists, source_projects = source / "artists", source / "projects"
    catalog = []
    if source_artists.exists() or source_artists.is_symlink():
        _check_path(source_artists, directory=True)
        for child in sorted(source_artists.iterdir()):
            if child.name == "_template":
                continue
            _check_path(child, directory=child.is_dir())
            if child.is_dir() and (child / "artist.yaml").is_file():
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", child.name):
                    raise MigrationError("UNSAFE_ARTIST_ID", "Artist directory is not a valid identifier")
                catalog.append(child)
    projects = []
    if source_projects.exists() or source_projects.is_symlink():
        _check_path(source_projects, directory=True)
        for child in sorted(source_projects.iterdir()):
            _check_path(child, directory=child.is_dir())
            if child.is_dir():
                projects.append(child)
    names = {artist.name for artist in catalog}
    entries, rewrites, warnings, transformed, bindings = [], [], [], {}, []
    selected_by_project: dict[str, set[str]] = {}
    for kind, owners in (("artist", catalog), ("project", projects)):
        for owner in owners:
            selected = set()
            for path in _tree(owner):
                relative = path.relative_to(source).as_posix()
                sha256, size, raw = _read(path, content=path.suffix in _STRUCTURED | _TEXT_REFERENCES)
                destination = (PurePosixPath("library/artists") if kind == "artist" else PurePosixPath("projects")) / owner.name / path.relative_to(owner).as_posix()
                destination_bytes = None
                if raw is not None and path.suffix in _TEXT_REFERENCES:
                    if _legacy_text(raw.decode("utf-8", errors="replace"), source):
                        warnings.append({"code": "MANUAL_REVIEW_UNSTRUCTURED_PATH", "file": relative,
                                         "message": "Code/text was preserved; review historical paths before reusing this file."})
                if raw is not None and path.suffix in _STRUCTURED:
                    document = _document(path, raw)
                    archived = kind == "project" and path.relative_to(owner).parts[0] == "history"
                    provider_graph = kind == "project" and _provider_graph(document)
                    provider_metadata = kind == "project" and _provider_metadata(document)
                    if archived:
                        _archived_inputs(document, source, relative, warnings)
                    elif provider_metadata:
                        warnings.append({"code": "MANUAL_REVIEW_PROVIDER_METADATA", "file": relative,
                                         "message": "Provider schema was preserved; review against the provider before reuse."})
                    elif provider_graph:
                        warnings.append({"code": "MANUAL_REVIEW_PROVIDER_GRAPH", "file": relative,
                                         "message": "Provider graph was preserved; review node links and provider-local inputs before reuse."})
                    elif kind == "project":
                        selected.update(_cast(document, names))
                    before_rewrites = len(rewrites)
                    normalised = document if archived or provider_graph or provider_metadata else _normalise(document, source=source, owner=owner, kind=kind, artist_ids=names,
                                                                     bound=selected, relative_file=relative, rewrites=rewrites, warnings=warnings)
                    if len(rewrites) != before_rewrites:
                        destination_bytes = _json_bytes(normalised) if path.suffix == ".json" else yaml.safe_dump(normalised, allow_unicode=True, sort_keys=False).encode("utf-8")
                        transformed[relative] = destination_bytes
                entries.append({"source_path": relative, "destination_path": destination.as_posix(), "source_sha256": sha256,
                                "size_bytes": size, "destination_sha256": hashlib.sha256(destination_bytes).hexdigest() if destination_bytes is not None else sha256})
            if kind == "project":
                selected_by_project[owner.name] = selected
    entries.sort(key=lambda entry: entry["source_path"])
    versions = {}
    for artist in catalog:
        prefix = f"artists/{artist.name}/"
        files = [{"path": entry["source_path"][len(prefix):], "sha256": entry["destination_sha256"]}
                 for entry in entries if entry["source_path"].startswith(prefix) and entry["source_path"][len(prefix):] not in _LOG_FILES]
        versions[artist.name] = "sha256-" + _digest(files)
    for project in projects:
        for artist in sorted(selected_by_project[project.name]):
            if (project / "assets/artists" / artist).exists():
                raise MigrationError("BINDING_CONFLICT", f"Legacy project already owns an artist snapshot: {project.name}/{artist}")
            bindings.append({"project": project.name, "asset_id": artist, "alias": artist, "kind": "artist", "version": versions[artist]})
    plan = {"version": "1.0", "source_root": str(source), "data_root": str(data), "inventory": entries,
            "inventory_sha256": _digest(entries), "artists": [{"asset_id": name, "version": versions[name]} for name in sorted(versions)],
            "projects": [project.name for project in projects], "bindings": bindings, "rewrites": rewrites, "warnings": warnings,
            "excluded": ["artists/_template", "artists top-level non-profile files"]}
    return plan, transformed


def inventory(source_repo: str | Path, data_dir: str | Path) -> dict:
    """Return a complete, content-free source inventory and explicit rewrite plan."""
    return _prepare(source_repo, data_dir)[0]


def _preflight(plan: dict) -> None:
    data = Path(plan["data_root"])
    for entry in plan["inventory"]:
        target = data / entry["destination_path"]
        _check_path(target, missing=True)
        if target.exists() and _read(target)[0] != entry["destination_sha256"]:
            raise MigrationError("DESTINATION_CONFLICT", f"Destination has different bytes: {entry['destination_path']}")
    for binding in plan["bindings"]:
        project = data / "projects" / binding["project"]
        target = project / "assets/artists" / binding["alias"]
        _check_path(target, directory=True, missing=True)
        if target.exists():
            from lib.project_assets import load_project_artist
            load_project_artist(project, binding["alias"])
            existing = _document(project / "assets/bindings.json", _read(project / "assets/bindings.json", content=True)[2])
            match = next((item for item in existing["bindings"] if item["alias"] == binding["alias"]), None)
            identity_path = data / "library/library.json"
            _check_path(identity_path, missing=True)
            identity = _document(identity_path, _read(identity_path, content=True)[2]) if identity_path.exists() else {}
            if (not match or match.get("mode") != "imported" or not isinstance(identity, dict)
                    or identity.get("version") != "1.0" or match.get("library_id") != identity.get("library_id")
                    or any(match.get(key) != binding[key] for key in ("asset_id", "version", "kind"))):
                raise MigrationError("BINDING_CONFLICT", f"Destination artist alias is already owned: {binding['project']}/{binding['alias']}")


def _copy(entry: dict, source: Path, data: Path, transformed: bytes | None) -> bool:
    target = data / entry["destination_path"]
    if target.exists():
        if _read(target)[0] != entry["destination_sha256"]:
            raise MigrationError("DESTINATION_CONFLICT", f"Destination changed: {entry['destination_path']}")
        return False
    descriptor = _parent_fd(target, create=True)
    temporary = f".migration-{uuid.uuid4().hex}.tmp"
    created = False
    try:
        out_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=descriptor)
        created = True
        with os.fdopen(out_fd, "wb") as output:
            if transformed is not None:
                if _read(source / entry["source_path"])[0] != entry["source_sha256"]:
                    raise MigrationError("SOURCE_CHANGED", f"Source changed before copy: {entry['source_path']}")
                output.write(transformed)
            else:
                parent = _parent_fd(source / entry["source_path"])
                try:
                    in_fd = os.open(Path(entry["source_path"]).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                finally:
                    os.close(parent)
                with os.fdopen(in_fd, "rb") as input_file:
                    if not stat.S_ISREG(os.fstat(input_file.fileno()).st_mode):
                        raise MigrationError("FILE_TYPE", f"Source is no longer a regular file: {entry['source_path']}")
                    source_hash = hashlib.sha256()
                    while chunk := input_file.read(1024 * 1024):
                        source_hash.update(chunk)
                        output.write(chunk)
                    if source_hash.hexdigest() != entry["source_sha256"]:
                        raise MigrationError("SOURCE_CHANGED", f"Source changed during copy: {entry['source_path']}")
            output.flush()
            os.fsync(output.fileno())
        if _read(target.parent / temporary)[0] != entry["destination_sha256"]:
            raise MigrationError("COPY_VERIFICATION", f"Copied bytes do not match inventory: {entry['destination_path']}")
        try:
            os.link(temporary, target.name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        except FileExistsError:
            if _read(target)[0] != entry["destination_sha256"]:
                raise MigrationError("DESTINATION_CONFLICT", f"Destination appeared during copy: {entry['destination_path']}") from None
            return False
        return True
    finally:
        if created:
            os.unlink(temporary, dir_fd=descriptor)
        os.close(descriptor)


def _save_report(report: dict, data: Path) -> dict:
    path = data / "migration-reports" / f"migration-{uuid.uuid4().hex}.json"
    descriptor = _parent_fd(path, create=True)
    report["report_path"] = str(path)
    try:
        out_fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        with os.fdopen(out_fd, "wb") as stream:
            stream.write(_json_bytes(report))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    return report


def apply_plan(plan: dict) -> dict:
    """Revalidate an inventory, preflight every target, then copy and bind."""
    data = _absolute(plan["data_root"])
    report = {**plan, "applied": True, "status": "failed", "copied_files": 0, "verified_files": 0}
    try:
        fresh, transformed = _prepare(plan["source_root"], data)
        if fresh["inventory_sha256"] != plan["inventory_sha256"]:
            raise MigrationError("SOURCE_CHANGED", "Source inventory no longer matches the approved plan")
        if any(fresh[key] != plan[key] for key in _PLAN_KEYS):
            raise MigrationError("PLAN_CHANGED", "The plan no longer matches its source and scoped destinations")
        _preflight(fresh)
        source = Path(fresh["source_root"])
        for entry in fresh["inventory"]:
            report["copied_files"] += _copy(entry, source, data, transformed.get(entry["source_path"]))
            if _read(data / entry["destination_path"])[0] != entry["destination_sha256"]:
                raise MigrationError("COPY_VERIFICATION", f"Destination verification failed: {entry['destination_path']}")
            report["verified_files"] += 1
        if inventory(source, data)["inventory_sha256"] != fresh["inventory_sha256"]:
            raise MigrationError("SOURCE_CHANGED", "Source changed before immutable versions were published")
        from lib.project_assets import import_asset, publish_asset
        library = data / "library"
        for artist in fresh["artists"]:
            publish_asset(library / "artists" / artist["asset_id"], artist["asset_id"], artist["version"], kind="artist", library=library)
        for binding in fresh["bindings"]:
            import_asset(data / "projects" / binding["project"], binding["asset_id"], binding["version"],
                         binding["alias"], kind="artist", library=library)
        report["status"] = "completed"
        return _save_report(report, data)
    except Exception as error:
        failure = error if isinstance(error, MigrationError) else MigrationError("MIGRATION_FAILED", str(error))
        report["error"] = {"code": failure.code, "message": str(failure)}
        try:
            _roots(plan["source_root"], data)
            _save_report(report, data)
        except (OSError, MigrationError):
            pass
        failure.report = report
        raise failure from error


def migrate(source_repo: str | Path, data_dir: str | Path, *, apply: bool = False) -> dict:
    """Dry-run by default; writes only a content-free report until --apply."""
    try:
        plan = inventory(source_repo, data_dir)
    except Exception as error:
        failure = error if isinstance(error, MigrationError) else MigrationError("MIGRATION_FAILED", str(error))
        report = {"version": "1.0", "source_root": str(source_repo), "data_root": str(data_dir), "applied": apply,
                  "status": "failed", "error": {"code": failure.code, "message": str(failure)}}
        try:
            _roots(source_repo, data_dir)
            _save_report(report, _absolute(data_dir))
        except (OSError, MigrationError):
            pass
        failure.report = report
        raise failure from error
    return apply_plan(plan) if apply else _save_report({**plan, "applied": False, "status": "planned"}, Path(plan["data_root"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("migrate", help="Inventory and copy legacy data; defaults to dry-run")
    command.add_argument("source_repo")
    command.add_argument("--data-dir", required=True)
    command.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = migrate(args.source_repo, args.data_dir, apply=args.apply)
        code = 0
    except MigrationError as error:
        result, code = error.report or {"status": "failed", "error": str(error)}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
