# AutoMV-inspired music-video upgrade

Development baseline: **2bface7**, explicitly approved by the user because the
requested 53729ce could not be resolved. The upstream repository is
calesthio/OpenMontage; the user's fork is HaipingShi/OpenMontage.

This extends the existing agent-driven pipeline. Directors make creative
choices, obtain checkpoint approval and manage CostTracker. Python adds tools
and deterministic evidence helpers, not a second production orchestrator.

## Changes

- **WP1:** optional `stem_separator` runs htdemucs, caches by master content and
  model, and converts both stems to the master's sample rate/channels. Directors
  transcribe vocals and retain the master as the beat/timeline clock. Lip-sync
  slicing prefers vocals, explicitly records master fallback, and includes
  generation handles with derived-slice padding at song edges.
- **WP2:** Suno record lyrics precede user lyrics and transcription. Alignment
  token-match ratios, singer and original section tags are retained per line.
  Untimed or sub-threshold lines appear in `review_flags` at song_analysis's
  existing human gate (default threshold 0.6).
- **WP3:** `lib.timeline` builds 3–15-second narrative segments, prioritizes
  section/lyric boundaries and validates exact frame tiling. A section shorter
  than the minimum is retained with an explicit exception for the director.
  Impossible custom limits raise an actionable error instead of breaking them.
- **WP4:** generated shots use story_i2v, performance_lipsync, cast_multi_ref or
  continuation. Provider duration limits include both handles. Continuation
  verifies the predecessor's content hash, decodes the entire clip and extracts
  frame N-1 without approximate seek; provenance records the source and index.
  Narrative segments, generation clip boundaries and beat-cut units remain
  separate; edit cuts still follow song_map.
- **WP5:** bounded candidates retain all attempt costs and verification evidence.
  Deterministic media/reference/count checks precede physical review, then
  scoring; identity uses the lowest score across individually reviewed artists.
  All rejected candidates return revise_shot; unavailable review returns manual
  review, with no implicit generation loop or provider replacement.
- **WP6:** `mv_judge` accepts full video and locked master and reports the 12
  criteria from [AutoMV §4.1](https://arxiv.org/html/2512.12196v1#S4.SS1).
  Category weights are technical 20%, post-production 20%, content 30%, art 30%.
  AI novelty is separately reported and excluded by default (the other two art
  criteria share the category's 30%). Results are advisory and never change
  final_review.status or publish eligibility.

All schema additions are optional. Old artifacts still validate. New optional
inference tools are in manifest `optional_tools` and `tools_available`, rather
than required_tools: declaring them mandatory would contradict the required
UNAVAILABLE degradation. Free frame_sampler is required for assets; existing required tools remain. Deterministic timeline
and evidence checks are mandatory in the directors and review_focus.

## Optional reviewer integration and limits

No review vendor is hardwired. Register a BaseTool with capability
`multimodal_review` and the transport contract in `skills/meta/shot-verifier.md`.
Explicit preferred_tool/provider/hosting_provider/model routing is binding;
execution errors never select another provider. A video reviewer must declare
video_input; full MV review also requires audio_input. Actual model and charged
usage are retained, including malformed responses and failed calls.

An optional `openrouter_review` backend is registered for the explicitly chosen
free NVIDIA Nemotron Omni model. It reports UNAVAILABLE without
OPENROUTER_API_KEY; manual review at the assets checkpoint remains available,
and mv_judge absence never stops publishing. See
[OpenRouter setup](openrouter-review.md).
No new credential/configuration dependency is imposed. Demucs is also optional;
real separation may require provisioned model weights. There is no SongFormer
requirement, model training, lyric scraping or replacement beat analyzer.

Person-count evidence requires a free detector or explicit human inspection,
recorded with the candidate hash. This upgrade does not ship a new people
recognizer. Reference submission checks inspect captured request logs; they
cannot independently attest what an external provider does internally. Missing
evidence is manual-review pending, never a fabricated pass. Unreviewed clips
cannot seed automatic continuation; human evidence must be recorded first.

## Offline verification

```sh
pytest -q tests/contracts
python scripts/automv_dry_run.py --output projects/automv-upgrade-dry-run
```

The dry-run is an acceptance fixture, not a production workflow. Demucs, beat
analysis, video generation, people counts and model reviews are explicitly
mocked. Local FFmpeg actually creates 30-second audio, same-rate stems, vocal
slices, candidate videos, exact continuation frames, trims and the final mux.
The four artifacts are schema validated; timeline and final video have 720
frames at 24 fps; the master hash remains unchanged. Its output report records
zero external calls/cost. Mock scores make no artistic/identity-quality claim.

Baseline contracts: **1296 passed, 8 skipped**, no failures. Baseline targeted
music-video/artist contracts: **61 passed, 1 skipped**. Final contracts: **1328 passed, 8 skipped**; targeted music-video/artist:
**61 passed, 1 skipped**; added upgrade tests: **26 passed**. There are no
pre-existing or newly introduced failures. Local logs are under `.backlot/automv/`.

Export after committing with `git diff --binary 2bface7 HEAD > automv-upgrade.patch`.
The approved baseline replaces the unavailable original SHA in this command.
