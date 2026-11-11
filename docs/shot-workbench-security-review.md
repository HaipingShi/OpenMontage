# Shot Workbench security review

Independent ECC `agents/security-reviewer.md` review against baseline `89420e3bf2c7a65e5b970bb2122a1cc70ed895f1`, branch `codex/shot-workbench`. This is a software review, not a production run. Fixtures are temporary, generators are mocked, and the existing socket safety fixture prevents outbound provider calls. No real project, artist ledger, paid service or commit was touched.

## Confirmed findings

### HIGH — confirmed reference content can change before generator consumption

Before repair, `lib/shot_execution.py:314`, `:326–329`, `:350–365` adapted references into original project file paths. `render` validated their hashes while holding the shot/project mutexes, then released the mutexes before `tool.execute` (`:650–654`). A local collaborator replacing a source image between these steps changed the bytes the generator consumed without changing the authorization token, compiled hash or recorded provenance. Existing provider adapters consume local paths later; for example, Seedance uploads `reference_image_paths` inside `execute` (`tools/video/seedance_video.py:299–302`).

Evidence: `/tmp/test_shot_security_regression.py::test_confirmed_reference_bytes_remain_frozen_until_mock_consumes_them` reuses the existing API fixtures and `MockGenerator`. It imports the session socket guard. After confirming a red image, it models an ordinary concurrent edit to a blue image at the mock generator's consumption boundary. The request succeeds; approved and manifest SHA-256 are `29d1d4712cdef1287061c9e803fa24000ecd788c00ab0e31b0d534e544ae2c4f`, while the mock reads `623ee974051ef1f015a90bfd87bd4db8ce9b73da66a77c0be25ca75048a89acb`. The expected equality assertion fails. No actual provider was executed.

Required repair: capture and verify the bytes before budget reservation, use request-specific immutable snapshots or in-memory data for every ordinary/frame/control reference, retain original source provenance, and record the snapshot mapping. Reject an already-changed source before reservation. Recheck the actual frozen content and path boundary at consumption.

Status: **repaired and independently verified**. `lib/shot_execution.py:496–538` reads each source once, verifies those exact bytes, writes request-specific read-only snapshots, and rewrites ordinary/first/last/control input paths. Freeze and input preparation precede `approve_tool`, estimate and reserve (`:688–697`). The request stores the original-to-snapshot mapping; the asset/cost provenance retains original source paths. The original temporary regression now passes, and source modification after confirmation no longer changes generator input.

### HIGH — adapter transport uncertainty is reconciled as a confirmed zero charge

`ToolResult.cost_usd` defaults to `0.0` (`tools/base_tool.py:140`). Existing Seedance execution catches submission/polling errors and returns a failed result without setting a charge (`tools/video/seedance_video.py:378–382`). `lib/shot_execution.py:719–730` treats that default zero as a known actual cost, marks the request failed and releases its budget reservation. A request that was accepted remotely but whose polling response was lost therefore escapes the running/unknown guard; a fresh request ID can submit another payable call.

Evidence: `/tmp/test_shot_security_regression.py::test_adapter_transport_uncertainty_keeps_request_unknown_and_reservation` uses the real local Seedance adapter method with `requests.post`, `requests.get`, credentials and sleeping fully mocked. The local submission fixture reports acceptance once; polling raises a mocked timeout. The API returns `failed`, `actual_cost=0.0`, and the log has `budget_reserved_usd=0`, where the defensive assertions expect `unknown` and retention of the quote reservation. The socket safety fixture also remains active. This reproduces adapter contract behavior without any external call.

Required repair: do not interpret a default zero on a failed paid operation as proof of no charge. Retain an unknown request and its reservation until an explicit charge/receipt is available; known charged failures may still reconcile.

Status: **repaired and independently verified**. `lib/shot_execution.py:724–737` treats zero actual cost on a positively quoted action as an unknown charge, for both success and failure; the reservation remains. The original temporary real-adapter regression now returns `unknown`, retains `$1.52`, and no longer exposes a confirmed zero actual cost. The checked-in `test_default_zero_actual_cost_is_unknown_for_a_quoted_paid_call` and `test_real_seedance_adapter_polling_timeout_keeps_paid_reservation` also verify that a fresh request ID replays the unknown result without another submission. Known positive actual charges continue to reconcile, including failed calls.

