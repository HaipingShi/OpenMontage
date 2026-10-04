# Scene Director — Music Video Pipeline

## When to Use

Fifth stage of `music-video`. You turn the section script into a **grid-locked shot list**: every shot has song-timeline boundaries taken from `song_map`, a mode, a framing, a camera move, a continuity reference, and a generation plan sized to provider limits. The approved scene plan is what gets paid for.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `script`, `song_map`, `proposal_packet` | Beats, timings, treatment, budget |
| Creative | `skills/creative/beat-sync-editing.md` | Cut grammar and density contour |
| Creative | `skills/creative/video-gen-prompting.md` | Shot prompting vocabulary |
| Layer 3 | `.agents/skills/ai-video-gen/SKILL.md` | Provider clip lengths and i2v behavior |

## Process

### Step 1: Place shot boundaries on the grid

For each script section, take its song_map section:

- `pacing: "beat_cut"` → shot boundaries come from `cut_points_seconds`. You may **merge** adjacent cut points into longer shots (hold through a lyric phrase, let a hero shot breathe) but never invent a boundary that is not a grid point.
- `pacing: "phrase_flow"` → boundaries come from phrase starts (`cut_points_seconds` holds them) or lyric line starts (`song_map.lyrics.lines[].start_seconds`, snapped to the nearest beat if the line starts on a pickup).
- Section boundaries are always shot boundaries.

Follow the density contour in `beat-sync-editing.md`: verses hold, pre-choruses tighten, choruses hit, the bridge breaks the pattern, the final chorus is the densest.

### Step 2: Describe each shot

One `scene_plan.scenes[]` entry per shot:

| Field | Music-video use |
|-------|-----------------|
| `id` | `sh-<section>-<nn>`, e.g. `sh-s04-03` |
| `type` | `generated` (AI clip), `broll` (stock/supplied footage), `text_card` (lyric/title card), `animation` (motion graphics) |
| `start_seconds` / `end_seconds` | Exact grid times from Step 1 |
| `script_section_id` | `sec-<song_section_id>` |
| `description` | What happens, in one line a camera operator could shoot |
| `shot_language` | `shot_size`, `camera_movement`, `lens_mm`, `lighting_key`, `color_temperature`, `depth_of_field` — enums from the schema |
| `narrative_role` | e.g. `establish_context` (intro), `build_tension` (pre-chorus), `deliver_payload` (chorus hero), `emotional_beat`, `resolution` (outro) |
| `hero_moment` | `true` on the first downbeat of each chorus/drop and the final chorus hero shot |
| `character_actions` | Objects `{character_id, action_sequence[], emotion, notes}` — e.g. `{"character_id": "lead", "action_sequence": ["sings l017 to lens", "turns on the downbeat"], "emotion": "defiant"}` |
| `texture_keywords` | Grain, flare, haze, VHS, etc. from the treatment |
| `required_assets` | What must exist: bible refs, keyframe, clip, lip-sync pass (see below) |
| `overlay_notes` | On-screen lyric line ids, if any |

Encode lip-sync and generation details in `required_assets[].description` (the schema has no dedicated fields), e.g.:

```json
"required_assets": [
  {"type": "image", "source": "generate", "description": "keyframe from bible:lead_look_a, MCU, rooftop neon"},
  {"type": "video", "source": "generate", "description": "i2v 5s clip; shot 2.18s + handles; slow push-in"},
  {"type": "video", "source": "generate", "description": "lip_sync vocal_in=62.480 vocal_out=64.660 line=l017 (hook)"}
]
```

### Step 3: Size generation to provider limits

- Most providers generate fixed lengths (commonly 5 s or 10 s). Plan each generated clip ≥ shot length + **0.5 s handles** on both ends.
- **Coverage reuse:** a single 10 s performance clip can supply 3–4 shots in a chorus, and chorus coverage can be re-cut for later choruses (with an escalation insert per repeat). Mark reuse in `required_assets[].description` (`"reuse: clip of sh-s04-01, in=3.2s"`). This is the main lever that keeps a 3-minute video on budget.
- Shots under ~0.6 s rarely read as motion — use them only as flash cuts inside a roll/fill.

### Step 4: Continuity

- Every shot with cast references a bible look id (`bible:<cast>_<look>`). No cast shot without a bible reference.
- Keep screen direction consistent within a scene (who is left/right, direction of travel).
- Lighting key and color temperature stay constant within a location; changes are deliberate (bridge pattern break, time passing).

### Step 5: Lip-sync shots

Only for shots selected under the approved lip-sync scope:

- Framing: medium close-up or close-up, face ≥ ~30 % of frame height, near-frontal (≤ 30° yaw), mouth unoccluded (no mic in front of lips, no hair across the mouth).
- Movement: slow or static camera; avoid whip pans and heavy motion blur.
- Record `vocal_in`/`vocal_out` = the shot's own song-timeline window. The lip-sync audio slice will be cut from the master at exactly these times.

### Step 6: Budget reconcile

Total the generation plan (bible images, keyframes, clips, lip-sync passes) using the proposal's unit costs. If over budget: increase coverage reuse, convert some verse shots to longer holds, or swap a section's mode (e.g. bridge → lyric/typography) — then tell the user what changed.

### Step 7: Validate and gate

Validate `scene_plan` (`additionalProperties: false`). Check shots tile `[0, song duration]` with no gaps. Present: shot count per section, average shot length per section vs song energy, number of generated clips after reuse, lip-sync shot list, and the cost total. Checkpoint `awaiting_human` and **end your turn**.

## Common Pitfalls

- Shot boundaries 2–3 frames off the beat because they were typed by hand instead of copied from `cut_points_seconds`.
- One generated clip per shot with no reuse — the budget explodes and identity drift multiplies.
- Lip-sync shots framed as wides or profiles; they will fail or look like a dub.
- A hero moment that is not on the chorus downbeat.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary, and **END YOUR TURN**.
