# Proposal Director — Music Video Pipeline

## When to Use

Third stage of `music-video`. With the song locked and mapped, you design **treatments** — what the video *is* — and the production plan to build one. This is the money gate: nothing is generated in batch until the user approves the proposal **and** the chorus sample.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/proposal_packet.schema.json`, `decision_log.schema.json` | Validation |
| Prior artifacts | `brief`, `song_map` (+ `video_analysis_brief`, `source_media_review` if present) | Song truth, scope, references |
| Manifest | `pipeline_defs/music-video.yaml` → `production_modes` | The five treatment modes |
| Meta | `skills/meta/taste-direction.md`, `skills/meta/animation-runtime-selector.md` | Taste dials, runtime fit |
| Creative | `skills/creative/beat-sync-editing.md` | Cut grammar the treatment must respect |

## Runtime Selection (required field — `render_runtime`)

Read `AGENT_GUIDE.md` → "Present Both Composition Runtimes (HARD RULE)" and `skills/core/hyperframes.md`.

1. Query `video_compose.get_info()["render_engines"]`.
2. If both `remotion` and `hyperframes` are `True`, **present both** with brief-specific analysis:
   - **Remotion** — fits video-led music videos (`CinematicRenderer` with `<OffthreadVideo>` clips on the song timeline) and is the only runtime with word-level karaoke lyrics (`remotion_caption_burn`) and the line-level `LyricOverlay` composition. Tradeoff: kinetic typography beyond those components must be hand-authored in React.
   - **HyperFrames** — fits typography-led `lyric_video` treatments and beat-reactive HTML/GSAP motion; the `music-to-video` Layer 3 skill is a complete beat-synced lyric-video workflow that consumes the `audiomap.json` written by `song_beatmap`. Tradeoff: no word-level caption burn parity; heavy generated-clip timelines are less proven there.
   - **ffmpeg** — only when the treatment is a pure cut of video clips with no graphics (fast, cheap, no lyric text).
3. Recommend one, tied to the treatment's dominant mode and whether on-screen lyrics are part of the deliverable.
4. Wait for explicit approval, then write `production_plan.render_runtime`.
5. Log a `render_runtime_selection` decision listing every runtime considered (both, plus ffmpeg when realistic). A single-option entry when both were available is a CRITICAL reviewer finding.

If only one runtime is installed, say so explicitly and record the other as `rejected_because: "runtime not available on this machine"`.

Also log `composition_mode` (templated vs atelier). Default **atelier** for a hero single release; templated for visualizers, drafts, and batch lyric videos.

## Process

### Step 1: Read the song

From `song_map`, extract and keep in front of you: duration, BPM and `rhythmic_confidence`, section table, hook line, choruses count, bridge/breakdown location, hard stops. Every treatment is a plan *for this song's structure* — name sections by id.

### Step 2: Run preflight for the treatment-relevant capabilities

- `video_generation` providers (via `video_selector`): which support image-to-video and reference images? This determines whether a consistent cast is feasible.
- `image_generation` (via `image_selector`): for the performer bible and keyframes.
- `avatar` lip-sync: `kling_lip_sync` (cloud) and/or `lip_sync` (local). Note per-shot cost and the vocal-stem situation from the brief.
- Runtimes, as above.

### Step 3: Design at least 3 treatments

Each treatment specifies:

| Field | Content |
|-------|---------|
| Logline | One sentence a director would pitch |
| Mode mix by section | e.g. `intro: concept · verse: narrative · pre_chorus: narrative (tighter) · chorus: performance · bridge: concept · final chorus: performance + narrative converge` |
| Signature image | The recurring image each chorus returns to and escalates |
| Visual world | Locations, era, palette (3–5 hex), lighting key, texture (grain/clean/VHS/anamorphic), camera language |
| Cast | Who appears, how many looks (wardrobe/hair), and whether it is a real artist (supplied photos), a generated performer, or a character |
| Lyric relationship | Literal, counterpoint (image contradicts the line), or abstract — per section |
| On-screen lyrics | None, hook-only, full karaoke, or lyric-video |
| Cut grammar | Average shot length per section derived from `song_map.sections[].cut_unit_beats` |
| Risk | The one creative risk this treatment takes |

**Diversity rule:** no two treatments share a dominant mode *and* a visual world. At least one should be cheaper (e.g. concept/lyric-led) and one should be the ambitious version.

If a `video_analysis_brief` exists, each treatment names one element kept from the reference and one changed — no carbon copies.

Fill `concept_options[]` per the schema; use `narrative_structure: "story"` for narrative-led treatments and `"journey"` for concept-led ones.

### Step 4: Performer bible plan (when cast appears)

For the selected treatment, specify what `assets` will build first:

- Per cast member: 1 front, 1 three-quarter, 1 profile reference, plus 1 full-body per wardrobe look, all generated from one locked description and seed (or from user-supplied photos of the real artist).
- Persistent artist: the bible already exists in `artists/<id>/bible/` — cost $0. Plan only new looks, and treatments must stay inside `persona.never`, `visual_language`, and `music` from the profile.
- Which video provider will carry the cast (one provider per cast for identity consistency).
- Keyframe-first rule: every cast shot = bible-referenced keyframe image → image-to-video. No text-only video generation for cast shots.

### Step 5: Lip-sync scope

Choose and justify: `none`, `hooks_only` (recommended default), or `all_sung_closeups`. State:

- vocal source (stem vs full mix — full mix degrades results; recommend asking the artist for an a cappella),
- count of lip-sync shots and cost each,
- the coverage strategy for sung lines that are not lip-synced (wide shots, backs, silhouettes, instruments, closed-mouth slow motion).

### Step 6: Music plan

The song is already locked; record its exact path in `production_plan.music_source`. Map the song-source vocabulary to the proposal schema: `user_supplied` → `bring_your_own`, `music_library` → `user_library`, `ai_generated` → `ai_generated`. Keep the original source type in `brief.metadata.song_source` and `song_map.audio.source_type`. There is no second background-music bed.

### Step 7: Cost estimate

Per section and per shot class, not a lump sum:

```
Performer bible (6 images × $0.04)                        $0.24
Keyframes (28 × $0.04)                                    $1.12
Video clips (28 × 5s i2v × $0.35)                         $9.80
Lip-sync (6 hook close-ups × $0.50)                       $3.00
Sample (chorus phrase, included above)                    —
Total                                                    $14.16   budget $15  → near_limit
```

Show the coverage-reuse saving explicitly (e.g. "chorus 1/2/3 share 8 coverage clips cut differently: −$4.20").

### Step 8: Sample sub-stage (mandatory)

After the proposal is approved, produce **one chorus phrase (4–8 bars)** end-to-end: bible images → keyframes → clips → (one lip-sync shot if in scope) → cut on the grid → render with the master slice. Present it. Batch generation starts only after the sample is approved. If the sample fails on identity or lip-sync, revise the plan here rather than discovering it after 60 clips.

For `lyric_video`, sample the timed typography with optional background plates; for `footage_cut`, sample approved footage selects. Neither mode requires image or video generation. Record the sample window, file, and approval in the proposal checkpoint’s `metadata`; do not invent a top-level `proposal.sample` checkpoint stage.

### Step 9: Submit

`approval.status: "pending"` until the user approves. Validate `proposal_packet` and `decision_log`. Decisions to log: `concept_selection`, `render_runtime_selection`, `composition_mode`, `provider_selection` (video, image, lip-sync — distinct subjects), `music_source`, `motion_commitment`.

## Common Pitfalls

- Three treatments that are all "moody neon city at night".
- Treatments that ignore the song structure ("a girl walks through a forest" for 3:40 with no chorus payoff).
- Promising a consistent generated performer without checking that a reference-image-capable video provider is configured.
- Burying lip-sync cost. It is often the largest single line.
- Locking `render_runtime` before the user chose between the runtimes presented.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`) and has a mandatory
sample sub-stage. Checkpoint `awaiting_human`, present, and **END YOUR TURN** — once for the
proposal, again for the sample. An earlier "go ahead" does not cover either gate.
