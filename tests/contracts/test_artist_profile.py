"""Contracts for persistent digital artists (artists/<id>/ + lib.artist_profile)."""

from __future__ import annotations

import copy
import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from lib import artist_profile as ap
from schemas.artifacts import ARTIFACT_NAMES, validate_artifact
from tests.fixtures.artist_factory import write_artist, write_library, template_profile

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def artist_library(tmp_path, monkeypatch):
    root = write_library(tmp_path / "library", pending_alpha=True, provider_bound=True)
    monkeypatch.setattr(ap, "ARTISTS_DIR", root)
    for artist in ("solo", "alpha", "beta"):
        ap.record_ledger(artist, {"asset": "bible/fixture.png", "tool": "fixture_writer", "human_input": "Synthetic test asset"}, root)
    return root


def _raw(artist_id: str) -> dict:
    if artist_id == "_template":
        return template_profile()
    return {key: value for key, value in ap.load_artist(artist_id).items() if key != "_dir"}


def test_schema_registered():
    assert "artist_profile" in ARTIFACT_NAMES


def test_synthetic_catalog_validates_and_is_listed():
    artists = ap.list_artists()
    assert "solo" in artists
    assert "_template" not in artists
    for artist_id in artists:
        profile = ap.load_artist(artist_id)
        assert profile["id"] == artist_id


def test_template_validates_against_schema():
    validate_artifact("artist_profile", _raw("_template"))


def test_solo_bible_files_exist_and_voice_pending():
    profile = ap.load_artist("solo")
    assert any("voice local reference not ready" in issue for issue in ap.check_artist(profile))
    assert ap.check_artist(profile, voice_route="visual_only") == []
    assert profile["voice"]["lock_status"] == "pending_audition"
    assert profile["status"] == "draft"


def test_only_canonical_images_become_references():
    profile = ap.load_artist("solo")
    refs = ap.identity_prompt(profile, "medium shot on a pier at night")["reference_images"]
    names = [Path(p).name for p in refs]
    assert names[0] == "solo_main_front.png"
    assert "solo_main_expressions.png" not in names
    assert "keyvisual_main_night.png" not in names
    assert all(Path(p).is_file() for p in refs)


def test_identity_prompt_is_built_from_canon():
    profile = ap.load_artist("solo")
    result = ap.identity_prompt(profile, "close-up singing, eyes closed", look="main")
    assert result["prompt"].startswith(profile["identity"]["prompt_core"].strip())
    assert "plain test jacket" in result["prompt"]
    assert "close-up singing" in result["prompt"]
    assert result["locked_traits"] == profile["identity"]["locked_traits"]
    with pytest.raises(KeyError):
        ap.identity_prompt(profile, "x", look="does-not-exist")


def test_music_style_keeps_signature_and_cap():
    profile = ap.load_artist("solo")
    signature = profile["voice"]["style_signature"].strip()
    assert ap.music_style(profile) == signature
    style = ap.music_style(profile, "rainy bridge, 84 BPM, " + "x" * 400)
    assert style.startswith(signature)
    assert len(style) <= ap.STYLE_LIMIT


def test_schema_rejects_overlong_style_and_bad_id():
    raw = _raw("solo")
    too_long = copy.deepcopy(raw)
    too_long["voice"]["style_signature"] = "a" * 201
    with pytest.raises(Exception):
        validate_artifact("artist_profile", too_long)
    bad_id = copy.deepcopy(raw)
    bad_id["id"] = "Wan Yu"
    with pytest.raises(Exception):
        validate_artifact("artist_profile", bad_id)


def test_check_flags_locked_voice_without_track_and_unlicensed_clone(tmp_path):
    shutil.copytree(ap.ARTISTS_DIR / "solo", tmp_path / "solo")
    profile = ap.load_artist("solo", root=tmp_path)
    profile["voice"]["lock_status"] = "locked"
    profile["voice"]["source"] = "licensed_clone"
    issues = ap.check_artist(profile)
    assert any("no canonical_tracks" in i for i in issues)
    assert any("license_holder" in i for i in issues)


def test_check_flags_missing_bible_image(tmp_path):
    shutil.copytree(ap.ARTISTS_DIR / "solo", tmp_path / "solo")
    (tmp_path / "solo" / "bible" / "solo_main_front.png").unlink()
    issues = ap.check_artist(ap.load_artist("solo", root=tmp_path))
    assert any("solo_main_front.png" in i for i in issues)


