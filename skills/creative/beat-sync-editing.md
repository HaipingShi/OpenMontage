# Beat-Sync Editing

## When to Use

Any edit where picture must lock to music: the `music-video` pipeline (primary), and montage sections of `cinematic`, `documentary-montage`, or `clip-factory` when a track drives the cut. Timing facts come from `song_beatmap` (`song_map`); this skill is the grammar for using them.

## Core Rules

1. **Audio is the clock.** Cut the picture to the music; never stretch, loop, or trim the music to fit the picture.
2. **Cut on grid points only.** Every cut is a `song_map` beat, downbeat, phrase start, or aligned lyric-line start. Never a hand-typed time.
3. **Section changes cut on the downbeat.** The first frame of a chorus, drop, or bridge is a new shot — usually the hero shot.
4. **Density is a contour.** Shot length follows song energy; a constant cut rate for minutes is fatigue, not energy.
5. **The vocal owns the frame.** Do not cut in the middle of a sung word on a lip-synced shot; let the hook line land and finish on one shot.

## Where to Cut (in priority order)

| Anchor | Use for |
|--------|---------|
| Section boundary downbeat | Mode/location changes, hero shots |
| Bar downbeat (beat 1) | Default cut point in verses and choruses |
| Backbeat (beats 2 and 4 — the snare) | Extra punch in choruses; cut on the snare for a "hit" feel |
| Off-beat / 8th-note | Only inside rolls and fills, as flash cuts |
| Lyric line start | Phrase-flow sections, ballads, lyric videos |
| Hard stop / silence | Hold, black, or freeze — silence is a picture event too |

Cut 1–2 frames **before** a heavy transient if the shot change feels late at full speed; perception leads the waveform slightly on big hits. Do this consistently or not at all, and only on a `rhythmic` grid.

## Density Contour

Default target shot length by section energy (the `song_beatmap` cut units implement this):

| Level | Typical shot length | Cut unit at 120 BPM |
|-------|---------------------|---------------------|
| HIGH (chorus, drop) | 1–2 s | 2–4 beats |
| MEDIUM (verse, pre-chorus) | 2–4 s | 4–8 beats |
| LOW (intro, breakdown) | 4–8 s | 8–16 beats |
| VOID (silence, hard stop) | Hold | — |

Shape across the song: intro slow → verse holds → pre-chorus tightens → chorus hits → verse 2 slightly faster than verse 1 → bridge breaks the pattern (a long take, slow motion, or a single static frame) → final chorus densest → outro releases.

`rhythmic_confidence` overrides: `loose` → cut on bars only; `free` → cut on phrases and lyric lines only, never on the imposed grid.

## Motion on the Beat

A cut is strongest when motion **inside** the shot also hits the grid: a head turn, a footstep, a light flash, a camera whip landing on the downbeat. Choose clip in-points so the action lands on the beat rather than shifting the cut off the beat to meet the action.

## Repetition and Escalation

Choruses repeat; the edit should too — with escalation. Establish a signature shot in chorus 1, return to it in chorus 2 with one change (more light, more people, closer), and pay it off in the final chorus. Re-cut coverage clips with new in-points so repeats rhyme without being identical.

## Frame Math

Convert boundaries to frames once and derive durations from the rounded boundaries:

```
f_i      = round(t_i * fps)          # every boundary, including 0 and song end
frames_i = f_{i+1} - f_i             # integer frames per shot
span_i   = frames_i / fps * speed_i  # seconds to take from the source clip
```

Never round durations independently — 70 shots × half a frame of error is a beat of drift by the last chorus. The last boundary is `round(song_duration * fps)`.

## Verification

- Scene-detect the render and compare detected cuts with planned boundaries: ≥ 95 % within 1 frame.
- Watch the first chorus at full speed with sound; scrubbing hides sync errors.
- On lip-sync shots, a lead or lag above ~2 frames is visible; above ~4 frames reads as a dub.

## Common Pitfalls

- Cutting on every beat for an entire song.
- Cutting on the imposed grid of a rubato ballad.
- Fades on every cut because a renderer defaulted them on.
- Letting a strong shot run past the chorus downbeat because "it's a good shot".
