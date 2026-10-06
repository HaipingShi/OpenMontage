# Backlot — the living storyboard

A local board that shows a production happening: pipeline stages
lighting up, the script as a screenplay page, the scene plan as a filmstrip
that fills in as assets generate, decisions, spend, and activity — all
derived from what the pipeline already writes to `projects/<id>/`.

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