def test_ledger_and_releases_are_append_only(tmp_path):
    shutil.copytree(ap.ARTISTS_DIR / "solo", tmp_path / "solo")
    before = len(ap.read_ledger("solo", root=tmp_path))
    ap.record_ledger("solo", {"asset": "voice/canonical_01.mp3", "tool": "suno_music", "human_input": "picked take 2"}, root=tmp_path)
    assert len(ap.read_ledger("solo", root=tmp_path)) == before + 1
    ap.record_release("solo", {"title": "第十四号码头", "project_id": "mv-001"}, root=tmp_path)
    releases = ap.read_releases("solo", root=tmp_path)
    assert releases[-1]["title"] == "第十四号码头" and "date" in releases[-1]
    with pytest.raises(ValueError):
        ap.record_ledger("solo", {"asset": "x"}, root=tmp_path)


def test_fixture_ledger_is_valid_jsonl():
    for entry in ap.read_ledger("solo"):
        for key in ap.LEDGER_REQUIRED:
            assert entry.get(key), entry
    assert json.dumps(ap.read_ledger("solo"), ensure_ascii=False)


@pytest.mark.parametrize("artist_id", ["alpha", "beta"])
def test_pending_and_approved_identity_review_states(artist_id):
    profile = ap.load_artist(artist_id)
    assert any("voice local reference not ready" in issue for issue in ap.check_artist(profile))
    visual_issues = ap.check_artist(profile, voice_route="visual_only")
    if artist_id == "alpha":
        assert visual_issues == ["look 'main' has no canonical bible image"]
        assert profile["identity"]["visual_lock_status"] == "fixture_pending_review"
    else:
        assert visual_issues == []
        assert profile["identity"]["visual_lock_status"] == "fixture_approved"
    assert profile["voice"]["lock_status"] == "provider_bound"
    assert ap.check_artist(profile, voice_route="suno_web") == visual_issues
    assert profile["voice"]["provider_binding"]["confirmed"]
    refs = [Path(p).name for p in ap.identity_prompt(profile, "walking at dusk")["reference_images"]]
    assert refs == ([] if artist_id == "alpha" else ["beta_main_front.png"])
    assert "keyvisual_rooftop_dusk.png" not in refs


def test_catalog_profile_has_no_project_asset_dependencies():
    profile = ap.load_artist("beta")
    assert [look["id"] for look in profile["identity"]["looks"]] == ["main"]
    assert all(Path(entry["path"]).parts[0] == "bible" for entry in profile["identity"]["bible"])


def test_alpha_and_beta_reference_each_other():
    alpha, beta = ap.load_artist("alpha"), ap.load_artist("beta")
    assert [c["artist_id"] for c in alpha["collaborators"]] == ["beta"]
    assert [c["artist_id"] for c in beta["collaborators"]] == ["alpha"]
    assert alpha["collaborators"][0]["duet_style"] == beta["collaborators"][0]["duet_style"]


def test_cast_prompt_keeps_people_separate():
    alpha, beta = ap.load_artist("alpha"), ap.load_artist("beta")
    result = ap.cast_prompt([alpha, beta], "both laughing on a rooftop at dusk")
    assert "ALPHA:" in result["prompt"] and "BETA:" in result["prompt"]
    assert "both laughing on a rooftop" in result["prompt"]
    assert set(result["reference_map"]) == {"alpha", "beta"}
    assert all(Path(p).name.startswith("alpha_") for p in result["reference_map"]["alpha"])
    assert all(Path(p).name.startswith("beta_") for p in result["reference_map"]["beta"])
    assert result["locked_traits"]["beta"] == beta["identity"]["locked_traits"]
    assert "merged faces" in result["negative_prompt"]
    with pytest.raises(ValueError):
        ap.cast_prompt([alpha], "x")


def test_duet_style_fits_and_contains_both_voices():
    alpha, beta = ap.load_artist("alpha"), ap.load_artist("beta")
    style = ap.duet_style([alpha, beta], "sunset tempo 112 BPM, " + "x" * 300)
    assert len(style) <= ap.STYLE_LIMIT
    assert alpha["voice"]["duet_descriptor"] in style
    assert beta["voice"]["duet_descriptor"] in style
    assert style.startswith(alpha["collaborators"][0]["duet_style"])
    with pytest.raises(ValueError):
        ap.duet_style([alpha, ap.load_artist("solo")])


def test_check_flags_missing_collaborator(tmp_path):
    shutil.copytree(ap.ARTISTS_DIR / "alpha", tmp_path / "alpha")
    issues = ap.check_artist(ap.load_artist("alpha", root=tmp_path))
    assert any("collaborator 'beta'" in i for i in issues)


