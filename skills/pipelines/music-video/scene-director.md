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

### Step 1: Narrative segments, then beat-grid cuts

Use `lib.timeline.build_segments(song_map, min_s=3, max_s=15)` with approved
user limits and `song_map.metadata.fps` = the delivery fps. Section transitions
are mandatory boundaries, lyric boundaries preferred; duration-limited splits
cover long lyric gaps. Sections shorter than min_s record a constraint exception
for director confirmation. Save segments in scene_plan.metadata.segments.
A segment is ONE visual intention, not one cut: cut within it using the existing
song_beatmap cut unit. Each scene carries optional `segment_id` pointing to its
containing segment; no cut may cross two segment intents. Quantize times once
with `quantize(t, fps)`, including section ends, with final frame equal to
`round(song_duration * fps)`. Frame-quantization can move a grid point by at most
half a frame. Never shorten or retime the master to match the picture.


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

Record generation details in optional `generation_context` and `render_route`; retain `required_assets[].description` for older consumers, e.g.:

```json
"required_assets": [
  {"type": "image", "source": "generate", "description": "keyframe from bible:lead_look_a, MCU, rooftop neon"},
  {"type": "video", "source": "generate", "description": "i2v 5s clip; shot 2.18s + handles; slow push-in"},
  {"type": "video", "source": "generate", "description": "lip_sync vocal_in=62.480 vocal_out=64.660 line=l017 (hook)"}
]
```

### Step 3: Per-shot route and provider sizing

Set `render_route` on every generated shot:
- `story_i2v`: ordinary narrative; bible-conditioned keyframe then i2v.
- `performance_lipsync`: scoped singing, prioritizing section onsets, chorus
  entries and hook lines; exact vocal_in/vocal_out in generation_context.
- `cast_multi_ref`: multiple artists in frame; use `cast_prompt` and transmit
  EACH artist's references, keeping reference_map in submitted generation logs.
- `continuation`: use the verified predecessor's exact last decoded frame.

`lib.mv_routes.plan_render_routes` sizes an already approved intent plan, using
provider max_clip_s and 0.5s handles each side. A long segment can contain many
beat cuts; when a generation shot itself exceeds the provider maximum, split it:
first subclip uses its keyframe; subsequent clips use continuation and record
`generation_context.continuation_of`. Keep segment_id across these clips.
Continuation with `generation_context.lip_sync: true` STILL needs its own vocal
slice and lip-sync pass; route is not permission to omit singing requirements.
If the provider only offers discrete lengths, round generation duration UP to a
supported duration and reconcile costs. No provider change without approval.
The helper does not pick framing: director must ensure singing shots remain
near-frontal close-ups. Non-generated lyric/footage scenes may retain a legacy
plan without generation routes; do not generate unnecessary candidates.


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
- Record `vocal_in`/`vocal_out` = the shot's own song-timeline window. The lip-sync audio slice will be cut from song_map.audio.stems.vocals when available, otherwise the master. Include both generation handles in the derived slice; frame 0 maps to vocal_in minus the leading handle.

### Step 6: Budget reconcile

Total the generation plan (bible images, keyframes, clips, lip-sync passes) using the proposal's unit costs. If over budget: increase coverage reuse, convert some verse shots to longer holds, or swap a section's mode (e.g. bridge → lyric/typography) — then tell the user what changed.

### Step 7: Validate and gate

Before writing the artifact call `lib.timeline.validate_timeline(scene_plan,
song_map, fps)` (mandatory), then schema validation. Off-grid, zero-frame shots,
gaps/overlaps or total-frame mismatch must be fixed in the picture. Verify
segment_id references and render routes, and reconcile ALL candidates. Present: shot count per section, average shot length per section vs song energy, number of generated clips after reuse, lip-sync shot list, and the cost total. Checkpoint `awaiting_human` and **end your turn**.

## Common Pitfalls

- Shot boundaries 2–3 frames off the beat because they were typed by hand instead of copied from `cut_points_seconds`.
- One generated clip per shot with no reuse — the budget explodes and identity drift multiplies.
- Lip-sync shots framed as wides or profiles; they will fail or look like a dub.
- A hero moment that is not on the chorus downbeat.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary, and **END YOUR TURN**.
