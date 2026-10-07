# Asset decoupling security review

Independent defensive ECC `agents/security-reviewer.md` review of the asset-decoupling slice in `codex/shot-workbench`, against baseline `89420e3bf2c7a65e5b970bb2122a1cc70ed895f1`. The prior Shot Workbench review remains separate. This review uses synthetic temporary projects, mock generators and the existing socket guard. No user projects, credentials, production migration, paid calls or commits were used.

## Confirmed findings

### HIGH — fixed-version verification and consumed bytes were separate captures

Before repair, `import_asset` verified library files through `_manifest` and then captured the source a second time without comparing those bytes with the manifest. A concurrent replacement could therefore be installed under the previously verified version/hash. `fork_asset` and artist loading had the same verification/read split.

Independent evidence: `/tmp/test_asset_security_regression.py::test_import_rejects_library_change_after_manifest_verification` publishes a synthetic v1 artist, changes the packaged wardrobe immediately after successful manifest verification, and imports it. Before repair, the new wardrobe was loaded while the binding still claimed the original v1 digest. The defensive expected `ASSET_CHANGED` assertion failed. After repair it passes, and no new alias is installed. The checked-in capture-boundary regression covers import, load and fork.

Repair verified: `lib/project_assets.py:323–328`, `:339–343`, `:361–365` compare the actual captured file list against the expected immutable manifest/binding before consumption. Artist loading parses the same captured YAML bytes rather than opening the profile again. Publication/import and project operations use filesystem mutexes. **Resolved.**

### HIGH — generic imported references could evade fixed-version binding

Generic reference paths initially lacked the source-binding checks used for artist subjects. Replacing an imported environment image and supplying its new content hash could bypass the immutable version contract. The implementation owner added a regression and binding hydration. Independent revalidation found a remaining path alias: `bindings_for_paths` at `lib/project_assets.py:405–414` matched lexical input paths, whereas reference readers resolved internal symlinks.

Independent evidence: `/tmp/test_asset_security_regression.py::test_imported_generic_reference_cannot_bypass_version_checks_via_an_internal_alias` publishes/imports a red environment image, creates a project-internal image symlink to that snapshot, then changes the imported image to blue. Referencing the alias with the new SHA returns HTTP 200. Compilation reports `asset_bindings=[]`; the confirmed mock generation succeeds once and records no asset bindings. The expected rejection assertion fails. All data and generator behavior are temporary/offline.

Repair independently verified: `bindings_for_paths` (`lib/project_assets.py:405–415`) now first rejects any symlink via `_safe`, resolves cited paths and snapshot roots, and matches canonical ownership. Both input and parent references are checked before authorization/reservation. The descriptors enter the hydrated semantic spec/hash, confirmation, asset and cost provenance. The original temporary regression now passes without a mock call; the image/parent alias and internal project-alias regressions also pass. **Resolved.**

### MEDIUM — explicit catalog root bypassed imported voice-write protection

Before repair, `lib/artist_profile.py` checked immutable imports only in the `--project` CLI branch. `--root <project>/assets/artists bind-suno lead …` and the underlying direct functions could write an imported snapshot. The independent temporary regression observed changed `artist.yaml`, a new ledger entry and a subsequent `ASSET_CHANGED` project-read failure.

Repair verified: `_artist_edit_scope` and `_guard_artist_edit` (`lib/artist_profile.py:387–410`) enforce the binding check in both voice-write functions while holding the project-assets mutex. The temporary CLI regression now passes. The four checked-in direct/CLI bind/audio variants fail before copying or creating a voice directory; a private fork remains editable and imported usage logs remain appendable. Profile/voice/export suites: **53 passed**. **Resolved.**

### MEDIUM — missing project-relative media input passed migration inventory

Before repair, `_normalise` checked ordinary relative input paths only for artist packages. A scene's `reference_images=["assets/images/missing.png"]` passed project inventory with no warnings, contradicting the explicit missing-input failure contract.

Independent evidence: `/tmp/test_asset_security_regression.py::test_migration_rejects_a_missing_project_reference` modifies only that field in a temporary legacy fixture. Before repair inventory returned successfully with `warnings=[]`; afterwards it raises `DANGLING_REFERENCE`. The repair (`lib/asset_migration.py:252–270`) checks project-relative media inputs while preserving planned output paths and historical text/source warnings. Explicit embedded artist-owner, same-named files under different artists, unknown nested owner, and historical archive/prefix regressions also pass. **Resolved.**

### MEDIUM — malformed binding receipts were not rejected before use

The implementation owner added source-proof regressions. Independently running them before repair confirmed that replacing `sha256` with `invalid`, changing the claimed version to v2, or providing an absolute source-file record did not cause artist loading to reject the binding. Such data could be copied into generation provenance and fail later output validation.

