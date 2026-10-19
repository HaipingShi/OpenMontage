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

The current workspace follows `lib.paths.PROJECTS_DIR` and lives under `~/OpenMontageData/projects/`. The repository-local legacy copy is also configured for compatibility; do not mistake it for the active project or move task/media records between them.