### MEDIUM — checkpoint fallback follows a symlink outside the project

Before repair, `lib/shot_spec.py:_artifact` iterated `checkpoint_*.json` and called `read_json(cp)` directly (`:198–199`). The canonical artifact reads use `safe_path`, but this fallback did not check the checkpoint file's resolved path. An imported project containing an outward checkpoint symlink could therefore initialize a new shot from a JSON artifact outside its allowed project boundary.

Evidence: `/tmp/test_shot_security_regression.py::test_checkpoint_artifact_read_rejects_outside_project_symlink` uses an external temporary checkpoint fixture and a project-local symlink. With the standalone scene-plan artifact absent, `GET /api/shot/film/shot1` returns 200 and consumes that external artifact; the regression expects 403. This is a file-boundary issue and does not demonstrate arbitrary code execution or a real-secret disclosure.

Required repair: apply `safe_path` to every checkpoint/request path discovered through a glob before reading, including the request-list and request-match paths.

Status: **repaired and independently verified**. `_artifact` now resolves each glob result through `safe_path` (`lib/shot_spec.py:206–209`) and validates the checkpoint/artifact shapes. The original temporary regression now returns 403 and passes. Both request-glob consumers also use `safe_path` (`lib/shot_execution.py:490–493`, `:650–651`).

### MEDIUM — official reference provenance can be downgraded through save or duplicate upload

Independent confirmation of root's defensive regressions: `tests/contracts/test_shot_workbench_api.py::test_official_reference_provenance_cannot_be_downgraded` fails in both `save` and `upload` variants. Before repair, `_protect_canon` only checked references that retained `read_only` or `source_artist` (`backlot/workbench.py:108–113`), so removing both markers bypassed the comparison. Exact duplicate upload replacement (`:307–309`) also discarded the official source markers. Both operations returned 200 where the test expects 403.

Required repair: recognize protected source paths/identities from the existing authoritative spec, reject changes to their provenance, and prevent duplicate-upload replacement from stripping protected metadata.

Status: **repaired and independently verified**. `_protect_canon` recognizes protected original paths regardless of client markers (`backlot/workbench.py:108–113`); duplicate uploads matching a protected reference return 403 (`:305–309`). The existing save/upload regressions pass.

## Other checks and limits

- Existing token checks bind shot/revision, compiled hash, concrete tool, references and quote; pending tokens expire after ten minutes and are consumed before submission. Running/unknown requests prevent automatic paid resubmission, including under a fresh request ID. CAP checks apply before reservation. Corrupt cost logs are rejected and preserved.
- Shot changes use revision comparison plus the holder/lease capability. Filesystem mutexes coordinate threads and processes; the existing cross-process tests pass. Agent leases are omitted from the browser response.
- Mutation routes bound JSON request bodies, reject nonfinite JSON and cross-origin writes, and restrict Host. Uploads verify PNG/JPEG/WebP content, dimensions and byte limits. The reviewed new UI uses DOM text nodes rather than inserting user text as HTML, and its network paths are local.
- Vendor files locally match every SHA-256/length declaration in `backlot/ui/vendor/three-0.181.2/integrity.json`; the MIT license and declared npm archive integrity are present. Upstream archive/advisory state was not independently fetched in this local-only review. This does not claim a current dependency vulnerability scan.
- No actual credentials were read or printed. Provider transport error strings remain a review limitation: the offline checks do not prove how every external SDK formats sensitive failures.

## Validation state

- Pure compiler/spec suites: **111 passed**.
- Final API/spec suites after all repairs: **92 passed**.
- Independent temporary defensive regressions for reference freezing, checkpoint bounds and real-adapter transport uncertainty: **3 passed** after repair.

| Severity | Confirmed during review | Unresolved |
| --- | ---: | ---: |
| CRITICAL | 0 | 0 |
| HIGH | 2 | 0 |
| MEDIUM | 2 | 0 |
| LOW | 0 | 0 |

Verdict: **APPROVE** for the reviewed local feature boundary. Every confirmed finding is repaired and independently checked; no additional material issue remains from this review. The external-advisory/SDK limitations above remain explicit. Gate 2 remains separate; no commit was created.