Repair verified: `_valid_files` and `_bindings` (`lib/project_assets.py:105–118`, `:232–260`) validate relative paths, bounded integer sizes, SHA-256 shape, canonical snapshot placement and source identifiers, then recompute the imported manifest digest from the receipt. All three regressions now pass before provider consumption. **Resolved.**

### MEDIUM — migration detected another library's alias only after copying

The companion code reviewer identified that migration preflight compared the existing alias's asset/version/kind but omitted its library identity. Two libraries can legitimately publish identical files under the same asset/version. Before repair, a destination alias imported from the other library passed preflight; migration copied files and then the final import rejected the existing alias. The final alias/source remained protected, but the promised conflict-before-copy boundary failed.

Repair independently verified: `_preflight` (`lib/asset_migration.py:419–425`) reads the migration destination's library identity and requires the existing imported binding to match it before copying. `test_other_library_alias_conflicts_are_found_before_any_copy[True|False]` covers both an initialized destination library and one not yet initialized; both now return `BINDING_CONFLICT` with `copied_files=0` and leave the second project absent. The latest migration suite is **46 passed**. **Resolved.**

## Boundary checks and limitations

- Runtime data defaults to the user data directory; no same-name repository/global artist fallback is used for project rendering. Tests override data roots before collection. Explicit library identity is required by the HTTP import route.
- Immutable aliases cannot be replaced with another version/library or a private alias. Private forks change only their project snapshot. Two consecutive mocked generations preserve the imported snapshot and append project-local ledgers; another project and library history remain unchanged.
- Migration uses no-follow directory/file descriptors, compares inventory/source hashes, verifies copied temporary bytes, installs without overwriting existing files, and rechecks the source inventory. Source trees are retained. Conflicting destinations, changed sources, symlinks, traversal and overlapping roots are tested. Failed overlapping-root migration does not write a report into the source.
- Historical creative text, code and external source provenance are preserved with manual-review warnings. Structured files directly under a project's `history/` tree retain their original bytes, add `MANUAL_REVIEW_ARCHIVED_INPUT` warnings, and do not create bindings against today's library versions. `history2/` remains subject to active-input checks. Recognized provider graphs and node schemas also retain their bytes and receive provider-review warnings; they are not interpreted as OpenMontage media input fields. These warnings are not proof that historical/provider-local inputs remain usable. Actual structured media inputs require scoped resolution; embedded profile paths use an explicit artist owner and never guess from filenames.
- Source-update packaging restricts artist assets to the blank template and filters personal project/library/music directories at all archive prefixes. The isolated Git index supplies the patch and matching file bytes; personal fixtures and archive safety regressions pass.
- Confirmation retains shot/revision, semantic hash, tool, quote, references and asset descriptors. Existing byte snapshots, unknown-charge handling and request deduplication remain part of the prior review; no provider was contacted here.
- No external dependency/advisory scan or migration of real user data was performed. This slice adds no external package dependency.

## Validation state

- Final combined project-assets, migration, data paths, distribution, artist profile/export/voice safety, Shot Workbench API and shot-spec suites: **239 passed** in 33.32 seconds.
- The latest migration changes were then independently rerun: **46 passed**, including both cross-library preflight variants and the archive/profile/provider context cases.
- Independent temporary suite after repairs: **4 passed**.
- The added dropped-reference regression now passes: media bindings are selected from the actual compiler payload; textual artist identity bindings remain independent. Unused media is omitted from generated-asset, candidate and cost provenance.

Final checked-in suite command:

```sh
OPENMONTAGE_ALLOW_NETWORK=0 /Users/geesh/projects/OpenMontage/.venv/bin/python -m pytest tests/contracts/test_project_assets.py tests/contracts/test_asset_migration.py tests/contracts/test_data_paths.py tests/contracts/test_asset_distribution.py tests/contracts/test_artist_profile.py tests/contracts/test_artist_export.py tests/contracts/test_artist_voice_safety.py tests/contracts/test_shot_workbench_api.py tests/contracts/test_shot_spec.py -q --disable-warnings
```

| Severity | Confirmed during review | Unresolved |
| --- | ---: | ---: |
| CRITICAL | 0 | 0 |
| HIGH | 2 | 0 |
| MEDIUM | 4 | 0 |
| LOW | 0 | 0 |

Verdict: **APPROVE** for the reviewed local asset-decoupling boundary. All confirmed HIGH/MEDIUM findings are repaired and independently verified; no requested local check remains unfinished. The real-data/external-scan limitations above remain explicit. Gate 2/commit approval is separate; this review created no commit.
