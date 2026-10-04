# Compose Director — Music Video Pipeline

## When to Use

Eighth stage of `music-video`. Render the edit on the locked runtime, unify the grade across generated sources, draw lyrics if the treatment calls for them, mux the **untouched song master**, and prove sync before anyone watches it.

## Runtime Routing (MANDATORY first step)

Read `edit_decisions.render_runtime` (locked at proposal). Pass `proposal_packet` to every `video_compose.execute()` call so its `runtime_swap_detected` check runs.

- **`render_runtime="remotion"`** — video-led music videos.
  - Templated: `video_compose` operation `render` with `edit_decisions` (`renderer_family: "cinematic-trailer"` → `CinematicRenderer`), `asset_manifest`, `proposal_packet`, and **`audio_path` = the song master**. The render's audio is replaced by the master in the final mux.
  - Atelier (hero releases): hand-author a project-local entry under `projects/<id>/composition/` (the tool stages it inside the composer tree) and set `edit_decisions.composition_mode="atelier"` + `edit_decisions.bespoke` (`entry`, `composition_id`, required `art_direction`, optional `props_path`). Compute the composition's `durationInFrames` from the song duration and fps; never hard-code it. The stock `LyricOverlay` and `CinematicRenderer` sources may be read as a mechanics codex only (see `skills/meta/bespoke-composition.md`).
  - Word-level karaoke lyrics: `remotion_caption_burn` on the picture-lock render (Remotion-only — there is no HyperFrames parity).
- **`render_runtime="hyperframes"`** — typography-led `lyric_video` and beat-reactive HTML/GSAP pieces. Run the `.agents/skills/music-to-video` workflow inside `projects/<id>/hyperframes/`, starting at its **Step 2**: `song_beatmap` already wrote its `audiomap.json` (do not re-analyze), and the song is already locked (do not generate BGM — stage the master using its actual extension and reference that exact path throughout the authored workspace). For atelier mode, follow `skills/meta/bespoke-composition.md` and use the Layer 3 workflow only for timing and engine mechanics; its frozen templates/registry blocks are off-limits. Its plan gate (Step 3) and render gate (Step 6) still apply. `hyperframes lint` / `validate` / `inspect` must pass before render. See `skills/core/hyperframes.md`.
- **`render_runtime="ffmpeg"`** — pure concat of video cuts with no graphics: `video_compose` operation `render`/`compose` with `audio_path` = master.

A locked runtime is a commitment. Silent swap to another runtime (or to an FFmpeg still-image fallback) is a CRITICAL governance violation. If the runtime fails, escalate per AGENT_GUIDE.md → "Escalate Blockers Explicitly".

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `render_report.schema.json`, `final_review.schema.json` | Validation |
| Prior artifacts | `edit_decisions`, `asset_manifest`, `song_map`, `proposal_packet` | What to render |
| Tools | `video_compose`, `hyperframes_compose`, `remotion_caption_burn`, `color_grade`, `audio_probe`, `video_trimmer` | Render and finishing |
| Core | `skills/core/remotion.md`, `skills/core/hyperframes.md`, `skills/core/ffmpeg.md`, `skills/core/color-grading.md` | Engine conventions |

## Process

### Step 0: Preflight

```bash
python -c "
from tools.tool_registry import registry
registry.discover()
info = registry.get('video_compose').get_info()
print('Render engines:', info.get('render_engines'))
"
```

The locked runtime must be `True`. If not, stop and report.

### Step 1: Grade before compose

Generated clips from different providers or batches drift in contrast, saturation, and white balance. Apply one `color_grade` profile per treatment look to every clip (not to the finished render, which would also grade lyric graphics). Match the grade to the treatment palette; keep skin tones natural on performance close-ups.

### Step 2: Render picture lock

Render on the locked runtime to `projects/<id>/renders/picture_lock.mp4` with `audio_path` = master. Use the fps and resolution from the brief.

### Step 3: Lyrics pass (if in treatment)

- Remotion line-level: render the lyric layer in the same composition (atelier) so text and picture share one timeline.
- Remotion word-level: `remotion_caption_burn` with `input_path` = picture lock, `segments` from the transcript, `corrections` from the authoritative lyrics, highlight color from the treatment palette.
- HyperFrames: lyrics are part of the `music-to-video` frames; nothing extra.

For the current FFmpeg concat path, lock **30 fps at proposal** and use it consistently in the brief and edit frame math; this path currently normalizes video segments to 30 fps. If the requested master is 24 fps, choose an available runtime that honors it and obtain the runtime approval at proposal.

### Step 4: Final mux (always)

Whatever produced the last picture file, the shipped file's audio must be the master. The current `video_compose` mux uses AAC at 192 kbps with audio padding. For this pipeline, finish with an explicit mux from the original master to meet the 320 kbps delivery spec: copy the video stream, map audio from the master, encode AAC at 320 kbps (or keep a lossless master for the archive copy), end at the video length. Never re-encode the master through a filter chain (no loudnorm, no fades, no resample beyond the AAC encoder).

Write `projects/<id>/renders/final.mp4`.

### Step 5: Prove sync (mandatory)

1. `audio_probe` / ffprobe: video duration == `song_map.audio.duration_seconds` within one frame; one audio stream; expected resolution and fps.
2. Run `scene_detect` on `final.mp4` and compare detected cut times with the planned cut boundaries from `edit_decisions`. Report the share within one frame and the worst offender.
3. Sample frames (`frame_sampler`) at: the first chorus downbeat, each `hard_stops` time, the bridge entry, the final hit, and the middle of two lip-sync shots. Confirm the planned shot is on screen at each.
4. Play the first chorus and one lip-sync shot and state whether mouths read as in sync (lead/lag > ~2 frames is visible).

Record all of this in `final_review` with the exact timestamps checked.

### Step 6: Render report

Recommended `metadata` keys: `render_runtime`, `composition_mode`, `grade_profile`, `lyric_mode` (`none | line | word | hyperframes`), `audio_master_path`, `sync_check` (cut match %, worst offset in frames), `variant_outputs`.

Checkpoint `completed`, then present the render with the sync summary.

## Common Pitfalls

- Letting the renderer mix the song at its default music volume with fades instead of muxing the master.
- Grading the final render and tinting the lyric typography.
- A HyperFrames lyric workflow that re-runs its own analyzer or generates its own BGM, producing a second clock.
- Declaring done without the scene-detect cut comparison — off-by-a-few-frames edits look "fine" in a quick scrub and feel wrong at full speed.
