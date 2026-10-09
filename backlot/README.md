# Backlot — the living storyboard

A local board that shows a production happening: pipeline stages
lighting up, the script as a screenplay page, the scene plan as a filmstrip
that fills in as assets generate, decisions, spend, and activity — all
derived from what the pipeline already writes to the configured `PROJECTS_DIR/<id>/`.

Production data defaults to `~/OpenMontageData/projects`, outside the application checkout.
Set `OPENMONTAGE_DATA_DIR`, or independently `OPENMONTAGE_PROJECTS_DIR` and
`OPENMONTAGE_LIBRARY_DIR`, to absolute paths for a different workspace.
The workbench reads the current project's private or explicitly imported artist snapshots;
shared-library revisions never silently change an existing project. See
[`asset-decoupling.md`](../docs/asset-decoupling.md) for the copy-and-verify migration.

```bash
python -m backlot open <project-id>   # start server if needed + open browser
python -m backlot open                # library view (all projects)
python -m backlot serve --port 4750   # run the server in the foreground
```

## How it stays live

No agent involvement. A `watchfiles` watcher on `projects/` publishes change
notifications over SSE; the browser refetches board state. State sources:

| Board element | Disk source |
|---|---|
| identity / rail order | `project.json` + `pipeline_defs/<type>.yaml` |
| stage states, gates, versions | `checkpoint_<stage>.json` + `history/` |
| script card / modal | `artifacts/script.json` |
| filmstrip cards | `scene_plan × script × asset_manifest` join |
| generating shimmer, activity | `events.jsonl` (written by `BaseTool` instrumentation) |
| cost meter | checkpoint `cost_snapshot` |
| renders | `renders/*.mp4` (+ root-level mp4 heuristic) |

Projects without checkpoints degrade gracefully to a "what the watcher
found" view — media, snapshots, renders.

**Replay**: a completed run can be scrubbed end-to-end (▶ REPLAY RUN on the
board) — reconstructed from checkpoint history and event timestamps.

Try it without a real production:

```bash
python scripts/backlot_simulate_run.py          # live demo run (~1 min)
python -m backlot open backlot-demo-run
```

Design doc: `internal/design/LIVING_STORYBOARD.md`.

## Shot review annotations

Open `/r/<project-id>` for the dedicated human review workspace. It lists
saved video sources without autoplay or generated thumbnails, with a player,
source-relative point/range annotations, severity, issue categories and desired
changes. Accepted annotations autosave to `reviews/shot_reviews.json`; drafts
survive refresh per browser tab, and conflicting revisions require reopening.
The workspace can export notes and compare approved reference images/prompts.

**完成标注，交给 Agent** saves outstanding changes and calls
`POST /api/project/<id>/reviews/submit`. This freezes notes plus source SHA256
in `reviews/submissions/<id>.json` and updates `reviews/review_handoff.json`.
It requests later analysis of human feedback, and never approves production,
selects a candidate or resumes generation.

Click **标注** below a storyboard shot (or click an alternate take) to review
its source. Choose 通过 / 不合格 / 可裁切使用, pause the source to capture a
point or range, and record face, action, costume, lip-sync, framing, or other
issues. All times are source-relative seconds, not final-film timeline times.
Save explicitly to persist feedback in `reviews/shot_reviews.json`; production
artifacts and approval gates are unaffected. The board shows a correction list.

Shots using the same normalized media path share feedback, including explicit
`scene_plan.metadata.routing[].reuse_of` routes. Issues retain their scene ids.
Concurrent edits to the same source are rejected with a revision conflict.

API: `GET /api/project/<id>/reviews` returns sources and the derived fix list;
`PUT` at the same URL saves one source with `scene_id`, `media_path`, `revision`,
`status`, and `issues`. Each issue has `scene_id`, optional `start_seconds` and
`end_seconds`, `reasons`, and `note`.

Hand-authored assets review pages served under `/media/<id>/artifacts/` can
reuse `/ui/review-page.js` and `/ui/board.css` when they have `#sections` with
`article > h3` scene ids. This adapter uses the same editor and persisted data.

## Shot Workbench

Open a complex generated shot with:

```bash
python -m lib.shot_workbench open <project-id> <shot-id> --reason 'Adjust the two-person blocking'
```

The command initializes `shots/<shot-id>.json` from `scene_plan` when needed,
acquires the user lease and prints `/w/<project-id>/<shot-id>`. The four panels
share references, structured shot fields, a local 3D stage and a read-only
compiler preview. Save explicitly; conflicting revisions preserve your draft.
**保存并交回 Agent** saves and releases the lease, appending a field-level handoff
diff to `artifacts/decision_log.json`. An agent lease makes the UI read-only.

The browser is optional. For an isolated agent edit:

```bash
python -m lib.shot_workbench acquire <project-id> <shot-id> --holder agent --reason 'Refine movement cause'
python -m lib.shot_workbench status <project-id> <shot-id> --holder agent
python -m lib.shot_workbench edit <project-id> <shot-id> --holder agent --revision <revision> --lease-id <lease> --file <shot.json>
python -m lib.shot_workbench compile <project-id> <shot-id> --tool <approved-tool>
python -m lib.shot_workbench dry-run <project-id> <shot-id> --tool <approved-tool>
python -m lib.shot_workbench release <project-id> <shot-id> --holder agent --lease-id <lease>
```

All commands return JSON. `compile` and `dry-run` read the saved spec, verify local
reference hashes and obtain the existing tool's quote without reserving money or
calling generation. Leases expire after two hours without save/heartbeat;
`steal --reason ...` explicitly recovers an expired lease and records the reason.
Unresolved running/unknown render requests block forced handoff.

The stage uses vendored Three.js 0.181.2, five coarse human poses and basic shapes.
Exports save composition, linear camera-space depth, skeleton PNG/JSON and camera
JSON with their source spec. Projection uses the selected lens and cropped 36×24 mm
sensor. It does not model fingers. These images guide composition; only tools with
an explicit control-input mapping receive depth/pose. Artist canonical references
are read-only; candidate images are shown with their approval state.

Rendering compiles the saved revision on the server, checks lint and reference
content, then uses CostTracker and a persistent request record. New paid tools and
single-action thresholds require an explicit one-time confirmation; CAP and unknown
quotes still block execution. Confirmation expires after ten minutes. Double clicks
and request replay return the existing result, and unknown provider outcomes are
never automatically retried. Generated candidates remain unreviewed until the
existing review workflow selects them. The server listens on `127.0.0.1`.

Routes: `GET /w/<project>/<shot>`, `GET|PUT /api/shot/<project>/<shot>`, and POST
`references`, `compile`, `render`, `stage-export`, `acquire`, `heartbeat`, `release`
below that API path. Mutations require the current lease and, when saving/exporting,
the current revision; image uploads accept validated PNG/JPEG/WebP only.
