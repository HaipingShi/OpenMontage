# Asset decoupling functional code review

Independent review follows ECC `agents/code-reviewer.md`, reading staged and
unstaged changes, complete modules, callers and contract tests. Scope:
`lib/project_assets.py`, `lib/asset_migration.py`, scoped identity/reference
hydration in `shot_spec` and `shot_execution`, Backlot asset APIs, `lib.paths`,
music library defaults, and software export boundaries. The author's owned
`artist_profile.py` and artist tests are excluded from this independent review;
the root agent and security reviewer assess that implementation separately.

All reproductions below use synthetic profiles, tiny images, temporary projects
and an offline mock generator. No production assets, paid tools, real migration
or Git commits were touched by this review.

## Confirmed findings

### MEDIUM — dropped media still claimed a library source in generation provenance

At the reviewed version, `lib/shot_execution.py:420–423` collected bindings from
every draft reference before the compiler filtered references by route and tool
limits. Its `:444` returned that complete list. `_record_asset` and the cost
reservation then copied it into asset/candidate and cost provenance.

Concrete trigger: explicitly import an environment PNG as `room/v1`, attach it
to a `t2v` shot, and compile. The compiler correctly reports
`ROUTE_REFERENCE_DROPPED`, produces an empty actual reference payload and allows
text-only generation; `compiled.asset_bindings` still claims the shared room.
`validate_references` verifies all draft content but does not determine which
references were submitted, so its integrity check cannot catch this mismatch.

Independent failing proof:
`/tmp/test_asset_functional_review.py::test_dropped_reference_does_not_claim_source_asset_binding`;
RED log `/tmp/openmontage-asset-functional-review-red.log` (1 failed).
Required repair: retain integrity validation of all draft references, derive
media-source bindings from the actual compiled reference payload, and retain
artist bindings whose identity text participates in the prompt. Hash and
confirmation must contain the resulting source descriptors.

Repair independently verified: `lib/shot_execution.py:419` still validates all
draft references; `:423–429` compiles first, derives media bindings from the
actual payload and recompiles with those plus artist identity bindings before
quoting/confirmation. The original temporary proof now passes,
`/tmp/openmontage-asset-functional-review-green.log` (1 passed). The owner also
added a formal RED/GREEN contract covering the generated asset, candidate and
cost entry, `/tmp/project-assets-unused-binding-green.log`. **Resolved.**

### MEDIUM — another library's same-name version conflict was detected after copying

At the reviewed version, `lib/asset_migration.py:403–406` checked an existing
project alias using only `asset_id`, `version`, `kind` and imported mode. It
omitted the source library identity.

Concrete trigger: source migration plans `solo/<content version>`; the target
project already imported that alias and version from library L2, while the
migration's destination library is L1. Both contain identical files. Preflight
accepts the alias, copies 12 source files, then `import_asset` correctly rejects
L1 as an alias replacement. Source originals and conflicting snapshot remain
intact and the report is marked failed, but the documented promise to find all
destination conflicts before copying is violated.

Independent failing proof:
`/tmp/test_asset_functional_review.py::test_migration_conflict_from_other_library_is_found_before_any_copy`;
RED log `/tmp/openmontage-asset-migration-preflight-red.log` (1 failed,
`copied_files=12`). Required repair: compare the existing alias with the expected
migration library identity before copying. A target library without
an established identity cannot already own an imported receipt from that library.

Repair independently verified: `_preflight` now validates the destination's
`library/library.json` identity before any `_copy`. An existing alias with a
different identity, or a missing destination library identity, raises
`BINDING_CONFLICT`. The original independent proof and both formal cases
`test_other_library_alias_conflicts_are_found_before_any_copy[True/False]` pass,
with `copied_files=0`. Combined focused recheck also includes the original
unused-reference proof: **4 passed**, log
`/tmp/openmontage-asset-functional-review-recheck.log`. **Resolved.**

## Other boundaries checked

- Project artist reads use only project-local aliases. Missing same-name artists
  fail rather than reading the user catalog or repository. Imported snapshots
  survive relocation and deletion of their source library.
- Fixed published versions reject changed contents; alias reuse requires the
  same explicit library/version receipt. Canonical/candidate metadata remains
  in the snapshot. Private forks do not write to the source or another project.
- Actual captured bytes are compared to immutable receipts at import/load/fork
  consumption, and artist loading parses those captured YAML bytes. Receipt
  parsing validates relative file paths, digest shapes and source identity.
- Generation output, asset/candidate provenance, cost reservations and artist
  usage ledgers are current-project data. Historical source ledgers are excluded
  from published version hashing; two mocked generations preserve snapshot hash
  and another project's independent ledger.
- HTTP import requires explicit library identity; import/fork share local-host,
  origin and bounded-JSON write checks. No paid tool runs through those endpoints.
- Application paths default to separate user data; reading configuration does
  not create directories or migrate assets. Export packaging includes only the
  blank artist template and rejects personal directories at any archive prefix.
- Migration is copy-only, revalidates source/plan hashes, rejects unsafe paths
  and conflicting bytes, verifies installed files, and records partial failures.
  Unstructured historical references require manual review, as documented.

## Evidence and coordination

The first focused project-assets/migration/path/distribution run yielded
83 passed and 1 failed while the migration owner was adding its history-context
RED test (`test_archived_structured_inputs_keep_their_bytes_without_new_bindings`),
log `/tmp/openmontage-asset-functional-review-regression.log`. This in-progress
failure is not a separate new finding. The subsequent focused regression and
original-proof rechecks cover the coordinated fixes.

After history and payload provenance repairs, the focused project-assets,
migration, data-path, distribution and Shot Workbench API suites passed:
**155 passed in 22.59s**,
`/tmp/openmontage-asset-functional-review-final-regression.log`. The final
cross-library preflight change and both formal cases were added after that run
and independently passed in the four-case recheck above. The migration owner
also reports its full 46-test migration regression passed; root performs the
final full feature regression and coverage.

Security findings and their independent resolution are tracked separately in
`docs/asset-decoupling-security-review.md`, including immutable capture races,
generic reference aliases, missing migration inputs and receipt validation.
The author's common voice-write guard repair has formal four-entry RED and
53-test GREEN evidence in `docs/artist-storage-tdd.md`; the independent security
reviewer confirmed that fix, and root reviews the owned module.

## Review Summary

| Severity | Confirmed in this functional review | Open |
| --- | ---: | ---: |
| CRITICAL | 0 | 0 |
| HIGH | 0 | 0 |
| MEDIUM | 2 | 0 |
| LOW | 0 | 0 |

Verdict: **APPROVE** — no unresolved findings from this functional review.
Both confirmed MEDIUM issues were fixed and independently revalidated. This is
not Gate 2/commit approval or an override of the separate security verdict.
