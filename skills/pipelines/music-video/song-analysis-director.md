# Song Analysis Director — Music Video Pipeline

## When to Use

Second stage of `music-video`. You **lock the song master** and produce the `song_map` — the timing spine every later stage obeys. If the song still needs to be generated, it is generated here (the idea gate already approved the generation brief).

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/song_map.schema.json` | Artifact validation |
| Prior artifact | `brief` (`metadata.song_source`) | Where the song comes from |
| Tools | `song_beatmap` (required), `transcriber`, `audio_probe`, `suno_music` / `music_gen` | Analysis, lyric timing, generation |
| Layer 3 | `.agents/skills/music-to-video/SKILL.md` → "One analyzer, and you trust it" | Why there is exactly one beat analyzer |
| Layer 3 | `.agents/skills/music/SKILL.md`, `.agents/skills/speech-to-text/SKILL.md` | Generation and transcription specifics |

## Process

### Step 1: Secure the master

- **User file / library track:** copy it to `projects/<id>/assets/music/master.<ext>` unchanged (no transcoding). Keep the original extension; WAV/FLAC preferred.
- **Generate:** announce tool/provider/model/cost first. With `suno_music`, use `custom_mode: true`, `instrumental: false`, the approved lyrics as `prompt` (with `[Verse] [Chorus] [Bridge]` tags), `style`, and `title`; prefer `V4_5`/`V5` for songs longer than 4 minutes. Each call returns 2 takes — let the user listen and pick (`track_index`). Write to `projects/<id>/assets/music/master.mp3`.
- **Persistent artist:** set `style` to `lib.artist_profile.music_style(profile, "<song-specific additions>")` — never hand-write it — and use `voice.model`. If an existing Suno Persona/Voice is bound, use the approved Suno web workflow and manually select that binding; import the downloaded result. The current `suno_music` tool cannot transmit that binding or reference audio, so do not generate a replacement voice with it. Text style and singer tags do not enforce timbre. Before the user picks, compare each take against the artist's canonical voice track (register, breathiness, vibrato, accent) and flag drifted takes.
- **Joint release:** set `style` to `lib.artist_profile.duet_style(profiles, "<song-specific additions>")` and mark every lyric section with its singer (`[Verse 1: Amor]`, `[Verse 2: Fati]`, `[Chorus: Amor & Fati]`). Compare each singer's parts against that singer's own canonical track. Do not assume one web voice selection or a duet style prompt enforces two saved voices. If the current Suno interface cannot select both, explain that limitation and obtain approval for separately generated singer parts and an assembled duet.

Record a `music_source` decision in `decision_log` (category `music_source`, subject `Song master`).

### Step 2: Excerpt (only if requested)

1. Run `song_beatmap` on the full song first.
2. Choose start/end on **downbeats** that sit on section boundaries (e.g. intro start → end of chorus 1). Prefer ending on the downbeat *after* the last hit so the final chord rings.
3. Cut sample-accurately with ffmpeg (`-ss`/`-to` **after** `-i`, re-encode to WAV), add a short fade-out only if the end is not a natural stop, and save as `master_excerpt.wav`.
4. Re-run `song_beatmap` on the excerpt so all times are video-timeline times. Record the excerpt in `song_map.audio.excerpt_of`.

### Step 3: Vocal separation, then lyric timing

Run optional `stem_separator` on the locked master first, with `output_dir` under
`projects/<id>/assets/audio/stems/`. It uses htdemucs, caches by the input bytes,
and restores the master's sample rate for both stems. On success write only
`{vocals, accompaniment, model}` into `song_map.audio.stems`. Reuse a supplied
vocal stem when present. UNAVAILABLE/failure is a reported analysis degradation:
use the master for transcription; do not install dependencies or crash silently.

Lyrics precedence is Suno generation-record lyrics (`suno_lyrics_text`) > user
lyrics (`lyrics_text`) > transcription. Pass both sources to song_beatmap so it
records the real source. Do not fetch lyrics from the internet. Preserve singer
tags such as `[Verse 1: Amor]` and `[Chorus: Amor & Fati]`; the analyzer records
the tag and singer for every following line. Never infer singer from timbre.


If the song has vocals:

1. Best: run `transcriber` on the **vocal stem** (`song_map.audio.stems.vocals`, or a supplied vocal stem). Otherwise run it on the full mix — sung vocals over a full mix transcribe poorly, so use `model_size: "large-v3"` and set `language` explicitly.
2. If the user supplied lyrics (or lyrics were used to generate the song), pass them as `lyrics_text`. They are authoritative; the transcript only provides timing. Chinese, Japanese, and Korean lyrics align per character — no word segmentation needed.

### Step 4: Run the analyzer

```python
from tools.tool_registry import registry
registry.discover()
result = registry.get("song_beatmap").execute({
    "input_path": "projects/<id>/assets/music/master.wav",
    "output_path": "projects/<id>/artifacts/song_map.json",
    "transcript_path": "projects/<id>/assets/audio/master_transcript.json",  # optional
    "lyrics_text": "<authoritative lyrics, one line per row>",              # optional
    "suno_lyrics_text": "<approved generation-record lyrics>",             # optional; higher priority
    "lyrics_confidence_threshold": 0.6,
    "stems": {"vocals": "<separated vocals.wav>", "accompaniment": "<accompaniment.wav>", "model": "htdemucs"},  # only on success
    "language": "zh",                                                       # optional
    "source_type": "user_supplied",
})
print(result.data["summary"])
```

It also writes `audiomap.json` (the raw `music-to-video` analyzer output) next to the song_map — the HyperFrames `lyric_video` path consumes it directly.

### Step 5: Confirm sections by ear and by lyric (mandatory)

The tool's labels are **heuristic** (energy-based). You must confirm them:

- Lyrics are the strongest evidence: repeated lyric blocks are choruses; the block before each chorus with rising tension is a pre-chorus; a new lyric block late in the song with a texture change is the bridge.
- Use `key_moments` (SURGE/DROP), `hard_stops`, and `silences` to adjust boundaries — but move a boundary only to another downbeat.
- Merge or split sections where the energy heuristic disagrees with the arrangement (a quiet chorus is still a chorus).
- Mark the **hook line(s)** with `"hook": true` on the lyric line — the line the video's identity hangs on.
- Set every section's `label_confidence` to `"confirmed"` and add `notes` for anything unusual (half-time feel, key change, tempo change).

### Step 6: Sanity-check the grid

- `rhythmic_confidence = "free"` or `"loose"`: do not plan 1–2 beat cuts anywhere; pace by phrase and lyric line.
- A ballad marked `"rhythmic"` with HIGH-energy 1-beat cut units in the chorus is usually wrong — raise `cut_unit_beats` to 4 or 8 and note why.
- Double/half-time errors: if the BPM is clearly 2x or 0.5x what you hear, say so in `notes` and size cut units accordingly (the grid points are still valid beats).

### Step 7: Verify and checkpoint

- ffprobe the master; its duration must match `song_map.audio.duration_seconds` within 0.05 s.
- Validate the song_map against its schema.
- Each lyric line has confidence based on matched timed tokens, not a claim of
  semantic accuracy. Default threshold is 0.6 (user-configurable). Display EVERY
  `song_map.review_flags` line with id, text, singer, confidence and time range at
  the `song_analysis` checkpoint; require human confirmation/correction. Preserve
  flags until explicitly resolved, recording corrections in checkpoint metadata.
  Wordless transcript segments are timed but carry confidence 0, not invented
  confidence 1. Confirmed corrections must not relabel all timings as certain.
- Present to the user: BPM and feel, a section table (label, time range, bars, energy), lyric coverage (e.g. "38 of 41 lines timed; 3 ad-libs unaligned"), the hook line, and the cutdown windows. Ask them to confirm the section map — they know their song.

## Common Pitfalls

- Running a second beat tracker "to double-check". There is one analyzer; disagreements are resolved by listening, not by voting.
- Leaving labels heuristic. The proposal's chorus payoff and the scene plan's hero moments depend on knowing where the choruses really are.
- Analyzing the full song and then editing an excerpt — every downstream time would be offset.
- Trusting full-mix transcription for timing without spot-checking three lines by ear.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary, and **END YOUR TURN**.
After approval the master is locked for the rest of the run.
