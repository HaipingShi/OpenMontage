"""The offline fixture CLI follows user-data defaults without running media jobs."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "automv_dry_run.py"


@pytest.fixture
def fixture_cli(monkeypatch, tmp_path):
    projects = tmp_path / "configured-projects"
    monkeypatch.setattr("lib.paths.PROJECTS_DIR", projects)
    spec = importlib.util.spec_from_file_location("automv_dry_run_workspace", SCRIPT)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    report = {"duration_seconds": 30, "timeline": {}, "shots": 3, "candidates": 6,
              "external_calls": 0, "master_unchanged": True}
    run = Mock(return_value=report)
    monkeypatch.setattr(script, "run", run)
    monkeypatch.setattr(script, "command", Mock(side_effect=AssertionError("media subprocess must stay mocked")))
    return script, projects, run, report


def test_main_defaults_to_configured_user_data_root(fixture_cli, capsys):
    script, projects, run, report = fixture_cli

    assert script.main([]) == 0

    assert Path(run.call_args.args[0]) == projects / "automv-upgrade-dry-run"
    assert json.loads(capsys.readouterr().out) == report
    script.command.assert_not_called()
    assert not (Path.cwd() / "projects").exists()


@pytest.mark.parametrize("absolute", [False, True])
def test_explicit_output_keeps_user_override(fixture_cli, tmp_path, capsys, absolute):
    script, _, run, report = fixture_cli
    output = str(tmp_path / "custom-output") if absolute else "custom-output"

    assert script.main(["--output", output]) == 0

    assert Path(run.call_args.args[0]) == Path(output)
    assert json.loads(capsys.readouterr().out) == report
    script.command.assert_not_called()


@pytest.mark.parametrize("boundary", ["workspace", "nested"])
def test_default_workspace_symlink_is_rejected_before_running_fixture(fixture_cli, tmp_path, boundary):
    script, projects, run, _ = fixture_cli
    output = projects / "automv-upgrade-dry-run"
    outside = tmp_path / "other-project"
    outside.mkdir()
    projects.mkdir()
    if boundary == "workspace":
        output.symlink_to(outside, target_is_directory=True)
    else:
        output.mkdir()
        (output / "nested-output.json").symlink_to(outside / "output.json")

    with pytest.raises(SystemExit) as exc:
        script.main([])

    assert exc.value.code == 2
    run.assert_not_called()
    script.command.assert_not_called()
    assert not list(outside.iterdir())


def test_explicit_output_keeps_authorized_symlink_override(fixture_cli, tmp_path):
    script, _, run, _ = fixture_cli
    outside = tmp_path / "other-project"
    outside.mkdir()
    output = tmp_path / "chosen-output"
    output.symlink_to(outside, target_is_directory=True)

    assert script.main(["--output", str(output)]) == 0

    assert run.call_args.args == (str(output),)
