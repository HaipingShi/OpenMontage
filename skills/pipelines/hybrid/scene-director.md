# Scene Director - Hybrid Pipeline

## When To Use

You are translating the hybrid structure into a visual system that keeps the source visible and the support layers under control.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["script"]["script"]`, `state.artifacts["idea"]["brief"]` | Hybrid structure and source truth |
| Tools | `frame_sampler`, `scene_detect` | Optional source inspection |
| Playbook | Active style playbook | Layout consistency |

## Process

### 1. Keep The Anchor Medium Visible

If the piece is source-led, the source must remain visually primary in the scene plan. Do not hide the anchor behind constant overlays.

### 2. Reserve Support For Clear Jobs

Use support scenes for:

- chapter transitions,
- clarifying diagrams,
- stat emphasis,
- CTA or summary moments,
- gap-filling inserts.

### 3. Plan Variant Safety

If the project needs multiple aspect ratios, define where:

- subtitles live,
- speaker labels live,
- chart or code safe zones live,
- crop-sensitive source media becomes unsafe.

### 4. Use Metadata For Balance Rules

Recommended metadata keys:

- `anchor_rules`
- `support_rules`
- `safe_zones`
- `variant_rules`
- `overlay_density_limits`

### 5. Quality Gate

- the anchor medium stays primary where intended,
- support layers are limited and purposeful,
- aspect-ratio planning is explicit,
- no scene relies on invisible future magic.

## Common Pitfalls

- Turning source-led scenes into overlay soup.
- Forgetting variant-safe zones until compose.
- Using generated inserts for every transition.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.

## Structured generated shots

Read [`skills/meta/shot-workbench.md`](../../meta/shot-workbench.md) before planning or generating a complex shot. Use the shared `shots/<shot_id>.json` as its source of truth, preserving `shot_spec_path` and `shot_spec_revision` in the scene plan. Build or edit structured fields, then use the registry's local `shot_compiler` (or the headless CLI `compile` / `dry-run`) and resolve every lint error before calling the previously approved generation tool. Keep the selected provider/model and budget approval policy. Do not independently rewrite the compiled prompt.

Offer the workbench when there are three or more references, multiple subjects, precise camera/blocking needs, frame continuity, or two rejected attempts. Explain why, open the shot for the user, and wait for their `release`; inspect its revision and handoff diff before resuming this stage. Agent saves need an agent lease. Source footage remains on its existing edit path; this contract applies only to generated shots.
