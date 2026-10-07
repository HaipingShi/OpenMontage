"""Scaffold a project-local bespoke (atelier) Remotion composition.

This is the ONLY thing reused across atelier videos: the engine plumbing
(registerRoot/Composition/calculateMetadata boilerplate and the project layout).
**No creative content is ever emitted.** The placeholder scene is deliberately
blank — the agent must hand-author it from scratch per
`skills/meta/bespoke-composition.md`.

Why this exists: friction is what nudges agents back to the templated path.
Re-deriving the entry/Root/index boilerplate from memory every time is the kind
of friction this removes; emitting a finished scene would reintroduce the
template trap.

Usage:
    python scripts/scaffold_atelier_project.py <slug> [--composition-id CamelName]

Creates:
    <configured PROJECTS_DIR>/<slug>/
      index.tsx                # registerRoot(Root)
      Root.tsx                 # one Composition + calculateMetadata
      Composition.tsx          # EMPTY scene with TODO; no imports from src/
      art-direction.md         # checklist to fill BEFORE authoring
      artifacts/props.template.json
      assets/{audio,music,footage}/
      public/                  # narration/music get copied here for staticFile()
      renders/
      README.md                # render command + doctrine pointer
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from lib.paths import PROJECTS_DIR


def to_camel(slug: str) -> str:
    parts = re.split(r"[\s_\-]+", slug.strip())
    return "".join(p.capitalize() for p in parts if p) or "Bespoke"


def write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing output symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        print(f"  skip (exists)  {path}")
        return
    path.write_text(content, encoding="utf-8")
    print(f"  wrote          {path}")


def _check_output(project: Path, relative: str) -> None:
    path = project
    for component in Path(relative).parts:
        path /= component
        if path.is_symlink():
            raise ValueError(f"Refusing project output symlink: {path}")
    if not path.resolve().is_relative_to(project):
        raise ValueError(f"Output escapes project: {path}")


def scaffold(slug: str, comp_id: str, root: Path | None = None) -> Path:
    """Use the user projects root; explicit legacy roots retain root/projects."""
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,127}", slug):
        raise ValueError("slug must be a bounded kebab-case project name")
    if not isinstance(comp_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", comp_id):
        raise ValueError("composition id must be a bounded identifier")
    projects = ((Path(root).expanduser() / "projects") if root is not None else PROJECTS_DIR).resolve()
    proj = projects / slug
    if proj.is_symlink() or not proj.resolve().is_relative_to(projects):
        raise ValueError(f"Refusing project symlink or path outside projects root: {proj}")
    directories = ("artifacts", "assets/audio", "assets/music", "assets/footage", "public", "renders")
    files = ("index.tsx", "Root.tsx", "Composition.tsx", "art-direction.md", "artifacts/props.template.json", "README.md")
    # Check every existing destination before creating any part of the scaffold.
    for relative in (*directories, *files):
        _check_output(proj, relative)
    (proj / "artifacts").mkdir(parents=True, exist_ok=True)
    for sub in directories[1:]:
        (proj / sub).mkdir(parents=True, exist_ok=True)

    # --- index.tsx -------------------------------------------------------
    write(proj / "index.tsx", """\
import { registerRoot } from "remotion";
import { Root } from "./Root";

registerRoot(Root);
""")

    # --- Root.tsx --------------------------------------------------------
    write(proj / "Root.tsx", f"""\
import {{ Composition }} from "remotion";
import {{ Scene, calculateMetadata, SceneProps }} from "./Composition";

export const Root: React.FC = () => (
  <Composition
    id="{comp_id}"
    component={{Scene}}
    durationInFrames={{30 * 30}}
    fps={{30}}
    width={{1920}}
    height={{1080}}
    defaultProps={{ {{ /* fill from artifacts/props.json at render time */ }} as SceneProps }}
    calculateMetadata={{calculateMetadata}}
  />
);
""")

    # --- Composition.tsx (EMPTY scene; no creative content) --------------
    write(proj / "Composition.tsx", """\
import React from "react";
import { AbsoluteFill, CalculateMetadataFunction } from "remotion";

// ----------------------------------------------------------------------------
// ATELIER (BESPOKE) — hand-authored from scratch.
//
// HARD RULES (enforced by tools/video/video_compose.py → _run_atelier_checks
//             and skills/meta/reviewer.md → Composition Authoring Mode Review):
//   1. Do NOT import from remotion-composer/src/components, src/Explainer,
//      src/CinematicRenderer, src/{TitledVideo,TalkingHead,CollageBurst,...}.
//      The stock registry is a mechanics codex, not a parts bin.
//   2. Read skills/meta/bespoke-composition.md FIRST.
//   3. Fill in art-direction.md BEFORE writing the scene.
//
// Engine knowledge you MAY reuse freely (from `remotion`, `@remotion/*`):
//   useCurrentFrame, useVideoConfig, spring, interpolate, Sequence,
//   AbsoluteFill, Audio, OffthreadVideo, Img, staticFile, random, Easing.
// ----------------------------------------------------------------------------

