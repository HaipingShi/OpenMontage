# Edit Director — Music Video Pipeline

## When to Use

Seventh stage of `music-video`. You assemble `edit_decisions`: a frame-accurate, sequential cut list that runs exactly the song's length, lands every cut on the grid, places lip-synced shots where their vocal came from, and times on-screen lyrics from `song_map`.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan`, `asset_manifest`, `song_map`, `proposal_packet` | Shots, media, clock, locked runtime |
| Creative | `skills/creative/beat-sync-editing.md` | Cut grammar, contour, frame math |

## How the renderer reads cuts (know this before editing)

- Cuts are **sequential**: each cut's timeline duration is `(out_seconds - in_seconds) / speed`, and cuts are laid end to end from 0. There is no explicit timeline start — position is the running sum. One wrong duration shifts every later cut off the beat.
- In `CinematicRenderer`, video scenes get default fades unless `transition_in`/`transition_out` are `"cut"` (or `"none"`). Music videos cut hard: set both to `"cut"` on every cut unless a dissolve is a deliberate choice.
- Clip audio is muted by the renderer; the song is muxed afterwards from `audio_path` at compose. Do not add music through `edit_decisions.music` volume/fade defaults.

## Process

### Step 1: Lock the frame math

Use the fps from the brief (`metadata.fps`, default 24). Convert every scene_plan boundary to a frame index **once**: `f = round(t * fps)`. Each cut's duration in frames is `f_end - f_start`; its source span is `frames / fps` seconds (times `speed` if ramped). Computing durations from rounded boundaries — never from rounded durations — guarantees zero cumulative drift. See `beat-sync-editing.md` → "Frame math".

The final cut's end frame must equal `round(song_duration * fps)`.

### Step 2: Build cuts shot by shot

For each scene_plan shot in order:

| Field | Value |
|-------|-------|
| `id` | the shot id |
| `source` | asset id from the manifest (the lip-synced variant when one exists) |
| `in_seconds` | Where in the source clip the shot starts (inside the handles) |
| `out_seconds` | `in_seconds + frames / fps * speed` |
| `speed` | `1.0` by default; see speed ramps below |
| `transition_in` / `transition_out` | `"cut"` unless deliberate |
| `layer` | `"primary"` |
| `reason` | The grid anchor and intent, e.g. `"chorus1 downbeat — hero; cut on snare 2"` |

**Choosing `in_seconds` (the edit inside the clip):** pick the moment of motion that best hits the cut — a head turn, a step, a light flash — so the action lands on the downbeat. For coverage-reuse clips, use different in-points for each reuse so repeats don't look identical.

**Lip-synced shots:** the clip's frame 0 = `vocal_in` on the song timeline. Their `in_seconds` must equal `shot_start - vocal_in` (normally `0`). Any other in-point breaks sync. Verify against the asset's `generation_summary`.

**Speed ramps:** only on non-lip-sync shots. Slow motion from a 24 fps generated clip stutters below ~0.6x — prefer generating the slow-motion shot natively (prompt "slow motion") over `speed < 1`. Speed-ups (>1) to fit motion to a bar are fine up to ~1.5x.

### Step 3: Check the contour

Compute average shot length per section from your cuts and compare with the song's energy (`song_map.sections[].level`). Expected shape: verse > pre-chorus > chorus; bridge breaks the pattern; final chorus is the densest. If a section is flat (uniform shot length for 30+ seconds), vary it with holds on lyric phrases and quick inserts on fills/rolls.

### Step 4: On-screen lyrics (if in treatment)

Build lyric timing from `song_map.lyrics.lines` (only `aligned: true` lines; flag the rest to the user):

- **Line-level** (`LyricOverlay`-style, HyperFrames lyric video): `{text, inSeconds: start_seconds, outSeconds: end_seconds}`; extend `outSeconds` to the next line's start minus ~0.1 s when the gap is short so text does not flicker.
- **Word-level karaoke** (`remotion_caption_burn`, Remotion-only): pass the transcript segments with words; apply `corrections` from the authoritative lyrics.

Store the lyric plan in `edit_decisions.subtitles` (position, style) and/or `edit_decisions.metadata.lyrics` (the line list and which renderer path draws it). Keep text out of the singer's face on lip-sync shots (`position: "bottom-center"` or `"top-center"`).

### Step 5: Audio fields

```json
"audio": {
  "music": {"asset_id": "song-master", "volume": 1.0, "ducking": false, "fade_in_seconds": 0, "fade_out_seconds": 0}
},
"metadata": {"audio_policy": "song_master_only", "proposal_render_runtime": "remotion"}
```

No `audio.narration`, no SFX bed. The compose stage muxes the master file itself via `audio_path`; these fields document intent and keep any renderer default (e.g. 0.15 music volume) from being mistaken for the plan.

Fades are allowed only on an approved excerpt whose end is not a natural stop — and then they were already baked into the excerpt master at `song_analysis`.

### Step 6: Runtime and family

Copy `render_runtime`, `renderer_family` (typically `cinematic-trailer` for video-led, `animation-first` for typography-led), and `composition_mode` from `proposal_packet.production_plan` unchanged. Record `metadata.proposal_render_runtime` as well.

### Step 7: Validate (mandatory frame-grid check)

Before writing edit_decisions call `lib.timeline.validate_timeline(scene_plan,
song_map, fps)`; also construct a scene-shaped timeline from the running cut
frame indices and call it on that cut timeline. Both must return valid with
`total_frames == round(song_duration * fps)`. Resolve each generated source to
its selected_candidate path; never use rejected/unreviewed attempts silently.


- Schema-valid `edit_decisions`.
- Sum of cut durations (in frames) == `round(song_duration * fps)`.
- Every cut boundary (running sum) is within 1 frame of a `song_map` grid point or lyric-line start.
- Every lip-sync cut has `in_seconds` consistent with its `vocal_in`.
- Every `source` resolves to an asset in the manifest.

Write a small validation script under `projects/<id>/scripts/` if helpful (custom scripts are permitted by the manifest) — but never edit the song or regenerate assets here. Checkpoint `completed`.

## Common Pitfalls

- Typing durations like `2.18` by hand; after 70 cuts the edit is half a beat late.
- Forgetting `"cut"` transitions, so every shot fades in and the edit looks soft and off-beat.
- Re-using a lip-synced clip at a different song position because "it looks good there".
- Lyrics typed from memory instead of taken from `song_map.lyrics.lines`.
