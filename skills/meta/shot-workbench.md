# Shot Workbench: shared structured shots

Use this protocol in the generated-shot portions of cinematic, character-animation, animation, music-video, hybrid and animated-explainer. Source footage keeps its existing source review and editing workflow. This is an optional editing surface, not a separate production pipeline or an automatic generation step.

## When to offer it

Offer the workbench for at least three references, multiple people/characters, exact camera position or blocking, first/last-frame continuity, or a shot rejected twice. State the concrete reason and the currently approved tool/provider/model. Simple generated shots may use the same headless compiler without opening a browser.

## One source of truth

`projects/<project>/shots/<shot_id>.json` is the canonical shot spec. Initialize from the corresponding scene; preserve `shot_spec_path` and `shot_spec_revision` on the scene entry. The stage camera, subjects, framing, environment, exclusions, references and notes are editable structured fields. The final prompt is a read-only compiler output. Never save a separate free-text prompt or overwrite artist canon / locked identity traits. Candidate artist images remain candidates until the separate artist approval workflow promotes them.

Use the local registry tool `shot_compiler` or the CLI. The compiler consumes an explicit immutable snapshot: same semantic spec, identity, tool variant and refs produce the same compiled hash. Editing a spec requires the live holder's lease and matching revision; preserve a conflicting local draft and reload the canonical version before reconciling.

Resolve artist aliases only within this project's `assets/artists/`. Reusable assets must first be explicitly imported through `lib.project_assets`, pinning library/asset/version/hash in `assets/bindings.json`; do not look up same-named global artists. The imported source remains fixed and a project-specific look uses an explicit private fork. Neither generating a shot nor changing its look publishes to the library. Record all generation usage and artist ledger events in the current project. An unavailable or corrupt binding blocks generation; it cannot be replaced with a library's latest version.

```sh
python -m lib.shot_workbench acquire <project> <shot> --holder agent --reason 'Prepare camera and reference structure'
python -m lib.shot_workbench status <project> <shot> --holder agent
python -m lib.shot_workbench edit <project> <shot> --holder agent --revision <n> --lease-id <lease> --file <structured-shot.json>
python -m lib.shot_workbench compile <project> <shot> --tool <approved-tool>
python -m lib.shot_workbench dry-run <project> <shot> --tool <approved-tool>
python -m lib.shot_workbench release <project> <shot> --holder agent --lease-id <lease>
python -m lib.shot_workbench open <project> <shot> --reason 'Please adjust the two-person blocking'
```

## Handoff

Before `open`, release an existing agent lease. Give the user the workbench URL and the reason; end the production turn until the user releases the shot. While `holder=user`, the Agent only reads. While `holder=agent`, the UI is read-only. On user release, inspect the returned diff or the appended `decision_log` shot_handoff entry, read the new revision, acquire a fresh agent lease if needed, and continue the already approved stage. Leases expire after two hours without refresh; explicit `steal --reason ...` records a forced handoff. Never steal an unresolved running/unknown generation request.

## Compile, review, execute

Before generation, compile and inspect prompt, negative prompt, actual ordered references, discarded refs, route, fixed tool variant, estimated cost and lint. Fix every lint error; warnings require an explicit review, particularly missing artist canon, cropped identity refs, empty subjects with movement, and detailed hands. `movement.cause` must explain non-static motion. Do not repair these by quietly changing the creative route. A provider that cannot consume depth/pose receives composition and camera text only, with an explicit unsupported-control warning.

The workbench render endpoint enforces `CostTracker`, the project's `single_action_approval_usd`, new paid-tool approval and CAP. Its one-time confirmation binds revision, hash, reference content, concrete tool/model and quote. Unknown cost blocks paid execution. A changed shot or quote requires a fresh preview/confirmation. Preserve asset candidate lineage, parent_asset, compiled_hash, source revision, actual reference_map and artist ledger entries. Do not bypass this boundary with a direct provider call or retry a running/unknown request.

Review the existing stage quality gates as well as the shot compiler lint. Stage exports are coarse composition/depth/pose references, not final anatomy or finger control. Source footage needs neither this spec nor paid generation.
