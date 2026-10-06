# Asset Director — Music Video Pipeline

## When to Use

Sixth stage of `music-video`. You generate or collect every visual the scene plan needs — performer bible first, then keyframes, clips, and lip-sync passes — while keeping cast identity consistent and spend under control.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan`, `song_map`, `proposal_packet` | Shots, timing, approved providers |
| Creative | `skills/creative/image-gen-usage.md`, `skills/creative/video-gen-prompting.md`, `skills/creative/lip-sync-usage.md` | Prompting and lip-sync conventions |
| Layer 3 | Read every `agent_skills` entry of each tool before first use (`video_selector`, `image_selector`, `kling_lip_sync`, `lip_sync`) | Provider-specific prompting |

## Process

### Step 0: Register the song master

Add the locked master as the first asset:

```json
{"id": "song-master", "type": "music", "path": "projects/<id>/assets/music/master.wav",
 "source_tool": "user_supplied", "scene_id": "global", "duration_seconds": 214.37}
```

Use `source_tool: "suno_music"` (and `provider`, `model`, `prompt`) when it was generated. This is the only audio asset that reaches the final mix.

For pure `lyric_video` and `footage_cut`, collect only the approved fonts/backgrounds or footage selects and the master. Skip bible/keyframe/video/lip-sync generation unless the section mode mix actually calls for it.

### Step 1: Performer bible (cast shots only)

**Persistent artist (`brief.metadata.artist_id` set): do not generate a bible.** Copy the artist's canonical images (`lib.artist_profile.bible_assets(profile, look)`) into `assets/images/bible/`, register them with `subtype: "performer_bible"` and `source_tool: "artist_profile"`, and build every cast keyframe prompt with `identity_prompt(profile, shot, look)` — use its `prompt`, `negative_prompt`, and `reference_images` as-is. Check each keyframe against its `locked_traits` list. `supporting_references` are wardrobe/proportion guides, never face references; use them only after masking/cropping their faces or as written wardrobe guidance. Do not flatten them into `reference_images`. A wardrobe the profile does not have is a new look added via `skills/meta/digital-artist.md`, not an improvised prompt. For `metadata.artist_ids`, copy every artist's canonical images; solo shots still use `identity_prompt`, and shots with several artists use `cast_prompt(profiles, shot, looks)`, which keeps each person's references separate (`reference_map`) and returns `locked_traits` per artist. Check every person in the frame against their own list; if the provider confuses them, generate each person separately and composite rather than accepting a blended face. Then skip to Step 2.

Otherwise, build it before any keyframe:

1. Write one locked **identity description** per cast member (age range, face shape, hair, skin, distinguishing features) and one **wardrobe description** per look. Reuse these strings verbatim in every later prompt.
2. Generate references with `image_selector` using the approved provider: front, three-quarter, profile, full-body per look. Fix the seed where the provider supports it. For a real artist, use the user's photos as references instead and skip generation.
3. Present the bible to the user — identity is the most expensive thing to fix later. Save under `assets/images/bible/<cast>_<look>_<view>.png` and register each with `subtype: "performer_bible"`.

### Step 2: Candidate budget and route dispatch

Read `skills/meta/shot-verifier.md`. Before generation use `candidate_budget`
from shot_verifier: keyframes <=3, clips default 2 and <=3. Quote and reserve all
candidate generation AND both verification calls per candidate using CostTracker;
announce tool/provider/model/reason and candidate count before EACH paid call.
Reconcile failed and rejected attempts too; warn at 75% and stop at 90%. User may
approve one candidate for a bounded sample; it must be recorded explicitly.

Dispatch on `scene.render_route`:
- story_i2v: create official-reference-conditioned keyframe candidates, verify
  and select one, then clip candidates from that keyframe.
- performance_lipsync: the same flow plus exact vocal slice and singing review.
- cast_multi_ref: use lib.artist_profile.cast_prompt for multi-person keyframes;
  preserve reference_map, submit every artist reference, score every artist.
- continuation: require a selected previous candidate whose verification is
  `status: pass, eligible: true`. Call `verified_continuation_frame` from
  lib.mv_routes (wraps lib.frames.extract_last_frame); it decodes to exact frame
  index N-1 without seek. Save `first_frame` provenance (clip hash and frame
  number) with the new candidate and retain official reference evidence. Never
  chain an unreviewed/failed/absent clip. Apply a lip-sync pass if lip_sync is true.

Capture actual transmitted references in generation_log from the submitted
provider request. Person-count evidence must be produced by free local detection
or explicit human frame inspection, hash-bound to the candidate, never inferred
from a prompt. If unavailable, surface manual review at the assets gate.

### Step 2a: Keyframes

For every generated shot, create a keyframe with `image_selector`, passing the bible images as references. The keyframe fixes composition, wardrobe, location, and light before paying for motion. Reject keyframes where identity drifts; regenerate before moving on.

### Step 3: Clips (image-to-video)

Generate with `video_selector` from the keyframe (`reference_image_path` / `reference_image_paths`), on the provider approved at proposal:

- **Duration:** shot length + handles, rounded up to the provider's supported length. For coverage-reuse clips, the longest supported length.
- **Prompt motion, not the picture.** The keyframe already holds the picture; the prompt describes camera move, subject action, and timing (e.g. "slow dolly-in, she lifts her head on the second half, hair moving in wind").
- **Native audio:** some providers return clips with generated sound. It is irrelevant — the final render replaces all audio with the master — but never use it as a reference for timing.
- **Frame rate:** note each provider's output fps; mismatched sources are conformed at compose, not by speeding up clips.
- Batch in waves of ~6 shots, review each wave against the bible, then continue. Announce each wave (tool, provider, model, count, cost).

### Step 4: Lip-sync passes

For each shot flagged in the scene plan:

1. Use `lib.song_audio.vocal_slice`: select **song_map.audio.stems.vocals** if available (legacy vocal_stem_path is supported), otherwise the locked master, and cut the vocal slice at exactly `vocal_in`→`vocal_out` from the shot, with sample-index `atrim`. Pass the shot's `handles_seconds` so the slice includes both handles; pad only the derived slice beyond song edges, preserving the master. Save as `assets/audio/lipsync/<shot_id>.wav`.
2. Run the approved lip-sync tool on the generated clip with that slice. For `kling_lip_sync`, run `identify_face` first and select the face explicitly; set `sound_start_time`/`sound_end_time` in milliseconds if passing the full file instead of a slice.
3. The lip-synced clip's frame 0 corresponds to `vocal_in - handles_seconds`; trimming the leading handle restores the intended song position. Record `vocal_source`, its kind (vocals/master), exact slice times and frame-0 mapping in `generation_summary` so the edit places it at exactly that time.
4. Review: does it read as *singing* (sustained vowels, breath, jaw on held notes) rather than talking? If not, try once more with a tighter crop or a different take; if it still fails, propose replacing the shot with non-lip-sync coverage rather than burning budget.

### Step 5: Supplied footage (footage_cut mode)

Use the `source_media_review` selects. Run `scene_detect` to get usable sub-clips, record in/out handles, and register each select with `source_tool: "user_supplied"`.

### Step 6: Lyric and title graphics

If the edit will use on-screen lyrics, no graphic assets are needed for `remotion_caption_burn` or the `LyricOverlay` composition — they render from timing data. For HyperFrames `lyric_video`, assets are authored at compose by the `music-to-video` workflow. Register fonts as `type: "font"` if a custom typeface is licensed.

### Step 7: Verify, select and register candidates

Run `shot_verifier` on each generated image and final clip, passing shot, fps,
reference_map, locked_traits, actual generation_log and person_count_evidence.
Reuse frame_sampler for the review contact sheet. Deterministic failure skips
ALL model calls; physical failure skips scoring. Unavailable model reports
unreviewed/manual_review and does not crash the production; arrange human review
at the existing gate, record its evidence, and don't automatically chain it.
Only eligible passes can be automatically selected. `select_candidate` chooses
the maximum composite (identity dimension is the minimum across artists).
All-failed selection returns revise_shot; no infinite retries and no silent
fallback. Any revised creative/provider choice follows existing approval rules.

Each generated asset has `candidates: [{path, attempt, verification,
cost_usd, generation_log, first_frame?}]` and `selected_candidate` (0-based
index, null when no eligible selection). The asset's path is the selected path;
never leave the rejected first attempt as the render source. Manifest total
cost includes EVERY attempt, not only selected_candidate. Source master/fonts
and provided footage may retain the old schema without candidates.

### Step 8: Manifest, budget, gate

Every asset entry carries `scene_id` (shot id or `global`), `source_tool`, `provider`, `model`, `prompt`, `seed`, `cost_usd`, `duration_seconds`, `resolution`. Keep `total_cost_usd` current. Warn the user at 75 % of budget and stop for approval at 90 %.

Validate the manifest; confirm every path exists on disk. Present: bible sheet, a contact sheet of keyframes per section, the lip-sync shots, total spend vs budget, and any shot that was replaced or downgraded (with the approval that allowed it). Checkpoint `awaiting_human` and **end your turn**.

## Common Pitfalls

- Generating clips before the bible is approved.
- Text-to-video for cast shots — identity will drift every shot.
- Lip-syncing with a slice that is a few hundred milliseconds off the shot window; the mouth will lead or lag the song for the whole shot.
- Regenerating the same failing lip-sync shot five times. Two attempts, then change the shot.
- Swapping provider mid-batch because one is slow. That changes the look and the cast — ask first.
