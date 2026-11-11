# Asset Director - Character Animation Pipeline

## Goal

Produce `asset_manifest` with character parts, backgrounds, props, audio, music,
and preview artifacts.

## Layer 3 Gate

Before authoring or generating animation assets, read the relevant Layer 3 skills:

- `character-rigging`
- `svg-character-animation`
- `pose-library-design`
- `canvas-procedural-animation` when p5/canvas effects are used
- `character-animation-qa` before review
- `gsap-core`, `gsap-timeline`, and `gsap-react` for GSAP/Remotion work
- `remotion` and `remotion-best-practices` for Remotion render work
- `hyperframes` and `hyperframes-cli` for HyperFrames work

Before image/TTS/music generation, read the tool's `agent_skills` from the
registry.

## Asset Organization

Write character assets under:

```text
projects/<project-name>/assets/characters/<character-id>/
```

Use subfolders:

```text
parts/
poses/
previews/
```

Generated backgrounds go under:

```text
projects/<project-name>/assets/backgrounds/
```

## Process

1. Produce or source only the parts required by `rig_plan`.
2. Keep each moving part separate.
3. Preserve transparent backgrounds for parts.
4. Record prompts, seeds, providers, and model names.
5. Build a small preview before full asset expansion.

## Quality Bar

All parts referenced by `rig_plan` must exist before compose. Missing parts are a
blocker unless the action timeline removes the action requiring them.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.

## Structured generated shots

Read [`skills/meta/shot-workbench.md`](../../meta/shot-workbench.md) before planning or generating a complex shot. Use the shared `shots/<shot_id>.json` as its source of truth, preserving `shot_spec_path` and `shot_spec_revision` in the scene plan. Build or edit structured fields, then use the registry's local `shot_compiler` (or the headless CLI `compile` / `dry-run`) and resolve every lint error before calling the previously approved generation tool. Keep the selected provider/model and budget approval policy. Do not independently rewrite the compiled prompt.

Offer the workbench when there are three or more references, multiple subjects, precise camera/blocking needs, frame continuity, or two rejected attempts. Explain why, open the shot for the user, and wait for their `release`; inspect its revision and handoff diff before resuming this stage. Agent saves need an agent lease. Source footage remains on its existing edit path; this contract applies only to generated shots.
