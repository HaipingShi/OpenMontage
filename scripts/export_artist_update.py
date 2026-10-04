"""Export one artist patch into both distribution locations using an isolated Git index.

Usage: python scripts/export_artist_update.py --base-ref <pre-artist-commit>
       --output /absolute/path/update.zip [--source-zip /absolute/path/original.zip]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SCOPES = (
    ".gitignore", "artists", "lib/artist_profile.py", "schemas/artifacts/__init__.py",
    "schemas/artifacts/artist_profile.schema.json", "skills/INDEX.md",
    "skills/meta/digital-artist.md", "skills/pipelines/music-video",
    "tools/audio/suno_music.py", "docs/DIGITAL_ARTIST_VOICE.md",
    "scripts/export_artist_update.py", "tests/contracts/test_artist_profile.py",
    "tests/contracts/test_artist_voice_safety.py", "tests/contracts/test_artist_export.py",
)
PATCH_PATHS = ("openmontage-music-video/digital-artist.patch", "public/openmontage-digital-artist.patch")


def _exportable(path: Path) -> bool:
    return "__pycache__" not in path.parts and not (
        len(path.parts) > 2 and path.parts[0] == "artists" and path.parts[2] == "voice"
    )


def export_update(base_ref: str, output: Path, source_zip: Path | None = None, repo: Path = REPO_ROOT) -> dict:
    def git(*args, env=None):
        return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True).stdout

    base = git("rev-parse", "--verify", f"{base_ref}^{{commit}}").decode().strip()
    files = set()
    for scope in SCOPES:
        path = repo / scope
        for candidate in (path.rglob("*") if path.is_dir() else [path]):
            relative = candidate.relative_to(repo)
            if candidate.is_file() and _exportable(relative):
                if candidate.is_symlink():
                    raise ValueError(f"refusing to export symlink: {relative}")
                files.add(relative.as_posix())
    with tempfile.TemporaryDirectory(prefix="artist-export-") as temp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temp) / "index")}
        git("read-tree", base, env=env)
        git("add", "--", *sorted(files), env=env)
        patch = git("diff", "--cached", "--binary", "--full-index", "--no-ext-diff", base, "--", *SCOPES, env=env)
        if not patch:
            raise ValueError("no artist changes relative to base-ref")
        # Retrieve bytes from the same index snapshot as the patch, not the live workspace.
        replacements = {f"openmontage-music-video/{name}": git("show", f":{name}", env=env) for name in files}
    if source_zip:
        # Refresh matching legacy MTV source files too, retaining earlier compatibility fixes.
        with zipfile.ZipFile(source_zip) as original:
            for name in original.namelist():
                if name.startswith("openmontage-music-video/") and not name.endswith("/"):
                    relative = Path(name.removeprefix("openmontage-music-video/"))
                    if not relative.is_absolute() and ".." not in relative.parts and _exportable(relative):
                        local = repo / relative
                        if local.is_file() and not local.is_symlink():
                            replacements[name] = local.read_bytes()
    for name in PATCH_PATHS:
        replacements[name] = patch
    manifest = {"base_commit": base, "patch_sha256": hashlib.sha256(patch).hexdigest(),
                "patch_paths": list(PATCH_PATHS), "files": sorted(files),
                "local_voice_audio_included": False}
    replacements["artist-update-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    replacements["ARTIST_UPDATE_README.md"] = (
        f"# Artist update\n\nBase commit: `{base}`.\n\n"
        "Both digital-artist.patch copies contain identical bytes. Apply only one, from the repository root:\n\n"
        "```bash\ngit apply --check /path/to/digital-artist.patch\ngit apply /path/to/digital-artist.patch\n```\n\n"
        "The patch updates the existing music-video integration; do not also apply the legacy music-video.patch. "
        "Read docs/DIGITAL_ARTIST_VOICE.md for the saved Suno bindings and local import. "
        "Local voice recordings are permanent personal assets and are excluded; back them up separately.\n"
    ).encode()
    output = output.resolve()
    if source_zip and output == source_zip.resolve():
        raise ValueError("output must differ from source archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip", delete=False) as stream:
        temp_zip = Path(stream.name)
    try:
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as target:
            if source_zip:
                with zipfile.ZipFile(source_zip) as original:
                    seen = set()
                    for entry in original.infolist():
                        if entry.filename.startswith("openmontage-music-video/") and not _exportable(
                            Path(entry.filename.removeprefix("openmontage-music-video/"))
                        ):
                            continue
                        if entry.filename not in replacements and entry.filename not in seen:
                            target.writestr(entry, original.read(entry))
                            seen.add(entry.filename)
            for name, data in sorted(replacements.items()):
                target.writestr(name, data)
        with zipfile.ZipFile(temp_zip) as verify:
            assert verify.read(PATCH_PATHS[0]) == verify.read(PATCH_PATHS[1]) == patch
        temp_zip.replace(output)
    finally:
        temp_zip.unlink(missing_ok=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-zip", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_update(args.base_ref, args.output, args.source_zip), indent=2))
