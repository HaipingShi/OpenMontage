# Persistent H3 connection

The application's ignored `.env` is the local source of H3 connection credentials. Configure `COMFYUI_VIDEO_SERVER_URL` and `COMFYUI_VIDEO_API_KEY` for the registry's `comfyui_video` tool. Other ComfyUI capabilities keep their existing endpoints. All video HTTP operations and WebSocket waits send the scoped Bearer header; an explicitly different server does not inherit that key.

A project using the separate Node H3 client stores a non-secret `integrations/h3/connection.json` with `env_file` pointing to the application `.env`. That file supplies `COMFY_URL` and `COMFY_TOKEN`, without importing unrelated credentials or requiring `--env-file` on each invocation. The project client uses the resolver from `tools/_comfyui/node_connection.mjs` (copied alongside it as `connection.mjs`). Paths resolve relative to the registration file, not the shell working directory. Explicit arguments/environment remain overrides. Rotate the H3 key in the application `.env`; do not ask production agents to find keys in deployment logs or other checkouts.

For the local `test-zhenjiao-fati` registration, agents can run from any working directory:

```bash
node ~/OpenMontageData/projects/test-zhenjiao-fati/integrations/h3/check-connection.mjs
node ~/OpenMontageData/projects/test-zhenjiao-fati/integrations/h3/client.mjs ping
```

The first command only checks readiness and model presence, recording `connection-status.json`. Neither command generates videos. The approved generation runner also uses this same registered client automatically; follow existing production approvals and task-resume rules before invoking it.

HTTP 401/403 means rejected authentication. HTTP 404 can mean stopped/preparing cloud service or routing failure. These are not reasons to delete a registered address or ask for it again. Restore the existing service through its maintainer, then repeat the readiness check. The production service key cannot log into the cloud console or start GPU instances.

The `.env` contains secrets, must remain ignored, and should have mode 0600. Never copy values into workflow JSON, task logs, Backlot or Git. Local registration paths and model selections may be documented without keys. If a workspace moves to a different machine, update its local `env_file` registration to an administrator-provisioned secret file.

The current workspace follows `lib.paths.PROJECTS_DIR` and lives under `~/OpenMontageData/projects/`. Run project clients and read task/media records from this independent user-data directory. Repository-local legacy copies have been moved to the user-data archive and are historical records, not active project entry points.

## Exact vocal timing: reference audio is not a target lock

H3 Ref2VA reference-audio conditioning, including a `fully_copy` prompt, does not by itself guarantee that the generated target audio retains the original syllable timing. Replacing generated audio with the song master after generation cannot repair already mismatched mouth motion.

The verified project contract for exact vocal timing is:

1. Encode the original vocal window with `VAEEncodeAudio`, using the same audio input and audio VAE as H3 conditioning.
2. Pass that latent through `LTXVFreezeLatent`.
3. Combine the original H3 target video latent and frozen audio latent with `LTXVConcatAVLatent`, and connect that result to the actual sampler's `latent_image` input. Merely adding disconnected nodes is insufficient.
4. Validate this wiring before uploads/submission. Verify the frozen audio latent before/after sampling and retain the graph, hashes, and confirmed prompt ID. Human review is still required for mouth synchronization; a wiring check is not visual approval.

These are ComfyUI utility nodes in the verified H3 graph, not a switch to an LTX model. Check the deployed service's node schema before use; do not silently install nodes, change models, or fall back to reference-only audio.

The `zhenjiao-fati-mtv-20261009-v3` project keeps its old `ref2va-8s.json` under `integrations/h3/workflows/quarantine/reference-only-20261023/` as immutable historical evidence. Its active template is `ref2va-audio-locked-8s.json`; its client rejects unlocked Ref2VA audio graphs before network submission. Follow that project's `workflow-policy.json` and `AGENTS.md`. The old completed batch runners/delivery writers are retired to prevent duplicate generation or overwriting newer human review. Other projects must explicitly adopt and verify the contract; this documentation does not imply their clients already enforce it.
