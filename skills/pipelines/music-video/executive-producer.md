# Executive Producer — Music Video Pipeline

## When to Use

You are the **Executive Producer (EP)** for a music video (MTV-style promo, performance video, narrative video, lyric video, visualizer, or a beat-cut of supplied footage). You orchestrate `pipeline_defs/music-video.yaml` serially and own the gates.

Use this pipeline whenever **a song exists or will exist before any picture is made** and the picture must serve the song. If the user wants a trailer or brand film that merely *has* music, use `cinematic`. If they want a pure typography visualizer with no stage gates, the HyperFrames `music-to-video` Layer 3 skill can run standalone — but inside OpenMontage, route it through this pipeline's `lyric_video` mode so the song_map, decision log, and Backlot board exist.

## The One Law

**The song is the clock.** The song master is locked at `song_analysis` and never re-timed, time-stretched, looped, trimmed, re-mixed, ducked, or normalized after that. Every shot, cut, lyric, and lip-sync window conforms to `song_map`. If a picture problem tempts you to touch the audio, the answer is always to change the picture.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Pipeline | `pipeline_defs/music-video.yaml` | Stages, modes, tools, gates |
| Skills | 9 stage directors + `creative/beat-sync-editing` + `meta/reviewer` | Stage execution |
| Tool | `song_beatmap` | The single timing spine (wraps the `music-to-video` beat analyzer) |
| Schemas | `song_map` + the standard artifact schemas | Validation |

## Cumulative State

```
EP_STATE:
  pipeline: music-video
  project_id: <kebab-case from song title + artist>
  budget_total_usd: <configured, default 15>
  budget_spent_usd: 0.0

  # Music-video specific
  song_master_path: null       # locked at song_analysis; never changes afterwards
  song_duration_seconds: null  # from song_map.audio.duration_seconds
  rhythmic_confidence: null    # rhythmic | loose | free
  section_mode_mix: {}         # section_id -> performance|narrative|concept|lyric_video|footage_cut
  performer_bible: null        # cast ids -> reference image asset ids (locked at assets, designed at proposal)
  lip_sync_scope: null         # none | hooks_only | all_sung_closeups
  render_runtime: null         # locked at proposal

  artifacts: { idea, song_analysis, proposal, script, scene_plan, assets, edit, compose, publish }
  revision_counts: {}
  issues_log: []
```

## Execution Protocol

`idea → song_analysis → proposal (+ sample) → script → scene_plan → assets → edit → compose → publish`

Each stage: PREPARE (read the stage director skill) → EXECUTE → SELF-REVIEW (`meta/reviewer`) → CHECKPOINT → GATE.

At pipeline start:

1. `python -c "from lib.checkpoint import init_project; init_project('<project-id>', title='<Song — Artist>', pipeline_type='music-video')"`
2. `python -m backlot open <project-id>` (non-blocking; continue if it fails).
3. Run preflight (`registry.provider_menu_summary()`) and present the capability menu. For this pipeline, call out specifically: video generation (critical for every mode except `lyric_video` and `footage_cut`), lip-sync, music generation (only if the song does not exist yet), transcription, and both composition runtimes.

### Decision communication (binding, from AGENT_GUIDE.md)

Before every paid call, announce tool, provider, model, reason, and sample-vs-batch. Never switch provider/model/runtime, drop lip-sync, or downgrade motion to stills without asking. Re-log changed decisions with the same `(category, subject)` pair.

## Cross-Stage Checks

### After IDEA
```
CHECK: Song source is concrete
  - user master path exists, OR library track chosen, OR a generation brief (style, lyrics, length) awaits approval
  - rights/usage stated by the user (own song, licensed, AI-generated under provider terms)
  - full song vs excerpt, platform, and aspect explicit
```

### After SONG_ANALYSIS
```
CHECK: The clock is locked
  - ffprobe duration of the master == song_map.audio.duration_seconds (±0.05s)
  - every section label_confidence == "confirmed"
  - lyrics: alignment_coverage reported; any unaligned sung line fixed or flagged to the user
  - rhythmic_confidence sanity: ballads/rubato should not be "rhythmic" with 1-beat cuts
```

### After PROPOSAL (+ sample)
```
CHECK: Treatment is buildable and honest
  - section-by-section mode mix explicit; chorus payoff named
  - performer bible plan: who, how many looks, which provider keeps identity
  - lip-sync scope + vocal-stem situation stated; cost per lip-sync shot shown
  - render_runtime locked after presenting both runtimes (render_runtime_selection logged)
  - sample (one chorus phrase) approved before batch generation
```

### After SCRIPT
```
CHECK: Script tiles the song
  - one script section per song_map section, identical boundaries
  - every section has mode + beat/motif + lyric relationship
```

