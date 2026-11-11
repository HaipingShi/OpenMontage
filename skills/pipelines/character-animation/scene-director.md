# Scene Director - Character Animation Pipeline

## Goal

Produce a `scene_plan` where each scene is feasible for rigged character
animation.

## Scene Planning Fields

For each scene, include:

- character IDs,
- emotional beat,
- action sequence,
- camera/framing,
- background,
- props,
- effects,
- required assets,
- transition notes.

Use `type: "character_scene"` for rigged character acting scenes. Store
character-specific detail in `character_actions`; do not put per-scene acting
data in arbitrary metadata because the shared `scene_plan` schema rejects
unknown per-scene fields.

## Complexity Budget

Prefer fewer, stronger shots:

- one establish,
- one action beat,
- one reaction beat,
- one resolution beat.

Avoid scenes that require many unique views or complex physical contact unless
the user approved that complexity.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.

## Structured generated shots

Read [`skills/meta/shot-workbench.md`](../../meta/shot-workbench.md) before planning or generating a complex shot. Use the shared `shots/<shot_id>.json` as its source of truth, preserving `shot_spec_path` and `shot_spec_revision` in the scene plan. Build or edit structured fields, then use the registry's local `shot_compiler` (or the headless CLI `compile` / `dry-run`) and resolve every lint error before calling the previously approved generation tool. Keep the selected provider/model and budget approval policy. Do not independently rewrite the compiled prompt.

Offer the workbench when there are three or more references, multiple subjects, precise camera/blocking needs, frame continuity, or two rejected attempts. Explain why, open the shot for the user, and wait for their `release`; inspect its revision and handoff diff before resuming this stage. Agent saves need an agent lease. Source footage remains on its existing edit path; this contract applies only to generated shots.