export interface SceneProps {
  // TODO: define the props your composition consumes (timing, narration path,
  // captions, etc.). Keep this minimal — props are data, not configuration.
}

export const Scene: React.FC<SceneProps> = () => {
  // TODO: hand-stitch your scene here. The placeholder below renders solid
  // black so the render pipeline can be validated end-to-end before authoring.
  // Remove it before committing.
  return <AbsoluteFill style={{ background: "#000" }} />;
};

export const calculateMetadata: CalculateMetadataFunction<SceneProps> = async ({ props }) => ({
  durationInFrames: 30 * 30, // TODO: derive from your props (e.g. total seconds * fps)
  fps: 30,
  width: 1920,
  height: 1080,
});
""")

    # --- art-direction.md (the divergence engine — fill BEFORE authoring) ---
    write(proj / "art-direction.md", f"""\
# Art Direction — {slug}

> Fill this in BEFORE writing any scene code. It is the divergence engine: the
> reason this video looks like nothing you've made before. Per
> `skills/meta/bespoke-composition.md` step 1 and reviewer enforcement.

## Subject
What this video is about, in one sentence. What makes its *visual* problem
unlike any other you've solved.

## Palette
Three to five concrete hex colors. Why these specifically — what feeling do they
carry, what does the subject demand?

## Type personality
Two or three concrete fonts (heading, body, accent). Why this voice, not
another?

## Motion character
How things move and feel — adjectives + concrete physics (spring damping,
durations, easings). "Settling, ink-on-paper" feels different from "snap, neon"
even with the same Remotion primitives.

## Layout & rhythm
Where the eye goes. Hierarchy. Negative space. Time signature of cuts.

## Signature device
ONE bespoke visual element that belongs to *this* video and no other.
A hand-drawn diagram. An ink-settle reveal. A coin stamp. A custom transition.
If this slot is blank, the video will look like every other.

## Anti-references
What this should NOT look like — including any prior video you've made.
Naming them keeps you from drifting into them.
""")

    # --- props template ------------------------------------------------------
    write(proj / "artifacts" / "props.template.json", """\
{
  "// note": "Fill from your build pipeline. Put narration.mp3 / music.mp3 etc.",
  "// note2": "in ./public/ so Remotion staticFile() can resolve them."
}
""")

    # --- README -------------------------------------------------------------
    write(proj / "README.md", f"""\
# {slug} — atelier (bespoke) composition

Hand-authored Remotion composition. Source of truth lives here under
`{proj}/`; at render time the atelier path auto-stages source files under
`remotion-composer/projects/{slug}/` so the bundler can resolve `node_modules`.

## Doctrine
- Read `skills/meta/bespoke-composition.md` first.
- Fill in `art-direction.md` BEFORE authoring scenes.
- No imports from `remotion-composer/src/*` (the tool will fail the render).
- Reuse engine knowledge only; hand-stitch every creative component.

## Render

```python
from pathlib import Path
from tools.video.video_compose import VideoCompose
P = Path({json.dumps(str(proj), ensure_ascii=False)})
VideoCompose().execute({{
  "operation": "render",
  "output_path": str(P / "renders" / "final.mp4"),
  "edit_decisions": {{
    "render_runtime": "remotion",
    "composition_mode": "atelier",
    "bespoke": {{
      "entry": str(P / "index.tsx"),
      "composition_id": "{comp_id}",
      "props_path": str(P / "artifacts" / "props.json"),
      "public_dir": str(P / "public"),
      "art_direction": "<short note OR path to art-direction.md>",
      "scale": 1.0, "crf": 18, "concurrency": 8
    }}
  }}
}})
```
""")

    return proj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", help="kebab-case project name, e.g. 'compound-snowball'")
    ap.add_argument("--composition-id", help="React composition id (default: CamelCase of slug)")
    ap.add_argument("--root", type=Path, help="Explicit legacy root; use <root>/projects (default: configured user PROJECTS_DIR)")
    args = ap.parse_args(argv)

    slug = args.slug.strip().lower()
    if not re.match(r"^[a-z][a-z0-9\-]*$", slug):
        print(f"error: slug must be kebab-case, got {slug!r}", file=sys.stderr)
        return 2
    comp_id = args.composition_id or to_camel(slug)
    try:
        proj = scaffold(slug, comp_id, args.root)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"\nDone. Now:")
    print(f"  1. Open {proj / 'art-direction.md'} and fill it in.")
    print(f"  2. Read skills/meta/bespoke-composition.md.")
    print(f"  3. Hand-author Composition.tsx (replace the black placeholder).")
    print(f"  4. Render — see {proj / 'README.md'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
