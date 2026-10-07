"""Atelier helpers use isolated user workspaces; rendering is always mocked."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import atelier_snapshots as snapshots
from scripts import scaffold_atelier_project as scaffolding
from tools.video.video_compose import VideoCompose


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    repo = tmp_path / "software checkout"
    projects = tmp_path / "user data" / "works"
    cwd = tmp_path / "invocation"
    for path in (repo, projects, cwd):
        path.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(snapshots, "REPO_ROOT", repo)
    monkeypatch.setattr(snapshots, "COMPOSER_DIR", repo / "remotion-composer")
    monkeypatch.setattr(snapshots, "PROJECTS_DIR", projects, raising=False)
    monkeypatch.setattr(scaffolding, "PROJECTS_DIR", projects, raising=False)
    stages, renders = [], []

    def stage(_tool, entry, composer):
        stages.append((entry, composer))
        return composer / "projects/film/index.tsx"

    def run(command, **options):
        renders.append((command, options))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(VideoCompose, "_stage_atelier_project", stage)
    monkeypatch.setattr(snapshots.subprocess, "run", run)
    return SimpleNamespace(repo=repo, projects=projects, cwd=cwd, stages=stages, renders=renders)


def project_files(directory: Path, *, bespoke=None, scene_id="s01"):
    (directory / "artifacts").mkdir(parents=True)
    (directory / "public").mkdir()
    (directory / "index.tsx").write_text("// synthetic engine entry")
    (directory / "artifacts/props.json").write_text('{"fps":24}')
    (directory / "artifacts/scene_plan.json").write_text(json.dumps({"scenes": [
        {"id": scene_id, "start_seconds": 1, "end_seconds": 3}]}))
    (directory / "artifacts/edit_decisions.json").write_text(json.dumps({
        "bespoke": {"composition_id": "Scene1", **(bespoke or {})}}))
    return directory


def test_scaffold_default_cli_uses_user_projects_not_cwd(workspace, capsys):
    assert scaffolding.main(["film"]) == 0
    assert (workspace.projects / "film/index.tsx").is_file()
    assert not (workspace.cwd / "projects").exists()
    assert str(workspace.projects / "film") in capsys.readouterr().out


def test_scaffold_default_direct_api_uses_user_projects(workspace):
    assert scaffolding.scaffold("film", "Scene1") == workspace.projects / "film"


def test_explicit_scaffold_root_preserves_root_projects_and_existing_files(workspace, capsys):
    project = scaffolding.scaffold("film", "Scene1", workspace.repo)
    assert project == workspace.repo / "projects/film"
    content = (project / "Composition.tsx").read_text()
    assert scaffolding.main(["film", "--root", str(workspace.repo)]) == 0
    assert (project / "Composition.tsx").read_text() == content
    assert "skip (exists)" in capsys.readouterr().out


def test_generated_readme_executes_with_absolute_platform_native_project_paths(workspace, monkeypatch):
    # Allow the old logger to reach README generation so this RED proves the
    # render snippet itself, independently of the external-cwd logging bug.
    monkeypatch.chdir(workspace.repo)
    project = scaffolding.scaffold("film", "Scene1", workspace.repo)
    snippet = (project / "README.md").read_text().split("```python\n", 1)[1].split("```", 1)[0]
    inputs = []
    monkeypatch.setattr(VideoCompose, "execute", lambda _tool, payload: inputs.append(payload))
    monkeypatch.chdir(workspace.cwd)
    exec(compile(snippet, "generated README", "exec"), {})
    payload = inputs[0]
    assert Path(payload["output_path"]) == project / "renders/final.mp4"
    config = payload["edit_decisions"]["bespoke"]
    assert Path(config["entry"]) == project / "index.tsx"
    assert Path(config["props_path"]) == project / "artifacts/props.json"
    assert Path(config["public_dir"]) == project / "public"
    assert config["composition_id"] == "Scene1"
    assert all(Path(path).is_absolute() for path in (payload["output_path"], config["entry"], config["props_path"], config["public_dir"]))


@pytest.mark.parametrize("slug", ["../escape", "film/child", "film\\child", ".", "", "UPPER"])
def test_scaffold_direct_api_rejects_unsafe_slugs_before_creating_files(workspace, monkeypatch, slug):
    monkeypatch.chdir(workspace.repo)
    with pytest.raises(ValueError, match="slug"):
        scaffolding.scaffold(slug, "Scene1", workspace.repo)
    assert not (workspace.repo / "projects").exists()


@pytest.mark.parametrize("composition_id", ['Scene" injected', "Scene\nname", "../scene"])
def test_scaffold_direct_api_rejects_injected_composition_identifiers(workspace, monkeypatch, composition_id):
    monkeypatch.chdir(workspace.repo)
    with pytest.raises(ValueError, match="composition"):
        scaffolding.scaffold("film", composition_id, workspace.repo)
    assert not (workspace.repo / "projects").exists()


@pytest.mark.parametrize("relative", ["index.tsx", "README.md", "artifacts", "public", "assets/music"])
def test_scaffold_rejects_existing_output_symlinks_before_any_write(workspace, tmp_path, monkeypatch, relative):
    monkeypatch.chdir(workspace.repo)
    project = workspace.repo / "projects/film"
    project.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    link = project / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if Path(relative).suffix:
        outside = target / "existing.txt"
        outside.write_text("keep original")
        link.symlink_to(outside)
    else:
        link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink|project"):
        scaffolding.scaffold("film", "Scene1", workspace.repo)
    assert not (project / "Root.tsx").exists()
    assert sorted(path.name for path in target.iterdir()) == (["existing.txt"] if Path(relative).suffix else [])


def test_scaffold_cli_rejects_project_symlink_outside_default_root(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.projects / "film").symlink_to(outside, target_is_directory=True)
    assert scaffolding.main(["film"]) == 2
    assert list(outside.iterdir()) == []


def test_snapshots_default_uses_external_user_project_and_logs_without_relative_to_repo(workspace, capsys):
    project = project_files(workspace.projects / "film")
    assert snapshots.main(["film"]) == 0
    assert workspace.stages == [(project / "index.tsx", snapshots.COMPOSER_DIR)]
    command, options = workspace.renders[0]
    assert Path(command[5]) == project / "snapshots/s01.png"
    assert "--frame=48" in command
    assert f"--props={project / 'artifacts/props.json'}" in command
    assert f"--public-dir={project / 'public'}" in command
    assert options["cwd"] == snapshots.COMPOSER_DIR
    assert options["check"] and "shell" not in options
    assert str(project / "snapshots") in capsys.readouterr().out


@pytest.mark.parametrize("legacy_prefix", [False, True])
def test_snapshot_artifact_relative_paths_are_project_scoped(workspace, legacy_prefix):
    prefix = "projects/film/" if legacy_prefix else ""
    project = project_files(workspace.projects / "film", bespoke={
        "entry": prefix + "index.tsx", "props_path": prefix + "artifacts/props.json", "public_dir": prefix + "public"})
    assert snapshots.main(["film"]) == 0
    assert workspace.stages[0][0] == project / "index.tsx"
    assert f"--props={project / 'artifacts/props.json'}" in workspace.renders[0][0]
    assert f"--public-dir={project / 'public'}" in workspace.renders[0][0]


def test_snapshot_foreign_legacy_prefix_is_rejected_without_repo_fallback(workspace):
    project_files(workspace.projects / "film", bespoke={"entry": "projects/other/index.tsx"})
    assert snapshots.main(["film"]) == 2
    assert not workspace.stages and not workspace.renders


@pytest.mark.parametrize("absolute", [False, True])
def test_snapshot_cli_path_overrides_resolve_from_caller_cwd_and_allow_external_inputs(workspace, tmp_path, absolute):
    project_files(workspace.projects / "film", bespoke={"entry": "projects/other/ignored.tsx"})
    source = tmp_path / "explicit inputs" if absolute else workspace.cwd
    source.mkdir(exist_ok=True)
    (source / "entry.tsx").write_text("// explicit entry")
    (source / "props.json").write_text('{"fps":25}')
    (source / "public").mkdir()
    values = [str(source / name) if absolute else name for name in ("entry.tsx", "props.json", "public")]
    assert snapshots.main(["film", "--entry", values[0], "--props", values[1], "--public-dir", values[2]]) == 0
    assert workspace.stages[0][0] == source / "entry.tsx"
    command = workspace.renders[0][0]
    assert "--frame=50" in command
    assert f"--props={source / 'props.json'}" in command
    assert f"--public-dir={source / 'public'}" in command


@pytest.mark.parametrize("slug", ["../escape", "film/child", "film\\child", "."])
def test_snapshots_reject_unsafe_project_segments(workspace, slug):
    assert snapshots.main([slug]) == 2
    assert not workspace.stages and not workspace.renders


def test_snapshot_project_symlink_cannot_escape_user_root(workspace, tmp_path):
    outside = project_files(tmp_path / "outside")
    (workspace.projects / "film").symlink_to(outside, target_is_directory=True)
    (workspace.repo / "projects").mkdir()
    (workspace.repo / "projects/film").symlink_to(outside, target_is_directory=True)
    assert snapshots.main(["film"]) == 2
    assert not workspace.stages and not workspace.renders
    assert not (outside / "snapshots").exists()


@pytest.mark.parametrize("relative", ["snapshots", "snapshots/s01.png"])
def test_snapshots_reject_existing_output_symlinks_before_staging(workspace, tmp_path, monkeypatch, relative):
    project = project_files(workspace.repo / "projects/film")
    monkeypatch.setattr(snapshots, "PROJECTS_DIR", workspace.repo / "projects")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if Path(relative).suffix:
        link.symlink_to(outside / "s01.png")
    else:
        link.symlink_to(outside, target_is_directory=True)
    assert snapshots.main(["film"]) == 2
    assert not workspace.stages and not workspace.renders
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("scene_id", ["../escape", "folder/scene", "scene\\escape", ".", "/absolute"])
def test_snapshots_reject_unsafe_scene_filename_segments_before_staging(workspace, monkeypatch, scene_id):
    project_files(workspace.repo / "projects/film", scene_id=scene_id)
    monkeypatch.setattr(snapshots, "PROJECTS_DIR", workspace.repo / "projects")
    assert snapshots.main(["film"]) == 2
    assert not workspace.stages and not workspace.renders


def test_snapshot_only_selection_retains_existing_filter(workspace):
    project_files(workspace.projects / "film")
    assert snapshots.main(["film", "--only", "another-scene"]) == 0
    assert not workspace.renders
