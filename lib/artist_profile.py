"""Persistent digital artists shared across projects.

Reusable artists live in the user's asset catalog, by default
``~/OpenMontageData/library/artists/<id>/``. Project-private copies live at
``<project>/assets/artists/<id>/``. There is no repository catalog fallback.

    artist.yaml     identity, persona, voice, music, rights (schema: artist_profile)
    bible/          locked identity reference images
    voice/          canonical voice reference tracks
    ledger.jsonl    append-only provenance log (who made which asset, with what)
    releases.jsonl  append-only list of shipped works

Pipelines call ``identity_prompt`` and ``music_style`` instead of writing
their own descriptions, so every release starts from the same canon.

CLI:
    python -m lib.artist_profile list
    python -m lib.artist_profile check <id>
    python -m lib.artist_profile prompt <id> "<shot description>" [--look <look_id>]
    python -m lib.artist_profile style <id> ["<song-specific additions>"]
    python -m lib.artist_profile cast <id>,<id> "<shot description>"
    python -m lib.artist_profile duet-style <id>,<id> ["<song-specific additions>"]

Scope options precede the command: ``--root /absolute/catalog list`` selects
an explicit catalog; ``--project <project-id> prompt <alias> "<shot>"`` selects
that project's local assets. Omitting both uses the user catalog from lib.paths.
Project imports are verified immutable snapshots; voice edits require an
explicit private fork, while private project profiles remain editable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any

import yaml

from schemas.artifacts import validate_artifact
from lib.paths import ARTISTS_DIR
STYLE_LIMIT = 200
LEDGER_REQUIRED = ("asset", "tool", "human_input")
RELEASE_REQUIRED = ("title", "project_id")


def artist_dir(artist_id: str, root: Path | None = None) -> Path:
    if not isinstance(artist_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", artist_id):
        raise ValueError("artist_id must be a lowercase directory name, without path separators")
    base = (root or ARTISTS_DIR).resolve()
    path = (base / artist_id).resolve()
    if not path.is_relative_to(base):
        raise ValueError("artist directory escapes artists root")
    return path


def _asset_path(base: Path, relative: str) -> Path:
    """Reject absolute paths, traversal, and symlinks that leave the artist directory."""
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or "\\" in relative:
        raise ValueError(f"unsafe artist asset path: {relative}")
    path = (base / part).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"artist asset escapes directory: {relative}")
    return path


def list_artists(root: Path | None = None) -> list[str]:
    base = root or ARTISTS_DIR
    if not base.is_dir():
        return []
    return sorted(
        p.name for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "artist.yaml").is_file()
    )


def load_artist(artist_id: str, root: Path | None = None, validate: bool = True) -> dict[str, Any]:
    path = _asset_path(artist_dir(artist_id, root), "artist.yaml")
    if not path.is_file():
        raise FileNotFoundError(f"No artist profile at {path}")
    with open(path, encoding="utf-8") as f:
        profile = yaml.safe_load(f)
    if validate:
        validate_artifact("artist_profile", profile)
    if profile.get("id") != artist_id:
        raise ValueError(f"artist.yaml id '{profile.get('id')}' does not match directory '{artist_id}'")
    profile["_dir"] = str(path.parent)
    return profile


def _look(profile: dict[str, Any], look_id: str | None) -> dict[str, Any]:
    looks = profile["identity"]["looks"]
    if look_id is None:
        return looks[0]
    for look in looks:
        if look["id"] == look_id:
            return look
    raise KeyError(f"Unknown look '{look_id}'. Available: {[l['id'] for l in looks]}")


def bible_assets(
    profile: dict[str, Any],
    look: str | None = None,
    canonical_only: bool = True,
) -> list[dict[str, Any]]:
    """Bible entries with absolute paths. Canonical front views first."""
    base = Path(profile["_dir"])
    order = {"front": 0, "turnaround": 1, "three_quarter": 2, "profile": 3, "full_body": 4}
    entries = [
        {**entry, "abs_path": str(_asset_path(base, entry["path"]))}
        for entry in profile["identity"]["bible"]
        if (look is None or entry["look"] == look)
        and ((entry["canonical"] and entry.get("reference_role", "identity") == "identity") or not canonical_only)
    ]
    return sorted(entries, key=lambda e: (not e["canonical"], order.get(e["view"], 9)))


def identity_prompt(profile: dict[str, Any], shot: str, look: str | None = None) -> dict[str, Any]:
    """Prompt pieces for one keyframe: identity + wardrobe + shot, and the references to attach."""
    chosen = _look(profile, look)
    identity = profile["identity"]
    prompt = (
        f"{identity['prompt_core'].strip()} "
        f"Wardrobe: {chosen['wardrobe'].strip()} "
        f"Shot: {shot.strip()} "
        "Original fictional character; match the attached reference images exactly."
    )
    return {
        "prompt": prompt,
        "negative_prompt": identity.get("negative_prompt", ""),
        "reference_images": [e["abs_path"] for e in bible_assets(profile, look=chosen["id"])],
        "supporting_references": [
            e for e in bible_assets(profile, look=chosen["id"], canonical_only=False)
            if e.get("reference_role") == "wardrobe_proportions"
        ],
        "look": chosen["id"],
        "locked_traits": list(identity["locked_traits"]),
    }


def music_style(profile: dict[str, Any], extra: str = "") -> str:
    """Generator style string: fixed signature first, song-specific additions only if they fit."""
    signature = profile["voice"]["style_signature"].strip()
    extra = extra.strip().strip(",").strip()
    if not extra:
        return signature
    room = STYLE_LIMIT - len(signature) - 2
    if room <= 0:
        return signature
    return f"{signature}, {extra[:room].rstrip(', ')}"


def _collaboration(profile: dict[str, Any], other_id: str) -> dict[str, Any]:
    for entry in profile.get("collaborators") or []:
        if entry["artist_id"] == other_id:
            return entry
    raise ValueError(f"'{profile['id']}' has no collaborators entry for '{other_id}'")


def cast_prompt(
    profiles: list[dict[str, Any]],
    shot: str,
    looks: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Prompt pieces for a keyframe with several persistent artists in one frame.

    Each person is described by their own canon and keeps their own references, so the
    generator is told who is who instead of averaging two faces.
    """
    if len(profiles) < 2:
        raise ValueError("cast_prompt needs at least two artists; use identity_prompt for one")
    looks = looks or {}
    people = [identity_prompt(p, shot, looks.get(p["id"])) | {"artist_id": p["id"]} for p in profiles]
    described = " ".join(
        f"{p['names'].get('latin') or p['names']['stage']}: {p['identity']['prompt_core'].strip()} "
        f"Wardrobe: {_look(p, looks.get(p['id']))['wardrobe'].strip()}"
        for p in profiles
    )
    negatives = [person["negative_prompt"] for person in people if person["negative_prompt"]]
    negatives.append("merged faces, swapped outfits, identical twins, extra people")
    return {
        "prompt": (
            f"{len(profiles)} original fictional people in one frame. {described} "
            f"Shot: {shot.strip()} "
            "Each person must match their own attached reference images; do not blend their features."
        ),
        "negative_prompt": ", ".join(negatives),
        "reference_images": [path for person in people for path in person["reference_images"]],
        "reference_map": {person["artist_id"]: person["reference_images"] for person in people},
        "locked_traits": {person["artist_id"]: person["locked_traits"] for person in people},
        "looks": {person["artist_id"]: person["look"] for person in people},
        "supporting_references": {person["artist_id"]: person["supporting_references"] for person in people},
    }


