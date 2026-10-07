"""Real tool entry points default to user workspaces; media work stays mocked."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools.analysis import video_analyzer
from tools.character import character_animation
from tools.base_tool import ToolResult


@pytest.fixture
def projects(monkeypatch, tmp_path):
    root = tmp_path / "configured-projects"
    monkeypatch.setattr("lib.paths.PROJECTS_DIR", root)
    # Patch module imports too when another test has already imported a tool.
    monkeypatch.setattr(video_analyzer, "PROJECTS_DIR", root, raising=False)
    monkeypatch.setattr(character_animation, "PROJECTS_DIR", root, raising=False)
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return root


@pytest.mark.parametrize("explicit", [False, True])
def test_video_analysis_execute_preserves_output_override_and_uses_user_default(projects, monkeypatch, tmp_path, explicit):
    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"synthetic input; probing is mocked")
    tool = video_analyzer.VideoAnalyzer()
    probe = Mock(return_value=8.0)
    command = Mock(side_effect=AssertionError("no FFmpeg or other process in this regression"))
    monkeypatch.setattr(tool, "_get_duration", probe)
    monkeypatch.setattr(tool, "run_command", command)
    monkeypatch.setattr(video_analyzer.time, "time", lambda: 1234567890)
    inputs = {"source": str(source), "analysis_depth": "transcript_only"}
    expected = projects / "_analysis" / "analysis_1234567890"
    if explicit:
        inputs["output_dir"] = "custom-analysis"
        expected = Path("custom-analysis")

    result = tool.execute(inputs)

    assert result.success
    assert result.artifacts == [str(expected / "video_analysis_brief.json")]
    assert json.loads((expected / "video_analysis_brief.json").read_text())["source"]["duration_seconds"] == 8.0
    probe.assert_called_once_with(source)
    command.assert_not_called()
    assert not (Path.cwd() / "projects").exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_character_execute_preserves_output_override_and_uses_user_default(projects, monkeypatch, explicit):
    render = Mock(side_effect=AssertionError("no browser or FFmpeg render in this regression"))
    monkeypatch.setattr(character_animation, "_render_preview_mp4", render)
    inputs = {"action_timeline": {"scenes": [{"id": "scene", "end_seconds": 1, "actions": []}]}}
    expected = projects / "character-preview" / "preview.html"
    if explicit:
        inputs["output_path"] = "custom-preview/index.html"
        expected = Path("custom-preview/index.html")

    result = character_animation.CharacterRigRenderer().execute(inputs)

    assert result.success
    assert result.data["preview_path"] == str(expected)
    assert expected.exists()
    assert Path(result.data["hyperframes_workspace"]) == expected.parent / "hyperframes"
    assert Path(result.data["composition_path"]).exists()
    render.assert_not_called()
    assert not (Path.cwd() / "projects").exists()


def test_analysis_default_rejects_existing_symlink_before_probing(projects, monkeypatch, tmp_path):
    outside = tmp_path / "other-project"
    outside.mkdir()
    for root in (projects, Path.cwd() / "projects"):
        root.mkdir(parents=True, exist_ok=True)
        (root / "_analysis").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"fixture")
    tool = video_analyzer.VideoAnalyzer()
    probe = Mock(return_value=8.0)
    monkeypatch.setattr(tool, "_get_duration", probe)

    result = tool.execute({"source": str(source), "analysis_depth": "transcript_only"})

    assert not result.success
    assert "symlink" in result.error.lower()
    probe.assert_not_called()
    assert not list(outside.iterdir())


@pytest.mark.parametrize("boundary", ["scenes.json", "keyframes"])
def test_analysis_default_rejects_internal_symlinks_before_child_tools(projects, monkeypatch, tmp_path, boundary):
    from tools.analysis.audio_energy import AudioEnergy
    from tools.analysis.frame_sampler import FrameSampler
    from tools.analysis.scene_detect import SceneDetect

    outside = tmp_path / "other-project"
    outside.mkdir()
    for root in (projects, Path.cwd() / "projects"):
        output = root / "_analysis" / "analysis_1234567890"
        output.mkdir(parents=True)
        (output / boundary).symlink_to(outside / "scenes.json" if boundary == "scenes.json" else outside,
                                      target_is_directory=boundary == "keyframes")
    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"fixture")
    probe = Mock(return_value=8.0)
    monkeypatch.setattr(video_analyzer.time, "time", lambda: 1234567890)
    tool = video_analyzer.VideoAnalyzer()
    monkeypatch.setattr(tool, "_get_duration", probe)

    def write_scenes(inputs):
        Path(inputs["output_path"]).write_text('{"scenes": []}')
        return ToolResult(success=True, data={"scenes": []})

    def write_frame(inputs):
        directory = Path(inputs["output_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "frame.jpg").write_bytes(b"synthetic frame")
        return ToolResult(success=True, data={"frames": []})

    scenes = Mock(side_effect=write_scenes)
    frames = Mock(side_effect=write_frame)
    monkeypatch.setattr(SceneDetect, "execute", scenes)
    monkeypatch.setattr(FrameSampler, "execute", frames)
    monkeypatch.setattr(AudioEnergy, "execute", Mock(return_value=ToolResult(success=False, error="fixture skipped")))

    result = tool.execute({"source": str(source)})

    assert not result.success
    assert "symlink" in result.error.lower()
    probe.assert_not_called()
    scenes.assert_not_called()
    frames.assert_not_called()
    assert not list(outside.iterdir())


@pytest.mark.parametrize("boundary", ["project", "preview", "workspace", "frames"])
def test_character_default_rejects_existing_symlinks_before_writing(projects, monkeypatch, tmp_path, boundary):
    outside = tmp_path / "other-project"
    outside.mkdir()
    for root in (projects, Path.cwd() / "projects"):
        project = root / "character-preview"
        root.mkdir(parents=True, exist_ok=True)
        if boundary == "project":
            project.symlink_to(outside, target_is_directory=True)
        else:
            project.mkdir()
            target = project / {"preview": "preview.html", "workspace": "hyperframes", "frames": "preview_frames"}[boundary]
            target.symlink_to(outside / "preview.html" if boundary == "preview" else outside,
                              target_is_directory=boundary != "preview")

    def write_render(preview_path, video_path, duration_seconds, fps):
        frames = video_path.parent / f"{video_path.stem}_frames"
        frames.mkdir(parents=True, exist_ok=True)
        (frames / "frame_0000.png").write_bytes(b"synthetic rendered frame")

    render = Mock(side_effect=write_render)
    monkeypatch.setattr(character_animation, "_render_preview_mp4", render)

    result = character_animation.CharacterRigRenderer().execute({"action_timeline": {"scenes": []}, "render_video": True})

    assert not result.success
    assert "symlink" in result.error.lower()
    render.assert_not_called()
    assert not list(outside.iterdir())
