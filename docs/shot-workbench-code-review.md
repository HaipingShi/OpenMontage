# Shot Workbench code review

Reviewer: ECC `agents/code-reviewer.md`; review against the local worktree implementation, with offline temporary fixtures. No paid providers or production projects were invoked.

## Findings and repair status

- **HIGH — video route silently discards previewed identity references.** `lib/shot_execution.py:284–315` originally sent ordinary references only for `multi_ref`, while the compiler listed them as actually transmitted on `t2v`/`i2v`. A real Seedance adapter reproduction compiled identity plus composition with `generatable=true`; t2v submitted no image input, and i2v submitted only composition. The preview, reference_map and asset provenance still claimed both images. Lease, budget and hash validation did not inspect adapter coverage. Root accepted this finding; repair must select route-reachable refs, lint dropped refs, block dropped identity, and retain metadata for the exact frame/control images submitted. Independent reviewer note: the repair includes the reviewer's own compiler module, so root must independently check that portion.
- **MEDIUM — H3 Max native contract differs from generic adapter assumptions.** Offline `H3MaxVideo.build_request` rejected string duration, lower-case resolution, multi_ref local `image_paths`, and the first_last operation name. Current unknown-price blocking prevents paid execution. Backend added native endpoint schema adapters and offline regressions.
- **MEDIUM — character hydration drops appearance canon.** `lib/shot_execution.py:_identities` originally copied raw character_design dictionaries over the normalized from_scene identity snapshots. A character with `silhouette_notes="Bright purple triangular hair"` retained this appearance in pure compilation but lost it in API preview. Backend is aligning character snapshots with from_scene.
- **MEDIUM — read-only browser compilation requires a user lease.** `backlot/ui/workbench.js:262–263` originally sent `spec: draft` unconditionally even while the Agent owned the lease. API compile allows saved-state previews without a lease but requires a user lease for drafts (`backlot/workbench.py:323–325`). The exact browser-shaped request returned 409 LOCK_HELD in an isolated TestClient reproduction; removing the draft returned 200. Root repaired the browser to omit the draft when read-only; the existing agent-lock browser test needs to exercise compile.
- **MEDIUM — automatic Veo backend remains unfrozen.** `lib/shot_execution.py:_options` originally retained the tool default `backend="auto"`. With mocked Google credentials available before preview and unavailable afterwards, both Google and Fal priced the default audio-enabled 8-second request at $3.20. The two previews had identical hashes, while a completely mocked execution of the original preview selected Fal. Quote/hash/lease guards could not detect this provider switch. Backend owns the hydration repair to resolve auto to a concrete backend before hashing; final regression verification belongs to root.

## Completion notes

The HIGH reference issue is repaired in the pure compiler and independently checked by root. The added real-tool adapter tests produced RED `9 failed, 75 passed` and GREEN `84 passed`; logs are retained in `docs/evidence/shot-workbench/logs/`. Backend added native H3 contract adapters, normalized character snapshots and fixed automatic Veo backend hydration; root repaired read-only browser compilation. The Veo regression verifies equal prices produce different hashes after a credential change, while the original inputs keep their fixed Google backend. Root completed the combined regression after all repairs: `1547 passed, 7 skipped`, with 89% aggregate core line/branch coverage. The real Chrome rerun passed all six tests, including read-only compile. Independent security review is APPROVE with all two HIGH and two MEDIUM findings resolved; see `shot-workbench-security-review.md`.

No additional confirmed finding was raised for cooperative release during a render: execution retains its frozen spec and provenance, project transactions protect shared persistence, and explicit steal rejects running/unknown requests. The review found no demonstrable duplicate paid submission through that handoff. Revision/dirty browser state preserves drafts and rejects stale writes. Pipeline/schema additions preserve existing generated-shot and candidate contracts.

| Severity | Found | Status at review handoff |
| --- | ---: | --- |
| CRITICAL | 0 | None identified in either review |
| HIGH | 1 | Repaired; compiler repair independently checked by root |
| MEDIUM | 4 | All repaired and verified by final combined regressions |
| LOW | 0 | None reported |

Verdict: APPROVE for the repaired findings. Final feature-wide verification and independent security review are complete. No commit was created; Gate 2 remains separate.
