# OpenRouter multimodal review

`openrouter_review` is an optional BaseTool provider for `multimodal_review`.
It is discovered automatically and works with both shot_verifier and mv_judge.
The only enabled model is the user-selected
`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`.

## Configure

Create a key at https://openrouter.ai/settings/keys. Add the following line to
this repository's existing, gitignored `.env` (do not overwrite other keys):

```dotenv
OPENROUTER_API_KEY=your-openrouter-key
```

Alternatively set that environment variable in the process running OpenMontage.
Registry discovery loads `.env` without replacing existing environment values.
Restart the calling agent/process after changing credentials. No NVIDIA key,
new SDK, global `llm.provider` change or extra model setting is required.

Read-only local availability check (no upload, no inference):

```sh
python -c 'from tools.tool_registry import registry; registry.ensure_discovered(); print(registry.get("openrouter_review").get_info()["status"])'
```

AVAILABLE means credentials/dependencies exist, not that the remote endpoint has
been authenticated or is currently reachable.

## Use through the existing directors

Before live review, announce tool/provider/model, purpose and the media being
sent. Use the normal assets gate and compose final_review; do not treat adding
a key as approval for a bulk run. Supply the following explicit route fields
to shot_verifier or mv_judge:

```json
{
  "preferred_tool": "openrouter_review",
  "preferred_provider": "openrouter",
  "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
}
```

ShotVerifier still requires the candidate, shot, actual reference submission
log, hash-bound person-count evidence and official identity references. It sends
motion video, sampled frames, every artist's labeled references and locked_traits.
Deterministic failures skip all OpenRouter calls; physical failures skip scoring.
MvJudge sends the complete final video plus the exact locked master audio and
remains advisory. No returned score constitutes human approval.

The adapter uses https://openrouter.ai/api/v1/chat/completions with NVIDIA pinned,
`allow_fallbacks: false`, zero maximum prices, reasoning enabled and 8192 output
tokens by default. It never retries, selects a paid model or falls back to a
frame-only review. Local video/images are encoded as data URLs; WAV/MP3 audio
uses input_audio Base64. Unsupported media is rejected before any upload.
For lower-level approved calls, max_tokens, reasoning_enabled and max_media_bytes
can be specified directly on the adapter.

## Results and limits

- Default aggregate upload limit is 64 MiB of raw media; it can be explicitly
  raised up to 256 MiB. Base64 adds roughly one third to request size. Provider
  limits may be lower. Oversize input fails before upload; no automatic trimming,
  resampling or omission of the master/reference images occurs.
- The endpoint has no enforced response_format/JSON-schema support. Only one
  JSON object (optionally enclosed in a JSON code fence) is accepted. Partial,
  missing, nonfinite, invalid or per-artist-incomplete scores fail validation.
  Reasoning text is never interpreted as the review result.
- Results retain actual model, request id, usage/cost, and hash-bound submitted
  media provenance. Unexpected models and nonzero reported charges fail review;
  charged usage is preserved for budget reconciliation. Do not automatically
  retry malformed responses. HTTP 401/429/503 and timeouts remain inspectable;
  MvJudge failure never blocks publishing.
- get_status is offline. Free capacity, rate limits and provider availability
  can change. Actual modality handling and artistic/identity quality need a
  disclosed live sample; contract tests do not establish these qualities.
- No credential or Base64 payload is persisted in results or errors. The files
  themselves are transmitted to OpenRouter/NVIDIA during an approved execute.

Official references:
[model/endpoint](https://openrouter.ai/api/v1/models/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free/endpoints),
[video inputs](https://openrouter.ai/docs/guides/overview/multimodal/videos),
[audio inputs](https://openrouter.ai/docs/guides/overview/multimodal/audio),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

## Offline validation

```sh
pytest -q tests/contracts/test_openrouter_review.py tests/contracts/test_automv_upgrade.py
pytest -q tests/contracts
```

All HTTP requests in the tests are mocked. Tests inspect video/master encoding,
per-artist references, pinned free routing, HTTP/transport failure, incomplete
responses, JSON/score validation, cost retention and integration with selectors.
No real MV material or credentials are sent by these tests.

Validation for this implementation: **1348 contracts passed, 8 skipped**;
19 adapter contracts cover mocked transport. Registry discovery confirmed one
multimodal_review provider; without the key it reports UNAVAILABLE. No live
inference or authentication test was performed.
