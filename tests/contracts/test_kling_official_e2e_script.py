"""Contract tests for the Kling official E2E smoke script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "kling_official_animated_explainer_e2e.py"


@pytest.fixture(autouse=True)
def isolate_environment_loading(monkeypatch):
    monkeypatch.setattr("lib.env_loader.load_env", lambda root: None)


def _load_script():
    spec = importlib.util.spec_from_file_location("kling_official_animated_explainer_e2e", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_status_redacts_secret_values():
    script = _load_script()

    status = script._env_status(
        {
            "KLING_API_KEY": "secret-token",
            "KLING_API_BASE_URL": "https://api-beijing.klingai.com",
            "FAL_KEY": "",
        }
    )

    assert status["KLING_API_KEY"]["present"] is True
    assert status["KLING_API_KEY"]["display"] == "<set:12 chars>"
    assert "secret-token" not in repr(status)
    assert status["KLING_API_BASE_URL"]["display"] == "https://api-beijing.klingai.com"
    assert status["FAL_KEY"]["present"] is False


def test_cli_modes_are_explicit_and_non_paid_by_default():
    script = _load_script()

    assert script._execution_mode(script._parse_args([])) == "dry_run"
    assert script._execution_mode(script._parse_args(["--live-tts"])) == "live_tts"
    assert script._execution_mode(script._parse_args(["--live-full"])) == "live_full"
    assert script._execution_mode(script._parse_args(["--live"])) == "live_full"
    assert script._execution_mode(script._parse_args(["--live-avatar"])) == "live_avatar"
    assert script._execution_mode(script._parse_args(["--live-all"])) == "live_all"


def test_video_duration_aligns_to_narration_within_kling_limits():
    script = _load_script()

    assert script._aligned_video_duration("3", 6.05) == "7"
    assert script._aligned_video_duration("10", 6.05) == "10"
    assert script._aligned_video_duration("3", None) == "3"
    assert script._aligned_video_duration("3", 30.0) == "15"


def test_live_all_combines_core_and_avatar_results(monkeypatch, tmp_path):
    script = _load_script()
    core = {
        "artifacts": {"narration": "narration.mp3", "final": "final.mp4"},
        "ffprobe": {"final": {"duration": 6.0}},
        "estimated_cost_usd": 0.25,
    }
    avatar_suite = {
        "artifacts": {"avatar": "avatar.mp4", "lip_sync": "lip.mp4"},
        "ffprobe": {"avatar": {"duration": 7.0}, "lip_sync": {"duration": 7.0}},
        "estimated_cost_usd": 0.75,
    }
    monkeypatch.setattr(script, "_run_live_full", lambda *args, **kwargs: core)
    monkeypatch.setattr(
        script, "_run_live_avatar_suite", lambda *args, **kwargs: avatar_suite
    )

    result = script._run_live_all(
        tmp_path,
        voice_id="voice-a",
        voice_language="en",
        voice_speed=1.0,
        text="hello",
        timeout_seconds=30,
        poll_interval=1.0,
        include_account_usage=False,
        video_duration="3",
    )

    assert result["core"] is core
    assert result["avatar_suite"] is avatar_suite
    assert result["artifacts"]["final"] == "final.mp4"
    assert result["artifacts"]["lip_sync"] == "lip.mp4"
    assert result["estimated_cost_usd"] == 1.0


@pytest.fixture
def isolated_main(monkeypatch, tmp_path):
    # Import configuration after the test's user-data root is installed. Never
    # discover tools or call a provider from this CLI regression fixture.
    projects = tmp_path / "user-data" / "projects"
    monkeypatch.setattr("lib.paths.PROJECTS_DIR", projects)
    monkeypatch.setattr("lib.env_loader.load_env", lambda root: None)
    script = _load_script()
    checkout = tmp_path / "application"
    checkout.mkdir()
    monkeypatch.setattr(script, "ROOT", checkout)
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    mocks = {}
    for name, result in {
        "load_pipeline": {"name": "animated-explainer"},
        "_discover": None,
        "_env_status": {},
        "_tool_statuses": {},
        "_capability_summary": {},
        "_rank_selectors": {},
        "_dry_run": {"mock": True},
        "_missing_required_env": [],
        "_run_live_tts": {"artifacts": {"narration": "mock.mp3"}},
        "_run_live_full": {"artifacts": {"final": "mock.mp4"}},
        "_run_live_avatar_suite": {"artifacts": {"avatar": "mock.mp4", "lip_sync": "mock.mp4"}},
        "_run_live_all": {"artifacts": {"avatar": "mock.mp4", "lip_sync": "mock.mp4"}},
    }.items():
        mocks[name] = Mock(return_value=result)
        monkeypatch.setattr(script, name, mocks[name])
    return script, projects, checkout, mocks


@pytest.mark.parametrize("argv,slug", [([], "kling-animated-explainer-e2e"), (["--project", "test-shot_2"], "test-shot_2")])
def test_main_writes_under_configured_projects_root(isolated_main, argv, slug):
    script, projects, checkout, mocks = isolated_main

    assert script.main(argv) == 0

    report_path = projects / slug / "artifacts" / "kling_official_animated_explainer_e2e_report.json"
    report = json.loads(report_path.read_text())
    assert report["project_dir"] == str(projects / slug)
    assert report["mode"] == "dry_run"
    assert not (checkout / "projects").exists()
    assert not (Path.cwd() / "projects").exists()
    mocks["_discover"].assert_called_once()
    for name in ("_run_live_tts", "_run_live_full", "_run_live_avatar_suite", "_run_live_all"):
        mocks[name].assert_not_called()


def test_live_cli_uses_the_same_configured_root_with_mocked_execution(isolated_main):
    script, projects, checkout, mocks = isolated_main

    assert script.main(["--project", "mock-live", "--live-tts"]) == 0

    assert mocks["_run_live_tts"].call_args.args == (projects / "mock-live",)
    assert not (checkout / "projects").exists()


@pytest.mark.parametrize("slug", ["", ".", "..", "../escape", "nested/project", "nested\\project", "/absolute", "has spaces", "a" * 300])
def test_invalid_project_slug_is_rejected_before_discovery_or_live(isolated_main, tmp_path, slug):
    script, _, _, mocks = isolated_main
    if slug == "/absolute":
        slug = str(tmp_path / "absolute")

    with pytest.raises(SystemExit) as exc:
        script.main(["--project", slug, "--live-tts"])

    assert exc.value.code == 2
    mocks["load_pipeline"].assert_not_called()
    mocks["_discover"].assert_not_called()
    mocks["_run_live_tts"].assert_not_called()


def test_workspace_oserror_is_rejected_before_discovery_or_live(isolated_main, monkeypatch):
    script, _, _, mocks = isolated_main
    monkeypatch.setattr(script, "_project_workspace", Mock(side_effect=OSError("fixture workspace is inaccessible")))

    with pytest.raises(SystemExit) as exc:
        script.main(["--project", "inaccessible", "--live-tts"])

    assert exc.value.code == 2
    mocks["load_pipeline"].assert_not_called()
    mocks["_discover"].assert_not_called()
    mocks["_env_status"].assert_not_called()
    mocks["_dry_run"].assert_not_called()
    mocks["_run_live_tts"].assert_not_called()


@pytest.mark.parametrize("boundary", ["receipt_parent", "receipt_target"])
def test_receipt_path_type_is_rejected_before_discovery_or_live(isolated_main, boundary):
    script, projects, _, mocks = isolated_main
    project = projects / "invalid-receipt"
    project.mkdir(parents=True)
    if boundary == "receipt_parent":
        (project / "artifacts").write_text("fixture file where a directory is required")
    else:
        (project / "artifacts" / "kling_official_animated_explainer_e2e_report.json").mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        script.main(["--project", "invalid-receipt", "--live-tts"])

    assert exc.value.code == 2
    mocks["load_pipeline"].assert_not_called()
    mocks["_discover"].assert_not_called()
    mocks["_env_status"].assert_not_called()
    mocks["_dry_run"].assert_not_called()
    mocks["_run_live_tts"].assert_not_called()


@pytest.mark.parametrize("boundary", ["project", "artifacts", "report", "assets", "assets/audio", "assets/images", "assets/video", "renders"])
def test_symlink_escape_is_rejected_before_discovery_or_live(isolated_main, tmp_path, boundary):
    script, projects, checkout, mocks = isolated_main
    outside = tmp_path / "outside"
    outside.mkdir()
    report_name = "kling_official_animated_explainer_e2e_report.json"
    # Both old and configured roots are synthetic; reproducing the bug cannot
    # write into this checkout or any real workspace.
    for root in (projects, checkout / "projects"):
        project = root / "escape-test"
        root.mkdir(parents=True, exist_ok=True)
        if boundary == "project":
            project.symlink_to(outside, target_is_directory=True)
        elif boundary == "report":
            artifacts = project / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / report_name).symlink_to(outside / report_name)
        else:
            target = project / boundary
            target.parent.mkdir(parents=True)
            target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SystemExit) as exc:
        script.main(["--project", "escape-test", "--live-tts"])

    assert exc.value.code == 2
    mocks["load_pipeline"].assert_not_called()
    mocks["_discover"].assert_not_called()
    mocks["_env_status"].assert_not_called()
    mocks["_dry_run"].assert_not_called()
    mocks["_run_live_tts"].assert_not_called()
    assert not (outside / report_name).exists()
