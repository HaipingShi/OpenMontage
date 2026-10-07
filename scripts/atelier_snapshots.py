"""Render one review still per scene for an atelier (bespoke) composition.

The Backlot storyboard can't thumbnail a `.tsx` scene, so a bespoke run
populates the assets-gate filmstrip by writing `projects/<slug>/snapshots/
<scene_id>.png` — one Remotion `still` per scene at a representative frame.
Projects resolve under the configured user PROJECTS_DIR. Run this AT THE
ASSETS GATE (before any draft/compose render):

    python scripts/atelier_snapshots.py <slug>

It reads scene timings from `artifacts/scene_plan.json` and the bespoke render
config from `artifacts/edit_decisions.json` (falling back to conventional
paths: index.tsx / artifacts/props.json / public/). The composition id comes
from edit_decisions.bespoke.composition_id or --composition-id.
Artifact relative paths resolve within this project. Explicit CLI path overrides
resolve from the caller's working directory and may select external inputs.

See skills/meta/bespoke-composition.md and skills/meta/checkpoint-protocol.md.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# On Windows npx is npx.cmd — resolve it so subprocess finds it without a shell.
NPX = shutil.which("npx") or "npx"

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSER_DIR = REPO_ROOT / "remotion-composer"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from lib.paths import PROJECTS_DIR


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _input_path(override: str | None, configured: str | None, default: Path, project: Path) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    if not configured:
        return default.resolve()
    path = Path(configured).expanduser()
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] == "projects":
        if len(path.parts) < 2 or path.parts[1] != project.name:
            raise ValueError("Artifact projects/<slug> path must name this project")
        path = Path(*path.parts[2:])
    result = (project / path).resolve()
    if ".." in path.parts or not result.is_relative_to(project):
        raise ValueError("Artifact relative input escapes this project")
    return result


def _snapshot_path(project: Path, scene_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", scene_id):
        raise ValueError("Scene id must be a single bounded filename segment")
    directory = project / "snapshots"
    output = directory / f"{scene_id}.png"
    if directory.is_symlink() or output.is_symlink() or not output.resolve().is_relative_to(project):
        raise ValueError(f"Refusing snapshot output symlink or path outside project: {output}")
    return output


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slug", help="project slug under configured user PROJECTS_DIR")
    ap.add_argument("--composition-id", help="Remotion composition id (else from edit_decisions)")
    ap.add_argument("--entry", help="entry .tsx override, relative to cwd (default: project index.tsx)")
    ap.add_argument("--props", help="props JSON override, relative to cwd (default: project artifacts/props.json)")
    ap.add_argument("--public-dir", help="public directory override, relative to cwd (default: project public/)")
    ap.add_argument("--fps", type=int, default=None, help="frames per second (default from props or 30)")
    ap.add_argument("--only", nargs="*", help="only these scene ids")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", args.slug):
        print("error: project slug must be a single bounded directory name", file=sys.stderr)
        return 2
    projects = PROJECTS_DIR.resolve()
    proj = projects / args.slug
    if proj.is_symlink() or not proj.resolve().is_relative_to(projects):
        print(f"error: project symlink or path escapes projects root: {proj}", file=sys.stderr)
        return 2
    if not proj.is_dir():
        print(f"error: no project at {proj}", file=sys.stderr)
        return 2

    scene_plan = _load(proj / "artifacts" / "scene_plan.json")
    scenes = (scene_plan.get("scenes") or []) if isinstance(scene_plan, dict) else []
    if not scenes:
        print("error: no scenes in artifacts/scene_plan.json", file=sys.stderr)
        return 2

    edit = _load(proj / "artifacts" / "edit_decisions.json")
    bespoke = (edit.get("bespoke") or {}) if isinstance(edit, dict) else {}
    try:
        props_path = _input_path(args.props, bespoke.get("props_path"), proj / "artifacts/props.json", proj)
        entry = _input_path(args.entry, bespoke.get("entry"), proj / "index.tsx", proj)
        public_dir = _input_path(args.public_dir, bespoke.get("public_dir"), proj / "public", proj)
        selected = []
        if (proj / "snapshots").is_symlink():
            raise ValueError("Refusing snapshot directory symlink")
        for scene in scenes:
            sid = str(scene.get("id") or "").strip()
            if not sid or (args.only and sid not in args.only):
                continue
            selected.append((scene, sid, _snapshot_path(proj, sid)))
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    comp_id = args.composition_id or bespoke.get("composition_id")
    if not comp_id:
        print("error: composition id unknown (pass --composition-id or set edit_decisions.bespoke)", file=sys.stderr)
        return 2

    fps = args.fps
    if fps is None:
        props = _load(props_path)
        fps = int(props.get("fps") or 30)

    # Stage the project into remotion-composer so webpack resolves node_modules.
    from tools.video.video_compose import VideoCompose  # noqa: E402
    staged_entry = VideoCompose()._stage_atelier_project(entry, COMPOSER_DIR)

    snap_dir = proj / "snapshots"
    snap_dir.mkdir(exist_ok=True)

    ok, fail = 0, 0
    for sc, sid, out in selected:
        start = sc.get("start_seconds")
        end = sc.get("end_seconds")
        mid = ((start + end) / 2) if (start is not None and end is not None) else (start or 0)
        frame = max(0, round(mid * fps))
        # Recheck after staging, immediately before allowing Remotion to write.
        try:
            _snapshot_path(proj, sid)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        cmd = [
            NPX, "remotion", "still", str(staged_entry), str(comp_id), str(out.resolve()),
            f"--frame={frame}",
            f"--props={props_path.resolve()}",
            f"--public-dir={public_dir.resolve()}",
        ]
        try:
            subprocess.run(cmd, cwd=COMPOSER_DIR, check=True, capture_output=True, text=True, timeout=600)
            ok += 1
            print(f"  {sid}: frame {frame} -> {out}")
        except subprocess.CalledProcessError as e:
            fail += 1
            print(f"  {sid}: FAILED — {(e.stderr or e.stdout or '')[-300:]}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  {sid}: FAILED — {e}", file=sys.stderr)

    print(f"snapshots: {ok} ok, {fail} failed -> {snap_dir}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
