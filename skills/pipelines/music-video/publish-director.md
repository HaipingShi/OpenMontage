# Publish Director — Music Video Pipeline

## When to Use

Final stage of `music-video`. Package the hero master, build hook cutdowns for short-form platforms, and write honest metadata and credits.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Validation |
| Prior artifacts | `render_report`, `final_review`, `song_map`, `proposal_packet` | What exists, hook windows |
| Tools | `auto_reframe`, `video_trimmer`, `video_compose` | Cutdowns and reframes |
| Creative | `skills/creative/short-form.md` | Vertical platform conventions |

## Process

### Step 1: Hero master

`renders/final.mp4` is the deliverable. Note duration, resolution, fps, audio codec/bitrate. If the user needs a delivery spec (e.g. a distributor requiring ProRes or 48 kHz PCM), produce it from the same picture and the original master — never from the AAC file.

### Step 2: Hook cutdowns

Use `song_map.cutdown_windows` (15 s and 30 s windows anchored one bar before the strongest chorus). For each:

1. Trim picture and audio from the hero render on the window's downbeats — both ends on downbeats, so the loop point is musical. Add a short audio fade-out (≤ 1 beat) only at the end.
2. For vertical platforms, reframe with `auto_reframe` (face-tracked) unless the master was already 9:16. Check that lyric text and faces stay inside the 9:16 safe area (avoid the bottom ~20 % where platform UI sits).
3. Name files `renders/cutdown_<15|30>s_<aspect>.mp4`.

If the user wants a different hook (e.g. the bridge), take it from a confirmed section boundary in `song_map`, not from scrubbing.

### Step 3: Thumbnail / poster frame

Pick 3 candidate frames from hero moments (`scene_plan` shots with `hero_moment: true`), extract stills, and present them. The signature image is usually the right one.

### Step 4: Metadata and credits

Draft per platform: title (`<Artist> — <Song> (Official Music Video)` unless the user prefers otherwise), description with lyrics credit and links, tags. **Disclose AI generation** (which visuals and, if applicable, the song) in line with the platform's policy — YouTube, TikTok/抖音, Bilibili, and Instagram all have AI-content labels. List providers and models from the decision log in the credits block.

### Step 5: Publish log

Write `publish_log` listing every file with path, duration, aspect, intended platform, and the metadata drafts. Present the package and **end your turn** for approval.

## Common Pitfalls

- Cutdowns that start mid-bar and loop with a hiccup.
- Vertical reframes that crop the lyric text or the singer's mouth.
- Omitting AI disclosure, which can get the upload restricted.