### After SCENE_PLAN
```
CHECK: Shot list is on the grid
  - every shot boundary is a song_map cut point or phrase boundary
  - generated shots: planned clip length >= shot length + 0.5s handles, within provider limits
  - lip-sync shots carry vocal in/out seconds
  - total shot count and generation count reconciled against budget (coverage reuse planned)
```

### After ASSETS
```
CHECK: Identity and motion
  - performer bible images exist; every cast shot references them
  - no still-image substitutes for motion shots without approval
  - lip-sync shots driven by the exact song slice for their window
  - budget: warn at 75%, stop and ask at 90%
```

### After EDIT
```
CHECK: Sync and contour
  - all cuts within 1 frame of a grid point; timeline length == song duration
  - song master is the only audio; music volume 1.0, no ducking, no fades unless excerpt outro approved
  - edit density rises into choruses and peaks at the final chorus
```

### After COMPOSE
```
CHECK: Output
  - ffprobe: duration == song (±1 frame), resolution, fps, audio stream present
  - sync spot-checks at first chorus downbeat, each hard stop, final hit — recorded in final_review
  - render_runtime unchanged from proposal
```

## Quality Gates Summary

| Gate | After | What | Fail action |
|------|-------|------|-------------|
| G0 | idea | Song source, rights, scope | Revise |
| G1 | song_analysis | Locked master, confirmed sections, lyric timing | Revise |
| G2 | proposal | Treatments, mode mix, cast, lip-sync scope, runtime, cost | Revise |
| G2s | sample | One chorus phrase end-to-end | Revise or send back to proposal |
| G3 | script | Section tiling, lyric relationship | Revise |
| G4 | scene_plan | Grid-locked shot list, budget reconcile | Revise |
| G5 | assets | Identity continuity, motion, lip-sync, budget | Revise |
| G6 | edit | Frame-accurate sync, energy contour | Revise |
| G7 | compose | Probe, sync spot-checks, runtime lock | Revise or send back |
| G8 | publish | Cutdowns, credits | Revise |

## Execution Limits

| Limit | Value |
|-------|-------|
| Max revisions per stage | 3 |
| Max total send-backs | 3 |
| Default budget | $15 (state the scaling: a 3-minute fully generated video with lip-sync commonly lands at $25–80) |
| Max wall time | 60 minutes of active agent time; generation queues are reported, not hidden |

## Common Pitfalls

- **Touching the audio.** Re-encoding with drift, normalizing a mastered track, or letting a renderer apply its default music volume/fades (CinematicRenderer defaults music to 0.15 with fades) — always pass the master as `audio_path` to `video_compose` so the final mux replaces the audio with the untouched master.
- **Cutting on every beat for four minutes.** Density is a contour, not a constant. Verses breathe; choruses hit.
- **A different face every shot.** Without a locked performer bible and image-to-video from bible keyframes, generated cast drifts. This is the #1 quality failure of AI music videos.
- **Lip-syncing everything.** It is the most expensive and most fragile shot class. Spend it on hook lines and chorus close-ups; cover the rest with performance coverage that does not need mouths (wides, silhouettes, instruments, backs, slow motion with closed mouth).
- **Chorus amnesia.** Each chorus should recall a signature image and escalate it. Three unrelated choruses read as three different videos.
- **Silent downgrade.** If video generation is blocked mid-run, stop and escalate; do not quietly ship a Ken Burns slideshow.


## AutoMV-informed optional tools and controls

- stem_separator (optional demucs/htdemucs): content-hash cache, master sample
  rate, vocal transcription/alignment and lip-sync; missing tool uses master.
- song_beatmap: Suno-record > user > transcription lyric precedence; singer tags,
  matched-token confidence and review_flags (<0.6 by default) shown at G1.
- lib.timeline: configurable 3–15s narrative segments, quantized frame grid;
  validate_timeline MUST run before scene_plan and edit artifacts are written.
- Routes: story_i2v, performance_lipsync, cast_multi_ref, continuation. Exact
  last-frame provenance from lib.frames; only verified predecessors can chain.
- shot_verifier: optional selected multimodal reviewer after free checks, then
  physical pass/fail, then per-artist scores (identity minimum). Optional model
  absence requires manual evidence at G5, not fake automatic approval. Keyframes
  <=3 candidates; clips default 2, <=3. All candidates count toward budget.
- mv_judge: optional full-video + master review; AutoMV's twelve criteria with
  20/20/30/30 weights. AI novelty excluded by default; report "仅供参考" and actual
  model in final_review.metadata.judge. NEVER a publish gate.

Review providers come from registry capability multimodal_review through existing
explicit provider/tool/model routing; no Gemini/Doubao dependency. No SongFormer
mandatory backend or new beat tracker. Section labels stay heuristic until the
director confirms them. Optional tools are not preflight blockers; report
UNAVAILABLE and the explicit degraded/manual path. See docs/automv-upgrade.md.