def duet_style(profiles: list[dict[str, Any]], extra: str = "") -> str:
    """Style string for a joint song: shared prefix from the first artist's collaborators entry,
    then every artist's duet_descriptor, then song-specific additions if they fit."""
    if len(profiles) < 2:
        raise ValueError("duet_style needs at least two artists; use music_style for one")
    lead = profiles[0]
    shared = _collaboration(lead, profiles[1]["id"])["duet_style"].strip()
    voices = []
    for profile in profiles:
        descriptor = (profile["voice"].get("duet_descriptor") or "").strip()
        if not descriptor:
            raise ValueError(f"'{profile['id']}' has no voice.duet_descriptor")
        voices.append(descriptor)
    fixed = ", ".join([shared, *voices])
    if len(fixed) > STYLE_LIMIT:
        raise ValueError(f"duet style without additions is {len(fixed)} chars (limit {STYLE_LIMIT})")
    extra = extra.strip().strip(",").strip()
    room = STYLE_LIMIT - len(fixed) - 2
    if not extra or room <= 0:
        return fixed
    return f"{fixed}, {extra[:room].rstrip(', ')}"


def voice_readiness(profile: dict[str, Any]) -> dict[str, Any]:
    """Report evidence and route capabilities separately; a style prompt is not a voice binding."""
    voice = profile["voice"]
    binding = voice.get("provider_binding") or {}
    external = bool(
        voice["provider"] == "suno" and binding.get("confirmed")
        and (binding.get("name") or binding.get("url") or voice.get("persona_id"))
    )
    tracks = voice.get("canonical_tracks") or []
    local = bool(tracks) and voice["lock_status"] == "locked"
    for track in tracks:
        try:
            path = _asset_path(Path(profile["_dir"]), track["path"])
            local = local and path.is_file()
            if path.is_file() and track.get("sha256"):
                local = local and _sha256(path) == track["sha256"]
        except ValueError:
            local = False
    return {
        "lock_status": voice["lock_status"],
        "external_binding_confirmed": external,
        "local_reference_ready": local,
        "generation_routes": {
            "suno_web": "manual_selection_required" if external else "binding_required",
            "suno_music": "unsupported_voice_reference",
        },
    }


