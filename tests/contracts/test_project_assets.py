"""Project-private assets and explicit portable library versions."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from lib.shot_spec import ShotError
from tests.fixtures.artist_factory import write_artist


@pytest.fixture
def assets(tmp_path):
    source = tmp_path / "catalog"
    write_artist(source, "solo")
    project = tmp_path / "film"
    project.mkdir()
    return source / "solo", project, tmp_path / "library"


def test_project_does_not_resolve_a_global_same_name_artist(assets, monkeypatch):
    from lib import artist_profile
    from lib.project_assets import load_project_artist, project_artist_root
    source, project, library = assets
    monkeypatch.setattr(artist_profile, "ARTISTS_DIR", source.parent)
    assert project_artist_root(project) == project / "assets" / "artists"
    with pytest.raises(FileNotFoundError):
        load_project_artist(project, "solo")


def test_publish_is_an_immutable_version_and_import_is_portable(assets, tmp_path):
    from lib.project_assets import publish_asset, import_asset, load_project_artist
    source, project, library = assets
    manifest = publish_asset(source, "performer", "v1", library=library)
    assert manifest == publish_asset(source, "performer", "v1", library=library)
    binding = import_asset(project, "performer", "v1", "lead", library=library)
    assert binding["library_id"] == manifest["library_id"]
    assert binding["sha256"] == manifest["sha256"]
    assert binding["snapshot_path"] == "assets/artists/lead"
    assert binding["mode"] == "imported"
    assert all(not Path(file["path"]).is_absolute() for file in binding["files"])
    assert str(source) not in json.dumps(binding) and str(library) not in json.dumps(binding)
    assert load_project_artist(project, "lead")["id"] == "lead"
    assert import_asset(project, "performer", "v1", "lead", library=library) == binding
    relocated = tmp_path / "relocated"
    shutil.copytree(project, relocated)
    shutil.rmtree(library)
    assert load_project_artist(relocated, "lead")["identity"]["prompt_core"] == load_project_artist(project, "lead")["identity"]["prompt_core"]


def test_library_publish_rejects_overwriting_an_existing_version(assets):
    from lib.project_assets import publish_asset
    source, project, library = assets
    original = publish_asset(source, "performer", "v1", library=library)
    profile = yaml.safe_load((source / "artist.yaml").read_text())
    profile["identity"]["looks"][0]["wardrobe"] = "New red jacket"
    (source / "artist.yaml").write_text(yaml.safe_dump(profile))
    with pytest.raises(ShotError) as error:
        publish_asset(source, "performer", "v1", library=library)
    assert error.value.status_code == 409
    assert publish_asset(source, "performer", "v2", library=library)["sha256"] != original["sha256"]


def test_alias_is_project_scoped_and_cannot_silently_change_version(assets, tmp_path):
    from lib.project_assets import publish_asset, import_asset
    source, project, library = assets
    publish_asset(source, "performer", "v1", library=library)
    publish_asset(source, "performer", "v2", library=library)
    first = import_asset(project, "performer", "v1", "lead", library=library)
    other = tmp_path / "other-film"
    other.mkdir()
    assert import_asset(other, "performer", "v2", "lead", library=library)["version"] == "v2"
    with pytest.raises(ShotError) as error:
        import_asset(project, "performer", "v2", "lead", library=library)
    assert error.value.status_code == 409
    assert json.loads((project / "assets" / "bindings.json").read_text())["bindings"] == [first]


@pytest.mark.parametrize("tamper", ["profile", "reference", "extra"])
def test_imported_snapshot_hash_changes_are_rejected(assets, tamper):
    from lib.project_assets import publish_asset, import_asset, load_project_artist
    source, project, library = assets
    publish_asset(source, "performer", "v1", library=library)
    import_asset(project, "performer", "v1", "lead", library=library)
    root = project / "assets" / "artists" / "lead"
    if tamper == "profile":
        (root / "artist.yaml").write_text((root / "artist.yaml").read_text() + "\n# changed\n")
    elif tamper == "reference":
        next((root / "bible").glob("*.png")).write_bytes(b"changed")
    else:
        (root / "untracked.png").write_bytes(b"added")
    with pytest.raises(ShotError) as error:
        load_project_artist(project, "lead")
    assert error.value.status_code == 409


def test_private_artist_edits_and_explicit_fork_do_not_change_other_projects_or_library(assets, tmp_path):
    from lib.project_assets import publish_asset, import_asset, fork_asset, load_project_artist
    source, project, library = assets
    publish_asset(source, "performer", "v1", library=library)
    import_asset(project, "performer", "v1", "lead", library=library)
    other = tmp_path / "other"
    other.mkdir()
    import_asset(other, "performer", "v1", "lead", library=library)
    fork = fork_asset(project, "lead", "local-look")
    assert fork["mode"] == "private" and fork["source_lineage"]["alias"] == "lead"
    path = project / "assets" / "artists" / "local-look" / "artist.yaml"
    profile = yaml.safe_load(path.read_text())
    original = profile["identity"]["looks"][0]["wardrobe"]
    profile["identity"]["looks"][0]["wardrobe"] = "Project A yellow coat"
    path.write_text(yaml.safe_dump(profile))
    assert load_project_artist(project, "local-look")["identity"]["looks"][0]["wardrobe"] == "Project A yellow coat"
    assert load_project_artist(other, "lead")["identity"]["looks"][0]["wardrobe"] == original
    assert load_project_artist(project, "lead")["identity"]["looks"][0]["wardrobe"] == original
    assert publish_asset(source, "performer", "v1", library=library)["files"]
    with pytest.raises(ShotError):
        fork_asset(project, "lead", "local-look")


def test_generation_ledger_is_local_and_not_part_of_a_shared_version(assets):
    from lib.project_assets import publish_asset, import_asset, record_project_artist_ledger, load_project_artist
    source, project, library = assets
    (source / "ledger.jsonl").write_text('{"source":"legacy work"}\n')
    manifest = publish_asset(source, "performer", "v1", library=library)
    assert "ledger.jsonl" not in {file["path"] for file in manifest["files"]}
    import_asset(project, "performer", "v1", "lead", library=library)
    record_project_artist_ledger(project, "lead", {"asset": "assets/images/new.png", "tool": "mock",
        "human_input": {"source": "test"}})
    assert (project / "assets" / "artists" / "lead" / "ledger.jsonl").is_file()
    assert (source / "ledger.jsonl").read_text() == '{"source":"legacy work"}\n'
    assert load_project_artist(project, "lead")["id"] == "lead"
    assert not list((library / "versions").rglob("ledger.jsonl"))


@pytest.mark.parametrize("field,value", [("asset_id", "../outside"), ("version", "../v1"), ("kind", "../artist"),
                                         ("alias", "../lead"), ("alias", "Lead")])
def test_binding_identifiers_cannot_escape_or_confuse_artist_aliases(assets, field, value):
    from lib.project_assets import publish_asset, import_asset
    source, project, library = assets
    publish_asset(source, "performer", "v1", library=library)
    arguments = {"asset_id": "performer", "version": "v1", "kind": "artist", "alias": "lead"}
    arguments[field] = value
    with pytest.raises(ShotError):
        import_asset(project, library=library, **arguments)
    assert not (project / "assets" / "bindings.json").exists()


@pytest.mark.parametrize("target", ["source", "version", "snapshot"])
def test_asset_symlinks_are_rejected_even_when_they_stay_inside_root(assets, target):
    from lib.project_assets import publish_asset, import_asset, load_project_artist
    source, project, library = assets
    if target == "source":
        (source / "linked.yaml").symlink_to(source / "artist.yaml")
        with pytest.raises(ShotError):
            publish_asset(source, "performer", "v1", library=library)
        return
    publish_asset(source, "performer", "v1", library=library)
    if target == "version":
        root = library / "versions" / "artist" / "performer" / "v1" / "files"
        original = root / "artist.yaml"
        original.rename(root / "actual.yaml")
        original.symlink_to(root / "actual.yaml")
        with pytest.raises(ShotError):
            import_asset(project, "performer", "v1", "lead", library=library)
    else:
        import_asset(project, "performer", "v1", "lead", library=library)
        root = project / "assets" / "artists" / "lead"
        original = root / "artist.yaml"
        original.rename(root / "actual.yaml")
        original.symlink_to(root / "actual.yaml")
        with pytest.raises(ShotError):
            load_project_artist(project, "lead")


def test_generic_non_artist_assets_use_the_same_explicit_file_protocol(tmp_path):
    from lib.project_assets import publish_asset, import_asset
    source = tmp_path / "environment"
    source.mkdir()
    (source / "layout.json").write_text('{"name":"Synthetic courtyard"}')
    project = tmp_path / "film"
    project.mkdir()
    library = tmp_path / "library"
    manifest = publish_asset(source, "courtyard", "v1", kind="environment", library=library)
    binding = import_asset(project, "courtyard", "v1", "courtyard", kind="environment", library=library)
    assert manifest["kind"] == binding["kind"] == "environment"
    assert (project / binding["snapshot_path"] / "layout.json").read_text() == (source / "layout.json").read_text()


def test_explicit_library_cli_publish_import_fork_and_list(assets, capsys):
    from lib.project_assets import main
    source, project, library = assets
    assert main(["publish", str(source), "performer", "v1", "--library", str(library)]) == 0
    assert main(["import", str(project), "performer", "v1", "lead", "--library", str(library)]) == 0
    assert main(["fork", str(project), "lead", "local-look"]) == 0
    assert main(["list", str(project)]) == 0
    assert '"alias": "local-look"' in capsys.readouterr().out


@pytest.mark.parametrize("section", ["bible", "voice"])
def test_artist_media_sources_must_be_packaged_relative_files(assets, section):
    from lib.project_assets import publish_asset
    source, project, library = assets
    path = source / "artist.yaml"
    profile = yaml.safe_load(path.read_text())
    if section == "bible":
        profile["identity"]["bible"][0]["path"] = "/outside/portrait.png"
    else:
        profile["voice"]["canonical_tracks"] = [{"path": "/outside/voice.wav"}]
    path.write_text(yaml.safe_dump(profile))
    with pytest.raises(ShotError):
        publish_asset(source, "performer", "v1", library=library)


@pytest.mark.parametrize("operation", ["import", "load", "fork"])
def test_immutable_bytes_are_rechecked_at_the_capture_consumed_by_the_operation(assets, monkeypatch, operation):
    from lib import project_assets
    source, project, library = assets
    project_assets.publish_asset(source, "performer", "v1", library=library)
    if operation == "import":
        directory = library / "versions" / "artist" / "performer" / "v1" / "files"
    else:
        project_assets.import_asset(project, "performer", "v1", "lead", library=library)
        directory = project / "assets" / "artists" / "lead"
    capture = project_assets._capture
    calls = []
    def replace_after_first_verification(root):
        if root == directory:
            calls.append(root)
            if len(calls) == 2:
                profile = yaml.safe_load((root / "artist.yaml").read_text())
                profile["identity"]["looks"][0]["wardrobe"] = "Unapproved replacement coat"
                (root / "artist.yaml").write_text(yaml.safe_dump(profile))
        return capture(root)
    monkeypatch.setattr(project_assets, "_capture", replace_after_first_verification)
    with pytest.raises(ShotError) as error:
        if operation == "import":
            project_assets.import_asset(project, "performer", "v1", "lead", library=library)
        elif operation == "load":
            project_assets.load_project_artist(project, "lead")
        else:
            project_assets.fork_asset(project, "lead", "private-look")
    assert error.value.status_code == 409
    if operation == "import":
        assert not (project / "assets" / "artists" / "lead").exists()


@pytest.mark.parametrize("field,value", [("sha256", "invalid"), ("version", "v2"),
    ("source_files", [{"path": "/outside/profile.yaml", "sha256": "0" * 64, "size_bytes": 1}])])
def test_binding_provenance_cannot_claim_a_different_or_malformed_source_version(assets, field, value):
    from lib.project_assets import publish_asset, import_asset, load_project_artist
    source, project, library = assets
    publish_asset(source, "performer", "v1", library=library)
    import_asset(project, "performer", "v1", "lead", library=library)
    path = project / "assets" / "bindings.json"
    data = json.loads(path.read_text())
    data["bindings"][0][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ShotError):
        load_project_artist(project, "lead")
