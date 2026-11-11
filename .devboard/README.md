# Dev-Board launcher

This manifest launches the Backlot project library as a foreground process.
Dev-Board manages its port, logs, and process lifecycle. The `BACKLOT_PORT`
environment variable keeps Backlot's CLI aligned with the assigned port.

Prepare the project environment once from the repository root:

```sh
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

In Dev-Board, scan or import this repository. The Runtime Manifest API can
inspect and associate `.devboard/runtime.json`; installations without a
manifest UI can use the custom launch command below directly. Start the
project and open its main endpoint to view Backlot. Stop and restart are
managed by Dev-Board.

If Dev-Board reports `LAUNCH_PROFILE_MAIN_COMMAND_MISSING` despite the
manifest association, configure this equivalent custom launch command in
the project card. Some versions still check for a legacy command before
starting a manifest-backed project:

```sh
BACKLOT_PORT={{port}} PYTHONUNBUFFERED=1 .venv/bin/python -m backlot serve
```

The launcher serves the board; video production still runs through an AI
assistant following `AGENT_GUIDE.md`. Remotion, HyperFrames, and generation
providers require their own setup when used in a production run.
