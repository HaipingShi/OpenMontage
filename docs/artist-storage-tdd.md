# Artist storage and isolated test assets

Artist APIs retain their explicit `root` parameter. The default now imports
`ARTISTS_DIR` from `lib.paths`, selecting the user's asset catalog rather than
a repository checkout. CLI scope flags precede the command:

```text
python -m lib.artist_profile --root /absolute/catalog list
python -m lib.artist_profile --project project-id prompt private-alias "quiet room"
```

The flags are mutually exclusive; neither scope falls back to another catalog.
Project reads use `load_project_artist` to verify imported snapshot hashes.
Project voice mutations reject imported bindings and explain that a private fork
is required. Private profiles and explicit private forks can be edited normally.

`tests/fixtures/artist_factory.py` derives valid alpha/beta/solo profiles from
the distributed blank template and writes tiny local PNGs. Profile, voice,
export, shot-spec and acceptance tests no longer consume repository artist
profiles, images or ledgers. Historical Fati scene descriptions and shot IDs
remain acceptance regressions, with beta/alpha synthetic identities explicitly
installed in temporary project assets. These are regression fixtures, not canon.

| Slice | Evidence |
| --- | --- |
| Existing canonical, voice, duet, ledger and schema behaviors converted to synthetic data | 41 passed, `/tmp/openmontage-assets-fixture-baseline.log` |
| User catalog root and CLI scope selection | RED 3 failed / 21 passed, `/tmp/openmontage-artist-storage-red.log`; GREEN 45 passed across profile and voice, `/tmp/openmontage-artist-storage-green.log` |
| Project immutable import / private fork CLI semantics and hash verification | RED 1 failed / 25 passed, `/tmp/openmontage-artist-binding-red.log`; GREEN 48 passed across profile, voice and export, `/tmp/openmontage-artist-binding-green.log` |
| Project-local identity loading for historical scene regressions | RED 3 failed / 32 passed, `/tmp/openmontage-project-artists-red.log`; GREEN after backend's local loader integration, covered by the combined 83-test run |
| Export only the public template and exclude private assets from legacy zip copies | Contract passed after root's export filter landed; `/tmp/openmontage-artist-export-green.log`; no fabricated RED run |

Tests use temporary roots and inherit the session socket guard and user-data
environment isolation. No real artist assets or ledgers were changed, and no
commit was created by this implementation slice. Final owned-scope validation:
`83 passed in 10.06s`, `/tmp/openmontage-artists-regression-green.log`, using
`test_artist_profile.py`, `test_artist_voice_safety.py`, `test_artist_export.py`,
`test_shot_spec.py`, and `test_shot_workbench_acceptance.py`. Root handles the
broader feature-wide regression after all parallel slices are complete.

Independent security review found that an explicit `--root` or direct voice API
could bypass the original CLI-only imported-snapshot guard. The common voice
write APIs now resolve their catalog root and, for `<project>/assets/artists`,
hold the project-assets mutex while verifying and rejecting imported bindings.
Audio import rejects before opening/copying source media or creating voice files.
Catalog and private-fork edits, plus imported usage/release log append, remain
supported. Formal RED: 4 failed / 1 passed,
`/tmp/openmontage-artist-immutable-red.log`; GREEN: 53 passed across profile,
voice safety and export, `/tmp/openmontage-artist-immutable-green.log`.
The root agent and independent security reviewer review this owned-module fix;
it is excluded from this author's independent functional review.