def test_pipeline_skills_reference_artist_profile():
    stage_dir = REPO_ROOT / "skills" / "pipelines" / "music-video"
    for name in ("idea-director.md", "asset-director.md", "song-analysis-director.md", "publish-director.md"):
        assert "artist_profile" in (stage_dir / name).read_text(encoding="utf-8"), name
    assert (REPO_ROOT / "skills" / "meta" / "digital-artist.md").is_file()


def test_default_catalog_uses_user_library_from_paths(tmp_path):
    library = tmp_path / "user-library"
    env = {**os.environ, "OPENMONTAGE_LIBRARY_DIR": str(library),
           "OPENMONTAGE_DATA_DIR": str(tmp_path / "data"),
           "OPENMONTAGE_PROJECTS_DIR": str(tmp_path / "projects")}
    process = subprocess.run([sys.executable, "-c", "from lib.artist_profile import ARTISTS_DIR; print(ARTISTS_DIR)"],
                             env=env, capture_output=True, text=True, check=True)
    assert Path(process.stdout.strip()) == library / "artists"


def test_cli_explicit_root_never_falls_back_to_default_catalog(tmp_path, capsys):
    root = tmp_path / "selected-catalog"
    write_library(root)
    assert ap._main(["--root", str(root), "list"]) == 0
    assert set(line.split("\t")[0] for line in capsys.readouterr().out.splitlines()) == {"alpha", "beta", "solo"}
    assert ap._main(["--root", str(tmp_path / "empty"), "list"]) == 0
    assert capsys.readouterr().out == ""


def test_cli_project_scope_reads_private_artist_without_global_catalog(tmp_path, monkeypatch, capsys):
    import lib.paths as paths
    import lib.shot_spec as shot_spec
    project = tmp_path / "projects" / "film"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{}')
    write_artist(project / "assets/artists", "private")
    monkeypatch.setattr(paths, "PROJECTS_DIR", project.parent)
    monkeypatch.setattr(shot_spec, "PROJECTS_DIR", project.parent)
    assert ap._main(["--project", "film", "prompt", "private", "quiet room"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["prompt"].startswith("PRIVATE")
    assert all(Path(path).is_relative_to(project) for path in output["reference_images"])
    with pytest.raises(FileNotFoundError):
        ap._main(["--project", "film", "prompt", "solo", "quiet room"])


def test_cli_scope_flags_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit) as error:
        ap._main(["--project", "film", "--root", str(tmp_path), "list"])
    assert error.value.code == 2


def test_project_cli_rejects_imported_mutation_and_allows_explicit_private_fork(tmp_path, monkeypatch, capsys):
    from lib import shot_spec
    from lib.project_assets import publish_asset, import_asset, fork_asset
    project = tmp_path / "projects/film"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{}')
    source = tmp_path / "source"
    write_artist(source, "solo")
    library = tmp_path / "catalog"
    publish_asset(source / "solo", "fixture-performer", "v1", library=library)
    import_asset(project, "fixture-performer", "v1", "bound", library=library)
    monkeypatch.setattr(shot_spec, "PROJECTS_DIR", project.parent)
    arguments = ["bind-suno", "bound", "--kind", "voice", "--name", "Test binding", "--confirm"]
    with pytest.raises(ValueError, match="fork"):
        ap._main(["--project", "film", *arguments])
    assert not (project / "assets/artists/bound/ledger.jsonl").exists()
    fork_asset(project, "bound", "private")
    arguments[1] = "private"
    assert ap._main(["--project", "film", *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["confirmed"] is True
    assert ap.load_artist("private", project / "assets/artists")["voice"]["lock_status"] == "provider_bound"


def test_project_cli_reads_verify_imported_snapshot_hash(tmp_path, monkeypatch):
    from lib import shot_spec
    from lib.project_assets import publish_asset, import_asset
    project = tmp_path / "projects/film"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{}')
    source = tmp_path / "source"
    write_artist(source, "solo")
    library = tmp_path / "catalog"
    publish_asset(source / "solo", "fixture-performer", "v1", library=library)
    import_asset(project, "fixture-performer", "v1", "bound", library=library)
    path = project / "assets/artists/bound/artist.yaml"
    profile = yaml.safe_load(path.read_text())
    profile["status"] = "retired"
    path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(shot_spec, "PROJECTS_DIR", project.parent)
    with pytest.raises(ValueError, match="changed|hash|snapshot"):
        ap._main(["--project", "film", "prompt", "bound", "quiet room"])
