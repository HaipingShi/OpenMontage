# Script Director — Music Video Pipeline

## When to Use

Fourth stage of `music-video`. A music video has no narration — its "script" is the **treatment written against the song's sections**: what happens in each section, what the lyrics are doing, and how each chorus pays off. The script is the bridge between the approved treatment and the shot list.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifacts | `song_map`, `proposal_packet` | Section timings, approved treatment |

## Process

### Step 1: One script section per song section

For every `song_map.sections[]` entry, in order, write one `script.sections[]` entry:

| script field | Source |
|--------------|--------|
| `id` | `sec-<song_section_id>` (e.g. `sec-s03`) |
| `label` | The song label + a story label, e.g. `"chorus 1 — the rooftop"` |
| `start_seconds` / `end_seconds` | **Copied exactly** from the song_map section — never rounded or adjusted |
| `text` | The lyrics sung in this section (from `song_map.lyrics.lines` where `section_id` matches), or `"[instrumental]"` |
| `delivery_cues` | Object using the existing schema: `{"energy": "peak", "delivery_note": "mode:performance; signature_image:return+escalate; lyrics:hook_only"}`. These describe the picture; do not generate narration. |
| `enhancement_cues` | Array of objects with `type` and `description`, describing what we see, what changes, and what it means; use `broll`, `animation`, or `overlay` as appropriate. |
| `source_ref` | The song_map section id |

`total_duration_seconds` = `song_map.audio.duration_seconds`. Sections must tile the song with no gaps or overlaps.

### Step 2: Write each section's visual beat

Use the classic music-video arc as a default, then break it on purpose where the treatment says so:

| Section | Default job |
|---------|-------------|
| Intro | Establish the world and plant the signature image in its weakest form |
| Verse 1 | Set up character/situation; wider shots, longer holds, room for the lyric |
| Pre-chorus | Tension: tighter framing, faster cuts, motion toward something |
| Chorus 1 | Payoff: the signature image, the performance, the widest emotional register |
| Verse 2 | Complication: same world, something has changed (time, place, relationship) |
| Chorus 2 | The signature image escalated (more people, more light, bigger move) |
| Bridge / breakdown | Break the pattern: new palette, slow motion, a single long take, silence |
| Final chorus | Convergence: every thread together; densest edit; the hero shot |
| Outro | The button — one image that resolves or reframes everything |

### Step 3: Decide the lyric relationship per section

For each section, record in `enhancement_cues` whether the picture is **literal** (shows what is sung), **counterpoint** (shows the opposite or the consequence), or **abstract** (mood/motif). Literal on every line reads as a karaoke illustration; mix them deliberately. The hook line should almost always land on a face or the signature image.

### Step 4: On-screen lyrics

If the treatment includes on-screen lyrics, mark the scope in `delivery_cues.delivery_note` (`"lyrics:hook_only"`, `"lyrics:all"`). Exact timing will come from `song_map.lyrics.lines` at the edit stage — never retype times here.

Schema-valid example (times copied from the corresponding song_map section):

```json
{
  "id": "sec-s03",
  "label": "chorus 1 — the rooftop",
  "start_seconds": 32.0,
  "end_seconds": 48.0,
  "text": "Hold on",
  "delivery_cues": {"energy": "peak", "delivery_note": "mode:performance; lyrics:hook_only"},
  "enhancement_cues": [{"type": "broll", "description": "Abstract: the singer turns toward the sunrise; the signature image returns."}],
  "source_ref": "s03"
}
```

### Step 5: Validate and checkpoint

Validate against `script.schema.json` (`additionalProperties: false` on sections — use only the listed fields). Check sections tile `[0, duration]` exactly. Checkpoint `completed` (this stage does not gate on human approval by default, but present the section-by-section beat summary so the user sees it on the board).

## Common Pitfalls

- Re-deriving section times from your own listening. The song_map is the clock.
- Writing a short film that happens to have a song under it. Every section's beat must make sense *at that energy*.
- Forgetting that choruses repeat. Plan the escalation across all of them now, not shot by shot later.
