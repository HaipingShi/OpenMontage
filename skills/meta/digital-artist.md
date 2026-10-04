# Digital Artist — Persistent Artist Profiles

## When to Use

The user wants an AI artist/virtual idol/虚拟歌手 that appears across many releases, or names an existing one in `artists/`. A digital artist is a long-term asset: its value is that every song, MV, cover, and caption is recognizably the same person. This skill creates the profile once; production pipelines (`music-video` and others) then read it instead of inventing a new performer per project.

Run `python -m lib.artist_profile list` first. If the artist already exists, do not re-create anything — go straight to the production pipeline with `artist_id` set.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/artist_profile.schema.json` | Validates `artists/<id>/artist.yaml` |
| Library | `lib/artist_profile.py` | `load_artist`, `identity_prompt`, `music_style`, `cast_prompt`, `duet_style`, `check_artist`, `record_ledger`, `record_release` |
| Template | `artists/_template/artist.yaml` | Starting point |
| Tools | `image_selector` (reference-image capable provider), `suno_music` | Identity bible and voice audition |

## What Makes It an Asset

| Asset | Where | Why it compounds |
|-------|-------|------------------|
| Name + logo | `names`, `rights.trademark` | The only part that is reliably protectable by trademark |
| Canon | `persona.canon`, `persona.never` | Fans notice contradictions; canon makes every release add to one story |
| Face | `identity.prompt_core`, `locked_traits`, canonical `bible/` images | Recognition across releases |
| Voice | `voice.style_signature`, canonical tracks | Recognition without the picture |
| Provenance | `ledger.jsonl` | Proof of human creative input and tool licensing for platforms, distributors, and disputes |
| Catalog | `releases.jsonl` | What has shipped, under which identity version |

## Process

### Step 1: Concept (gate)

Collect or propose: stage name (Chinese + Latin), one-liner, genre lane, 3-5 canon facts, lyric rules, and the `never` list. Push for specifics that generate songs (a job, a place, an object) over adjectives. Copy `artists/_template/` to `artists/<id>/`, fill the persona, and present it. **End your turn** — the name and canon are the hardest things to change later.

### Step 2: Identity lock

1. Write `prompt_core` with every distinguishing trait and **its side** (left/right). Always include "original … who does not resemble any real person". Never reference a real person.
2. Generate the canonical **front portrait** first (plain background, even light). Then a **turnaround** (front / three-quarter / profile / back in one image — generating views together keeps them consistent).
3. Inspect the results. The image is the source of truth: if the generator put the mole on the other side, update `prompt_core` and `locked_traits` to match the image, not the reverse.
4. Mark only images that pass identity QC as `canonical: true`. Grids with drift (expression sheets often drift) stay `canonical: false` with a `qc_note` — they must never be passed as identity references.
5. Log every image with `record_ledger` (asset, tool, model, and what the human decided).

### Step 3: Voice binding and local evidence

**When the user already selected a voice in Suno, preserve it. Do not re-audition or replace it.** Ask for which artist owns each Persona/Voice URL, and the source song link if available. Record a user-confirmed binding with `bind-suno` (see `docs/DIGITAL_ARTIST_VOICE.md`). A website Voice ID is not a persona credential for the third-party API.

- `pending_audition`: no selected voice evidence yet.
- `provider_bound`: existing website Persona/Voice recorded and confirmed; local audio may still be absent.
- `locked`: user-approved audio exported and saved locally for A/B comparison. This does not imply any generator can ingest it.

For an existing binding, use Suno web's saved selection. Download the source song or vocal stem from its song menu, not from the Voice profile URL. Import via `python -m lib.artist_profile import-voice <id> <absolute-audio-path> --approve`. This copies verified audio into `artists/<id>/voice/`, records duration, SHA-256, approval and provenance, then locks the local voice evidence. The user must have selected that recording; an agent must not approve an unheard take. Local reference files are permanent assets: back them up separately; they are excluded from patches by the exporter.

Only if there is no existing selected voice: write `style_signature`, audition using the chosen approved tool path, announce cost, then have the user choose a take. Save the approved recording through the same importer. For a licensed human/clone, record `license_holder`.

### Step 4: Readiness by actual route

`python -m lib.artist_profile check <id>` is strict local-reference readiness, so an unimported voice never reports ready. The report separately shows external binding, local evidence and generation routes.

- `--voice-route suno_web`: needs a confirmed saved binding; generation still requires manual selection in the user's Suno account. Local audio is recommended for independent QC but is not required to locate the selection.
- `--voice-route local_reference`: needs a locked, existing local canonical recording.
- `--voice-route suno_music`: reports unsupported voice reference; this tool has no website voice or audio reference adapter.
- `--voice-route visual_only`: checks identity assets for an MV using an already approved finished master; it does not certify singing readiness.

Present these limits with the profile. Missing local audio does not erase the user's existing Suno choice. Do not infer voice identity from style text, claim a website ID works in another service, or substitute another voice when the approved route is blocked.

## Using an Artist in Production

- Set `brief.metadata.artist_id` at the first stage. Read `persona.never` and refuse briefs that cross it.
- Lyrics follow `persona.lyric_rules` and must not contradict `persona.canon`.
- Song generation: `style = music_style(profile, "<song-specific additions>")`. A/B every new take against the canonical track for register, breathiness, vibrato, and accent; reject drifted takes.
- Visuals: `identity_prompt(profile, shot, look)` returns the prompt, negative prompt, canonical identity reference images, separate `supporting_references`, and the `locked_traits` checklist. Supporting wardrobe/proportion images must not be used to match faces. Every cast keyframe uses a reference-image capable provider; check each keyframe against `locked_traits`.
- New wardrobe for one release is a new `looks` entry with its own canonical images — added to the profile, not improvised in the project.
- At publish: use `rights.disclosure_label` in every description, then `record_release` and `record_ledger` for new assets.

## Artists Who Release Together

Each artist keeps a complete solo profile; a pairing is a `collaborators` entry on both sides (`artist_id`, `relation`, `duet_style`). Write the relation into `persona.canon` too, so lyrics cannot contradict it.

- Song: `duet_style(profiles, extra)` = shared `duet_style` + each artist's `voice.duet_descriptor` + additions, capped at 200 chars. Tag lyric sections by singer.
- Frames with several artists: `cast_prompt(profiles, shot, looks)`. A group key visual is never canonical for either artist — faces drift when two people share a generation.
- `check_artist` fails if a collaborator has no profile or the artist has no `duet_descriptor`.
- CLI: `python -m lib.artist_profile cast amor,fati "<shot>"` and `python -m lib.artist_profile duet-style amor,fati "<additions>"`.

## Evolution

Eras are allowed; drift is not. A new hairstyle or era is a deliberate decision: add a look, regenerate canonical images, bump `version`, and log it. Never edit `locked_traits` mid-project to excuse a bad generation.

## Rights and Disclosure

- Purely AI-generated output may get weak or no copyright protection depending on jurisdiction; the ledger's `human_input` records the creative decisions that strengthen a claim. The name and logo are best protected by trademark (music: Nice classes 9 and 41) — search before the first release.
- Label AI content wherever it is published. China's 《人工智能生成合成内容标识办法》 (in force since 2025-09-01) requires explicit labels; YouTube, TikTok/抖音, Bilibili, and Instagram have their own AI-content labels.
- Re-check each provider's commercial-use terms before release and update `rights.provider_terms[].checked_on`.

## Common Pitfalls

- Writing a new performer description inside a project instead of using `identity_prompt` — the artist forks.
- Passing non-canonical images (expression grids, key visuals) as identity references.
- Text-to-video for artist shots — identity drifts every shot.
- "Sounds like <real singer>" in a style prompt — legal risk and it breaks the artist's own identity.
- Locking the voice from a single take without the user listening.
