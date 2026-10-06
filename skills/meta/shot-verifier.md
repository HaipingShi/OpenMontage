# Shot verifier — evidence and bounded candidate review

Used by music-video assets and `shot_verifier`. Read the selected review
provider's agent_skills before any external call. Model/provider selection uses
registry capability `multimodal_review` and `filter_explicit_route`; explicit
preferences are binding. A provider error never triggers a substitute.

## Three layers, in order

1. Free deterministic evidence: actual candidate exists; a video has enough
   duration for the shot and both handles (default 0.5 seconds per side); fps
   matches the approved output fps. Check each artist's `cast_prompt.reference_map`
   against the actual transmitted `generation_log.reference_map` AND
   `generation_log.submitted_reference_images`, not the wording of a prompt.
   Capture these from the real submitted request, not a planned request. Empty
   references and wardrobe/proportion guides are not identity evidence.
   Person counts must come from a free local detector or an explicit human
   count, recorded as `{source_sha256, producer, counts: [count_per_sample]}`.
   The count evidence is bound to the actual file hash. No detector or count
   evidence means unverified; never invent a count from the shot description.
2. Physical plausibility: inspect all sampled frames AND motion in the supplied
   clip for hands, limbs, impossible motion/kinematics, perspective, and lighting.
   Return JSON `{"pass": true|false, "reasons": ["specific observations"]}`.
   Any failure eliminates the candidate immediately; do not score it.
3. Score only candidates passing layers 1 and 2. Return JSON:
   `{"script_lyrics": 1..5, "identity": {"artist-id": 1..5}, "locked_traits": 1..5,
   "singing": 1..5, "reasons": [...]}`. Omit singing on non-lip-sync shots.
   Evaluate EACH named artist against their OWN attached official references;
   do not average identities. The identity dimension is the minimum artist
   score. Check wardrobe and locked_traits separately. Singing requires sustained
   vowel/jaw/breath articulation, rather than generic talking mouth movements.

Rubric: 1 = strongly contradicts evidence; 2 = major mismatches; 3 = acceptable
with visible issues; 4 = convincing with minor issues; 5 = exceptionally precise.
Missing artist scores, invalid scores, or malformed responses are unreviewed.
Sample images support visual review, but alone do not prove motion plausibility.
Do not assert that sampled frames guarantee physical correctness.

## Budget and selection

Keyframe candidates: at most 3. Clip candidates: default 2, at most 3. Before
candidate generation, use `candidate_budget` and reserve ALL generation and
review costs with CostTracker. Announce tool/provider/model, reason, sample vs
batch, candidate count and maximum cost before each paid call. Reserve before
execution and reconcile every attempt, even rejected/failed ones. Warn at 75%
and stop at 90%; no infinite retry loop.

`shot_verifier` can make two paid review calls. `paid_call_approved` plus
`approved_cost_usd` document the pre-announced reservation; they do not replace
CostTracker. A model error means manual review, not provider switching. Missing
optional reviewer returns UNAVAILABLE and leaves candidates unreviewed. Continue
collecting evidence and present the manual review at the assets gate; never use
unreviewed candidates for an automatic continuation chain.

Use `select_candidate(candidates)`: highest composite score among eligible
passes wins, with deterministic first-candidate tie-break. If all fail, returns
`action: revise_shot` and no selection. Revise the shot under existing director
revision limits, obtain approval for creative/provider changes, and reconcile
budget. Never silently downgrade to a still, alter the song, or loop generation.

## Provider contract

An optional BaseTool with capability `multimodal_review` consumes `operation`
(physical / score / mv_judge), `prompt`, shot evidence, `frames`, official
`reference_map`, `locked_traits`, and media paths. It returns ToolResult with
JSON `data.review`, actual `model`, `cost_usd`, and success. Declare `supports`
with `video_input` for motion assessment and `audio_input` for full-MV review.
Transport/upload belongs in the selected provider; registering a provider never
silently uploads files. Providers must declare get_status and cost quotes.

### OpenRouter backend

Optional `openrouter_review` implements this transport with the user-selected
free Nemotron Omni model, NVIDIA pinned and fallback disabled. Setup and media
limits: `docs/openrouter-review.md`. Missing OPENROUTER_API_KEY is UNAVAILABLE;
configured status is an offline credential check, not proof of live quality.
Read the adapter's get_info and announce the uploaded evidence before use.

Explicit `model: qwen/qwen3.8-omni-flash` is also supported. This paid route pins
Alibaba, requests JSON-object output and requires announced CostTracker reserve
and a paid_call_approved/approved_cost_usd envelope for each call. Nemotron stays
the default. Never switch from the free model to Qwen as an error fallback.
