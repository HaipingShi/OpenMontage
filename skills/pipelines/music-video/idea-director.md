# Idea Director — Music Video Pipeline

## When to Use

First stage of `music-video`. You turn "make an MTV for my song" into a `brief` with a **concrete song source**, the scope of the deliverable, and a treatment hypothesis. You do not design shots, pick providers, or spend money here.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/brief.schema.json` | Artifact validation |
| Tools | `music_library`, `audio_probe`, `suno_music`, `music_gen` | Song source discovery |
| Reference | `skills/meta/video-reference-analyst.md` | When the user links a reference MV |

## Process

### Step 1: Resolve the song source (blocking)

Ask only what you cannot infer. The song is one of:

| Source | What to collect | `brief.metadata.song_source.source_type` |
|--------|-----------------|-------------------------------------------|
| User master file | Path; whether it is the final master; whether stems (esp. an **a cappella / vocal stem**) exist | `user_supplied` |
| Music library | Query `registry.get_by_capability("music_library")`, list tracks, let the user choose | `music_library` |
| Generate the song | Genre/style, mood, BPM feel, language, vocal gender/timbre, **lyrics** (user-written or drafted by you for approval), target length | `ai_generated` |

Run `audio_probe` on any existing file and report duration, sample rate, channels.

**Music-capability discovery is mandatory**, even if the user brought a file — they may want an alternative or a cutdown-friendly version. Check all three families and report what exists:

- `registry.get_by_capability("music_library")` — the user's own tracks in `music_library/`
- `registry.get_by_capability("music_search")` — royalty-free providers (instrumentals only; rarely right for an MV)
- `registry.get_by_capability("music_generation")` — e.g. `suno_music` (full songs with vocals and custom lyrics) or `music_gen` (instrumentals)

If the song will be generated, **do not generate here.** Write the generation brief into `brief.metadata.song_source.generation_brief`; generation happens at `song_analysis` after this gate is approved.

### Step 2: Rights (blocking)

Ask the user to confirm they have the right to make a video for this song (their own work, licensed, or AI-generated under the provider's terms). Record the answer verbatim in `brief.metadata.song_source.rights_statement`. Do not proceed on a commercially released song the user has no rights to — offer to make the video for an original or generated track instead.

### Step 3: Scope

Collect or default:

- **Full song or excerpt.** Default: full song. Excerpts (e.g. "first verse + chorus") are chosen on downbeats at `song_analysis`, not here.
- **Platform and aspect.** Default master 16:9 1920x1080 at 24 fps (cinematic) or 30 fps (performance/pop). If the primary platform is vertical (TikTok, 抖音, Reels, Shorts, 视频号), master at 9:16 1080x1920 instead of reframing later. `brief.target_platform` only accepts `youtube | instagram | tiktok | linkedin | generic`; use `generic` for Bilibili, 网易云, QQ 音乐, Weibo, 视频号 and record the real platform in `brief.metadata.platform_detail`.
- **Artist presence.** Does a real artist appear (footage/photos supplied), a generated performer, an animated character, or nobody?
- **Persistent artist.** Run `python -m lib.artist_profile list`. If the video is for an artist in `artists/`, set `metadata.artist_id` and `artist_presence: "persistent_artist"`, load it with `lib.artist_profile.load_artist`, and check the brief against `persona.never` and `persona.canon` — refuse or reshape anything that crosses them. Check the actual route: `check_artist(profile, voice_route="suno_web")` for manual selection of an existing Suno binding, `local_reference` for local audition comparison, or `visual_only` when an approved finished song is already supplied and no singing generation is needed. If issues remain, route to `skills/meta/digital-artist.md`. An existing Suno voice needs binding/import, not a new audition. The CLI report separates external binding, local sample availability, and generation capabilities; do not call a text style signature a voice reference. If the user wants a recurring artist that does not exist yet, create it there before this pipeline.
- **Joint release (two or more persistent artists).** Set `metadata.artist_ids` to the list in billing order (e.g. `["amor", "fati"]`) and `metadata.artist_id` to the first. Every artist must list the others in `collaborators`; check the brief against every artist's `persona.never` and `persona.canon`, including the `relation` recorded there.
- **Supplied media.** Footage, photos, logos, album art. If any exist, a `source_media_review` is required before the proposal (see AGENT_GUIDE.md).

### Step 4: Treatment hypothesis

Name the likely mode mix from the five `production_modes` in the manifest — `performance`, `narrative`, `concept`, `lyric_video`, `footage_cut` — in one sentence, e.g. *"Narrative verses about leaving a small town, performance choruses on a rooftop at night."* This is a hypothesis for the proposal to challenge, not a decision.

If the user linked a reference MV, run `skills/meta/video-reference-analyst.md` first and summarize pacing (average shot length per section), mode mix, palette, and the signature image. You will need this at proposal.

### Step 5: Write the brief

Map to the schema (`additionalProperties: false` — put everything music-specific in `metadata`):

```json
{
  "version": "1.0",
  "title": "<Song> — <Artist> (Official Music Video)",
  "hook": "<the one image or idea the video will be remembered by>",
  "key_points": ["<what the song is about>", "<emotional turn>", "<what the chorus should feel like>"],
  "tone": "<e.g. euphoric, melancholic, defiant>",
  "style": "<treatment hypothesis in one sentence>",
  "target_platform": "youtube",
  "target_duration_seconds": 214,
  "metadata": {
    "song_source": {
      "source_type": "user_supplied",
      "path": "music_library/<file>.wav",
      "has_vocal_stem": false,
      "rights_statement": "<user's words>",
      "generation_brief": null
    },
    "artist_presence": "generated_performer",
    "artist_id": null,
    "artist_ids": [],
    "excerpt_requested": false,
    "aspect": "16:9",
    "fps": 24,
    "platform_detail": null
  }
}
```

Set `target_duration_seconds` to the probed song length, or — if the song is not generated yet — to the generation brief's target length (the schema requires ≥ 1). `song_analysis` sets the truth in `song_map`.

### Step 6: Gate

Present: song source, rights, scope, treatment hypothesis, and what each capability gap means (e.g. "no video generation key → only `lyric_video` and `footage_cut` modes are possible"). Checkpoint `awaiting_human` and **end your turn**.

## Common Pitfalls

- Starting shot ideas before the song exists. Lyrics and arrangement change everything downstream.
- Treating "use a famous song" as fine. Rights are the user's statement, recorded, not your assumption.
- Defaulting to 16:9 when the user's audience lives on vertical platforms.
- Forgetting to ask about stems. A vocal stem makes lyric alignment and lip-sync dramatically better.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary, and **END YOUR TURN**.