def check_artist(profile: dict[str, Any], voice_route: str = "local_reference") -> list[str]:
    """Problems that block using this artist in a production. Empty list = ready."""
    issues: list[str] = []
    if voice_route not in ("local_reference", "suno_web", "suno_music", "visual_only"):
        raise ValueError(f"Unknown voice route: {voice_route}")
    if profile["status"] == "retired":
        issues.append("artist is retired")
    base = Path(profile["_dir"])
    look_ids = {l["id"] for l in profile["identity"]["looks"]}

    for entry in profile["identity"]["bible"]:
        try:
            path = _asset_path(base, entry["path"])
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not path.is_file():
            issues.append(f"bible image missing: {entry['path']}")
        if entry["look"] not in look_ids:
            issues.append(f"bible image {entry['path']} uses unknown look '{entry['look']}'")
    for look_id in look_ids:
        if not any(e["look"] == look_id and e["canonical"] and e.get("reference_role", "identity") == "identity" for e in profile["identity"]["bible"]):
            issues.append(f"look '{look_id}' has no canonical bible image")

    voice = profile["voice"]
    if len(voice["style_signature"]) > STYLE_LIMIT:
        issues.append(f"voice.style_signature exceeds {STYLE_LIMIT} chars")
    state = voice_readiness(profile)
    if voice_route == "local_reference" and not state["local_reference_ready"]:
        issues.append("voice local reference not ready: import a user-approved recording and lock it")
    if voice_route == "suno_web" and not state["external_binding_confirmed"]:
        issues.append("Suno voice binding required: record and confirm the existing Persona/Voice name or URL")
    if voice_route == "suno_music":
        issues.append("suno_music cannot transmit a voice reference or a Suno website Persona; use the approved Suno web workflow")
    if voice["lock_status"] == "locked" and not voice.get("canonical_tracks"):
        issues.append("voice is locked but has no canonical_tracks")
    for track in voice.get("canonical_tracks") or []:
        try:
            path = _asset_path(base, track["path"])
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not path.is_file():
            issues.append(f"canonical voice track missing: {track['path']}")
        elif track.get("sha256") and _sha256(path) != track["sha256"]:
            issues.append(f"canonical voice track checksum mismatch: {track['path']}")
    if voice["source"] in ("licensed_human", "licensed_clone") and not voice.get("license_holder"):
        issues.append("licensed voice requires voice.license_holder")

    for entry in profile.get("collaborators") or []:
        other = _asset_path(artist_dir(entry["artist_id"], base.parent), "artist.yaml")
        if not other.is_file():
            issues.append(f"collaborator '{entry['artist_id']}' has no profile in artists/")
        if not voice.get("duet_descriptor"):
            issues.append("artist has collaborators but no voice.duet_descriptor")

    return issues


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def record_ledger(artist_id: str, entry: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Append a provenance record. ``human_input`` documents the creative choices a person made."""
    profile = load_artist(artist_id, root)
    if not isinstance(entry, Mapping):
        raise ValueError("ledger entry must be a mapping")
    missing = [k for k in LEDGER_REQUIRED if not entry.get(k)]
    if missing:
        raise ValueError(f"ledger entry missing {missing}")
    record = {"date": date.today().isoformat(), **entry}
    _append_jsonl(_asset_path(Path(profile["_dir"]), "ledger.jsonl"), record)
    return record


def record_release(artist_id: str, release: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    profile = load_artist(artist_id, root)
    if not isinstance(release, Mapping):
        raise ValueError("release must be a mapping with title and project_id values")
    missing = [k for k in RELEASE_REQUIRED if not release.get(k)]
    if missing:
        raise ValueError(f"release missing {missing}")
    record = {"date": date.today().isoformat(), "identity_version": profile["version"], **release}
    _append_jsonl(_asset_path(Path(profile["_dir"]), "releases.jsonl"), record)
    return record


def read_ledger(artist_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    return _read_jsonl(_asset_path(artist_dir(artist_id, root), "ledger.jsonl"))


def read_releases(artist_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    return _read_jsonl(_asset_path(artist_dir(artist_id, root), "releases.jsonl"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_artist(profile: dict[str, Any]) -> None:
    raw = {key: value for key, value in profile.items() if key != "_dir"}
    validate_artifact("artist_profile", raw)
    base = Path(profile["_dir"])
    target = _asset_path(base, "artist.yaml")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=base, delete=False) as stream:
        temp = Path(stream.name)
        yaml.safe_dump(raw, stream, allow_unicode=True, sort_keys=False)
    try:
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def _artist_edit_scope(artist_id: str, root: Path | None):
    """Serialize project edits and enforce imported immutability at the write API."""
    base = Path(root or ARTISTS_DIR).expanduser().resolve()
    if base.name == "artists" and base.parent.name == "assets":
        from lib.project_assets import verify_binding
        from lib.shot_spec import transaction
        project = base.parent.parent
        with transaction(project, "project-assets"):
            binding = verify_binding(project, artist_id)
            if binding is not None and binding.get("mode") == "imported":
                raise ValueError("Imported artist snapshots are immutable; fork this asset to a private alias before editing")
            yield base
    else:
        yield base


def _guard_artist_edit(function):
    @wraps(function)
    def guarded(artist_id, *args, **kwargs):
        with _artist_edit_scope(artist_id, kwargs.get("root")) as root:
            kwargs["root"] = root
            return function(artist_id, *args, **kwargs)
    return guarded


@_guard_artist_edit
def bind_suno_voice(
    artist_id: str, *, kind: str, name: str = "", url: str = "",
    source_song_url: str = "", confirmed: bool = False, root: Path | None = None,
) -> dict[str, Any]:
    """Save a user-selected website binding. It is never an API persona credential."""
    profile = load_artist(artist_id, root)
    if profile["voice"]["provider"] != "suno":
        raise ValueError("Suno binding requires voice.provider=suno")
    if kind not in ("persona", "voice") or not (name.strip() or url.strip()):
        raise ValueError("binding requires kind=persona/voice and a name or URL")
    for value in (url, source_song_url):
        if value and not re.fullmatch(r"https://(?:www\.)?suno\.com/[^\s]+", value):
            raise ValueError("binding URLs must be HTTPS links on suno.com")
    binding = {
        "kind": kind, "name": name.strip(), "url": url.strip(),
        "source_song_url": source_song_url.strip(), "confirmed": confirmed,
        "confirmed_on": date.today().isoformat() if confirmed else None,
    }
    profile["voice"]["provider_binding"] = binding
    if confirmed and profile["voice"]["lock_status"] != "locked":
        profile["voice"]["lock_status"] = "provider_bound"
    elif not confirmed and profile["voice"]["lock_status"] == "provider_bound":
        profile["voice"]["lock_status"] = "pending_audition"
    _save_artist(profile)
    record_ledger(artist_id, {
        "asset": "voice.provider_binding", "tool": "suno_web",
        "human_input": "User confirmed existing Suno voice" if confirmed else "Recorded unconfirmed Suno binding",
        "binding": binding,
    }, root)
    return binding


@_guard_artist_edit
def import_voice_reference(
    artist_id: str, source: Path, *, approved: bool = False,
    source_url: str = "", notes: str = "", root: Path | None = None,
) -> dict[str, Any]:
    """Copy a decodable, user-approved audio export into the permanent voice library."""
    if not approved:
        raise ValueError("voice import requires approval of the selected recording")
    profile = load_artist(artist_id, root)
    source = Path(source).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
        raise ValueError("voice reference must be an audio file: WAV, MP3, FLAC, M4A, or OGG")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("ffprobe is required to verify the audio export")
    # Snapshot first, then probe and hash exactly the bytes that will be saved.
    base = Path(profile["_dir"])
    voice_dir = _asset_path(base, "voice")
    voice_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=voice_dir, suffix=source.suffix.lower(), delete=False) as stream:
        temp = Path(stream.name)
    try:
        shutil.copyfile(source, temp)
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_type,sample_rate:format=duration", "-of", "json", str(temp)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode:
            raise ValueError("voice reference is not decodable audio")
        info = json.loads(result.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        if not info.get("streams") or not math.isfinite(duration) or duration <= 0:
            raise ValueError("voice reference has no audio stream or positive duration")
        checksum = _sha256(temp)
        relative = f"voice/canonical_{checksum[:16]}{source.suffix.lower()}"
        target = _asset_path(base, relative)
        if target.exists():
            if _sha256(target) != checksum:
                raise ValueError("existing reference differs; refusing to overwrite")
        else:
            temp.replace(target)
        track = {
            "path": relative, "sha256": checksum, "duration_seconds": duration,
            "source_url": source_url, "notes": notes, "approved_on": date.today().isoformat(),
        }
        tracks = profile["voice"].setdefault("canonical_tracks", [])
        if not any(t["path"] == relative for t in tracks):
            tracks.append(track)
        profile["voice"]["lock_status"] = "locked"
        if profile["status"] == "draft":
            profile["status"] = "active"
        _save_artist(profile)
        record_ledger(artist_id, {
            "asset": relative, "tool": "local_voice_import", "sha256": checksum,
            "source_url": source_url, "human_input": notes or "User selected and approved this canonical voice recording",
        }, root)
        return track
    finally:
        temp.unlink(missing_ok=True)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m lib.artist_profile")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--root", type=Path, help="Explicit artist catalog root (before the command)")
    scope.add_argument("--project", help="Project id; use only its assets/artists catalog (before the command)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_check = sub.add_parser("check")
    p_check.add_argument("artist_id")
    p_check.add_argument("--voice-route", choices=["local_reference", "suno_web", "suno_music", "visual_only"], default="local_reference")
    p_bind = sub.add_parser("bind-suno")
    p_bind.add_argument("artist_id")
    p_bind.add_argument("--kind", choices=["persona", "voice"], required=True)
    p_bind.add_argument("--name", default="")
    p_bind.add_argument("--url", default="")
    p_bind.add_argument("--source-song-url", default="")
    p_bind.add_argument("--confirm", action="store_true")
    p_import = sub.add_parser("import-voice")
    p_import.add_argument("artist_id")
    p_import.add_argument("source", type=Path)
    p_import.add_argument("--approve", action="store_true")
    p_import.add_argument("--source-url", default="")
    p_import.add_argument("--notes", default="")
    p_prompt = sub.add_parser("prompt")
    p_prompt.add_argument("artist_id")
    p_prompt.add_argument("shot")
    p_prompt.add_argument("--look")
    p_style = sub.add_parser("style")
    p_style.add_argument("artist_id")
    p_style.add_argument("extra", nargs="?", default="")
    p_cast = sub.add_parser("cast")
    p_cast.add_argument("artist_ids", help="comma-separated artist ids or project aliases")
    p_cast.add_argument("shot")
    p_duet = sub.add_parser("duet-style")
    p_duet.add_argument("artist_ids", help="comma-separated artist ids or project aliases")
    p_duet.add_argument("extra", nargs="?", default="")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve() if args.root else None
    selected_project = None
    if args.project:
        from lib.shot_spec import project_path, safe_path
        selected_project = project_path(args.project)
        root = safe_path(selected_project, "assets/artists")

    def scoped_artist(artist_id: str) -> dict[str, Any]:
        if selected_project is not None:
            from lib.project_assets import load_project_artist
            return load_project_artist(selected_project, artist_id)
        return load_artist(artist_id, root)

    if args.cmd == "bind-suno":
        print(json.dumps(bind_suno_voice(args.artist_id, kind=args.kind, name=args.name, url=args.url,
            source_song_url=args.source_song_url, confirmed=args.confirm, root=root), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "import-voice":
        print(json.dumps(import_voice_reference(args.artist_id, args.source, approved=args.approve,
            source_url=args.source_url, notes=args.notes, root=root), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "list":
        for artist_id in list_artists(root):
            profile = scoped_artist(artist_id)
            print(f"{artist_id}\t{profile['names']['stage']}\t{profile['status']}\tv{profile['version']}")
        return 0
    if args.cmd in ("cast", "duet-style"):
        profiles = [scoped_artist(a.strip()) for a in args.artist_ids.split(",") if a.strip()]
        if args.cmd == "cast":
            print(json.dumps(cast_prompt(profiles, args.shot), ensure_ascii=False, indent=2))
            return 0
        style = duet_style(profiles, args.extra)
        print(json.dumps({"style": style, "chars": len(style)}, ensure_ascii=False))
        return 0

    profile = scoped_artist(args.artist_id)
    if args.cmd == "check":
        issues = check_artist(profile, args.voice_route)
        print(json.dumps({"artist": args.artist_id, "voice_route": args.voice_route,
            "ready": not issues, "issues": issues, "voice": voice_readiness(profile)}, ensure_ascii=False, indent=2))
        return 0 if not issues else 1
    if args.cmd == "prompt":
        print(json.dumps(identity_prompt(profile, args.shot, args.look), ensure_ascii=False, indent=2))
        return 0
    style = music_style(profile, args.extra)
    print(json.dumps({"style": style, "chars": len(style)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
