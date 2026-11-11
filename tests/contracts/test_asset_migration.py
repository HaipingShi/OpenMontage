"""Copy-only asset migration contracts using synthetic, temporary files."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from lib import asset_migration as migration
from tests.fixtures.artist_factory import write_artist


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def legacy(tmp_path):
    source = tmp_path / "legacy"
    write_artist(source / "artists", "solo")
    (source / "artists/solo/ledger.jsonl").write_text('{"tool":"old-local","asset":"bible/solo_main_front.png","human_input":"synthetic"}\n')
    (source / "artists/_template").mkdir()
    (source / "artists/_template/artist.yaml").write_text("template: true\n")
    for name in ("first-film", "second-film"):
        project = source / "projects" / name
        (project / "artifacts").mkdir(parents=True)
        (project / "assets/images").mkdir(parents=True)
        (project / "assets/images/local.png").write_bytes(b"local test image")
        (project / "project.json").write_text(json.dumps({"project_id": name, "title": "Synthetic title"}))
        (project / "artifacts/scene_plan.json").write_text(json.dumps({"version": "1.0", "scenes": [{"id": "s01",
            "description": "Keep this creative sentence unchanged.", "generation_context": {"artist_ids": ["solo"]},
            "character_actions": [{"character_id": "solo", "action_sequence": ["standing"]},
                                  {"character_id": "extra-person", "action_sequence": ["walking"]}],
            "reference_images": [str(source / "artists/solo/bible/solo_main_front.png"),
                                 f"projects/{name}/assets/images/local.png"]}]}))
    return source, tmp_path / "user-data"


def test_dry_run_inventory_hashes_all_files_and_copies_nothing(legacy):
    source, data = legacy
    report = migration.migrate(source, data)
    assert report["status"] == "planned"
    assert report["applied"] is False
    assert report["inventory_sha256"]
    assert all(len(item["source_sha256"]) == 64 for item in report["inventory"])
    assert not (data / "projects").exists()
    assert not (data / "library/artists").exists()
    assert Path(report["report_path"]).is_file()
    assert not any("_template" in item["source_path"] for item in report["inventory"])
    assert "Keep this creative sentence" not in Path(report["report_path"]).read_text()


def test_apply_copies_verifies_binds_and_keeps_source_unchanged(legacy):
    source, data = legacy
    original = {path.relative_to(source).as_posix(): digest(path) for path in source.rglob("*") if path.is_file()}
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    assert report["verified_files"] == len(report["inventory"])
    assert original == {path.relative_to(source).as_posix(): digest(path) for path in source.rglob("*") if path.is_file()}
    assert (data / "library/artists/solo/ledger.jsonl").read_bytes() == (source / "artists/solo/ledger.jsonl").read_bytes()
    first = data / "projects/first-film"
    scene = json.loads((first / "artifacts/scene_plan.json").read_text())["scenes"][0]
    assert scene["reference_images"] == ["assets/artists/solo/bible/solo_main_front.png", "assets/images/local.png"]
    assert scene["description"] == "Keep this creative sentence unchanged."
    bindings = json.loads((first / "assets/bindings.json").read_text())["bindings"]
    assert bindings[0]["alias"] == "solo"
    assert bindings[0]["version"].startswith("sha256-")
    profile = yaml.safe_load((first / "assets/artists/solo/artist.yaml").read_text())
    assert profile["identity"]["bible"][0]["canonical"] is True
    assert profile["identity"]["bible"][1]["canonical"] is False
    assert profile["identity"]["bible"][1]["review_status"] == "fixture_candidate"


def test_retry_checks_transformed_destinations_and_is_idempotent(legacy):
    source, data = legacy
    first = migration.migrate(source, data, apply=True)
    second = migration.migrate(source, data, apply=True)
    assert second["status"] == "completed"
    assert second["copied_files"] == 0
    assert second["inventory_sha256"] == first["inventory_sha256"]
    assert second["bindings"] == first["bindings"]


def test_destination_conflict_is_checked_before_any_asset_copy(legacy):
    source, data = legacy
    conflict = data / "projects/second-film/project.json"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("private destination content")
    with pytest.raises(migration.MigrationError, match="DESTINATION_CONFLICT") as error:
        migration.migrate(source, data, apply=True)
    assert conflict.read_text() == "private destination content"
    assert not (data / "library/artists").exists()
    assert error.value.report["status"] == "failed"
    assert Path(error.value.report["report_path"]).is_file()


def test_source_change_after_inventory_fails_before_copy(legacy):
    source, data = legacy
    plan = migration.inventory(source, data)
    (source / "projects/first-film/assets/images/local.png").write_bytes(b"changed after inventory")
    with pytest.raises(migration.MigrationError, match="SOURCE_CHANGED"):
        migration.apply_plan(plan)
    assert not (data / "projects").exists()


@pytest.mark.parametrize("location", ["source-file", "source-directory", "destination-parent"])
def test_symlinks_are_rejected_without_following_them(legacy, tmp_path, location):
    source, data = legacy
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not a migration asset")
    if location == "source-file":
        (source / "projects/first-film/escape.txt").symlink_to(outside / "secret.txt")
    elif location == "source-directory":
        (source / "projects/first-film/escape").symlink_to(outside, target_is_directory=True)
    else:
        data.mkdir()
        (data / "projects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(migration.MigrationError, match="SYMLINK"):
        migration.migrate(source, data, apply=True)
    assert (outside / "secret.txt").read_text() == "not a migration asset"


@pytest.mark.parametrize("reference", ["../../outside.png", "/outside/not-allowed.png", "projects/another-film/assets/image.png", "artists/missing/bible/front.png"])
def test_unmappable_legacy_references_fail_explicitly(legacy, reference):
    source, data = legacy
    path = source / "projects/first-film/artifacts/scene_plan.json"
    document = json.loads(path.read_text())
    document["scenes"][0]["reference_images"] = [reference]
    path.write_text(json.dumps(document))
    with pytest.raises(migration.MigrationError, match="REFERENCE"):
        migration.migrate(source, data, apply=True)
    assert not (data / "projects").exists()
    assert json.loads(path.read_text())["scenes"][0]["reference_images"] == [reference]


def test_unknown_explicit_artist_binding_fails_without_guessing(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/scene_plan.json"
    document = json.loads(path.read_text())
    document["scenes"][0]["generation_context"]["artist_ids"] = ["missing"]
    path.write_text(json.dumps(document))
    with pytest.raises(migration.MigrationError, match="UNKNOWN_ARTIST"):
        migration.migrate(source, data, apply=True)


def test_absolute_artist_profile_paths_are_explicitly_normalised(legacy):
    source, data = legacy
    path = source / "artists/solo/artist.yaml"
    profile = yaml.safe_load(path.read_text())
    profile["identity"]["bible"][0]["path"] = str(source / "artists/solo/bible/solo_main_front.png")
    path.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False))
    report = migration.migrate(source, data, apply=True)
    copied = yaml.safe_load((data / "library/artists/solo/artist.yaml").read_text())
    assert copied["identity"]["bible"][0]["path"] == "bible/solo_main_front.png"
    assert report["rewrites"]
    assert yaml.safe_load(path.read_text())["identity"]["bible"][0]["path"].startswith(str(source))


def test_two_project_ledgers_are_independent_of_source_and_library(legacy):
    from lib.project_assets import record_project_artist_ledger
    source, data = legacy
    migration.migrate(source, data, apply=True)
    first, second = [data / "projects" / name for name in ("first-film", "second-film")]
    before = (data / "library/artists/solo/ledger.jsonl").read_bytes()
    record_project_artist_ledger(first, "solo", {"asset": "candidate.png", "tool": "mock", "human_input": "local selection"})
    assert (first / "assets/artists/solo/ledger.jsonl").exists()
    assert not (second / "assets/artists/solo/ledger.jsonl").exists()
    assert (data / "library/artists/solo/ledger.jsonl").read_bytes() == before
    assert (source / "artists/solo/ledger.jsonl").read_bytes() == before


def test_plan_tampering_and_overlapping_roots_are_rejected(legacy):
    source, data = legacy
    plan = migration.inventory(source, data)
    altered = copy.deepcopy(plan)
    altered["inventory"][0]["destination_path"] = "../escape"
    with pytest.raises(migration.MigrationError, match="PLAN_CHANGED"):
        migration.apply_plan(altered)
    with pytest.raises(migration.MigrationError, match="OVERLAPPING_ROOTS"):
        migration.inventory(source, source / "nested-data")


def test_cli_defaults_to_dry_run_and_returns_failure_for_conflict(legacy, capsys):
    source, data = legacy
    assert migration.main(["migrate", str(source), "--data-dir", str(data)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "planned"
    conflict = data / "library/artists/solo/artist.yaml"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("private")
    assert migration.main(["migrate", str(source), "--data-dir", str(data), "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_rejected_overlapping_roots_do_not_write_failure_report_into_source(legacy):
    source, _data = legacy
    nested = source / "do-not-create"
    with pytest.raises(migration.MigrationError, match="OVERLAPPING_ROOTS"):
        migration.migrate(source, nested, apply=True)
    assert not nested.exists()


def test_copy_verification_failure_never_installs_corrupt_final_file(legacy, monkeypatch):
    source, data = legacy
    original_read = migration._read

    def corrupt_verify(path, **kwargs):
        if Path(path).name.startswith(".migration-"):
            return "f" * 64, 0, None
        return original_read(path, **kwargs)

    monkeypatch.setattr(migration, "_read", corrupt_verify)
    with pytest.raises(migration.MigrationError, match="COPY_VERIFICATION") as error:
        migration.migrate(source, data, apply=True)
    assert error.value.report["copied_files"] == 0
    assert not list(data.rglob(".migration-*.tmp"))
    assert not (data / "library/artists/solo/artist.yaml").exists()


def test_source_change_during_copy_is_reported_and_not_installed(legacy, monkeypatch):
    source, data = legacy
    original_copy = migration._copy

    def changing_copy(entry, *args, **kwargs):
        (source / entry["source_path"]).write_bytes(b"changed before streamed copy")
        return original_copy(entry, *args, **kwargs)

    monkeypatch.setattr(migration, "_copy", changing_copy)
    with pytest.raises(migration.MigrationError, match="SOURCE_CHANGED"):
        migration.migrate(source, data, apply=True)
    assert not (data / "library/artists/solo/artist.yaml").exists()


def test_existing_private_alias_conflicts_before_copy(legacy):
    from lib.project_assets import fork_asset
    source, data = legacy
    migration.migrate(source, data, apply=True)
    first = data / "projects/first-film"
    fork_asset(first, "solo", "private-copy")
    bindings = json.loads((first / "assets/bindings.json").read_text())
    original = next(binding for binding in bindings["bindings"] if binding["alias"] == "solo")
    original["mode"] = "private"
    (first / "assets/bindings.json").write_text(json.dumps(bindings))
    with pytest.raises(migration.MigrationError, match="BINDING_CONFLICT") as error:
        migration.migrate(source, data, apply=True)
    assert error.value.report["copied_files"] == 0


@pytest.mark.parametrize("path_value", ["relative-source", "../source"])
def test_relative_and_traversal_roots_are_rejected(legacy, path_value):
    _source, data = legacy
    with pytest.raises(migration.MigrationError, match="UNSAFE_PATH"):
        migration.migrate(path_value, data)


def test_invalid_document_fails_without_printing_creative_content(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/scene_plan.json"
    path.write_text('{"description": "private creative contents", broken')
    with pytest.raises(migration.MigrationError, match="INVALID_DOCUMENT") as error:
        migration.migrate(source, data, apply=True)
    assert "private creative contents" not in Path(error.value.report["report_path"]).read_text()


def test_dangling_source_bucket_symlink_is_rejected(legacy):
    source, data = legacy
    unused = source / "projects"
    renamed = source / "backup-projects"
    unused.rename(renamed)
    unused.symlink_to(source / "missing-target")
    with pytest.raises(migration.MigrationError, match="SYMLINK"):
        migration.migrate(source, data, apply=True)


def test_legacy_paths_in_unstructured_text_are_flagged_without_rewriting_code(legacy):
    source, data = legacy
    composition = source / "projects/first-film/composition.tsx"
    original = f'const reference = "{source}/artists/solo/bible/solo_main_front.png";\n'
    composition.write_text(original)
    report = migration.migrate(source, data, apply=True)
    assert report["warnings"]
    assert composition.read_text() == original
    assert (data / "projects/first-film/composition.tsx").read_text() == original


def test_historical_generation_summary_and_external_source_provenance_stay_unchanged(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/history.json"
    document = {"generation_summary": f"Old attempt used {source}/artists/solo/bible/solo_main_front.png",
                "source_path": "/previous-installation/deleted/reference.png", "original_path": "../../old/history.png"}
    path.write_text(json.dumps(document))
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    assert len(report["warnings"]) >= 3
    assert json.loads((data / "projects/first-film/artifacts/history.json").read_text()) == document
    saved = Path(report["report_path"]).read_text()
    assert "Old attempt used" not in saved


@pytest.mark.parametrize("field", ["reference_images", "reference_image", "image_path", "video_path", "audio_path", "first_frame", "master_path"])
def test_missing_project_relative_media_inputs_fail_before_copy(legacy, field):
    source, data = legacy
    path = source / "projects/first-film/artifacts/scene_plan.json"
    document = json.loads(path.read_text())
    reference = "assets/images/missing.png"
    document["scenes"][0][field] = [reference] if field == "reference_images" else reference
    path.write_text(json.dumps(document))
    with pytest.raises(migration.MigrationError, match="DANGLING_REFERENCE"):
        migration.migrate(source, data, apply=True)
    assert not (data / "projects").exists()
    assert json.loads(path.read_text()) == document


def test_project_relative_inputs_are_checked_but_future_outputs_are_preserved(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/scene_plan.json"
    document = json.loads(path.read_text())
    document["scenes"][0]["reference_images"] = ["assets/images/local.png"]
    document["scenes"][0]["output_path"] = "assets/video/planned.mp4"
    path.write_text(json.dumps(document))
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    assert json.loads((data / "projects/first-film/artifacts/scene_plan.json").read_text()) == document


def test_embedded_profile_paths_use_an_explicit_artist_owner(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/look-profile.json"
    path.write_text(json.dumps({"artist_id": "solo", "bible": [{"path": "bible/solo_main_front.png"}]}))
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    copied = json.loads((data / "projects/first-film/artifacts/look-profile.json").read_text())
    assert copied["bible"][0]["path"] == "assets/artists/solo/bible/solo_main_front.png"


def test_embedded_profile_paths_do_not_guess_artist_from_filename(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/look-profile.json"
    path.write_text(json.dumps({"bible": [{"path": "bible/solo_main_front.png"}]}))
    with pytest.raises(migration.MigrationError, match="DANGLING_REFERENCE"):
        migration.migrate(source, data, apply=True)
    assert not (data / "projects").exists()


def test_embedded_profiles_keep_explicit_owners_for_same_named_files(legacy):
    source, data = legacy
    write_artist(source / "artists", "second")
    for artist in ("solo", "second"):
        (source / "artists" / artist / "bible/shared.png").write_bytes(artist.encode())
    path = source / "projects/first-film/artifacts/look-profile.json"
    path.write_text(json.dumps({"look": {"base_artist_id": "solo", "references": [{"path": "bible/shared.png"},
                    {"artist_id": "second", "path": "bible/shared.png"}]}}))
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    copied = json.loads((data / "projects/first-film/artifacts/look-profile.json").read_text())["look"]["references"]
    assert [entry["path"] for entry in copied] == ["assets/artists/solo/bible/shared.png", "assets/artists/second/bible/shared.png"]
    assert (data / "projects/first-film" / copied[0]["path"]).read_bytes() == b"solo"
    assert (data / "projects/first-film" / copied[1]["path"]).read_bytes() == b"second"


def test_unknown_nested_artist_owner_does_not_inherit_another_artist(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/look-profile.json"
    path.write_text(json.dumps({"artist_id": "solo", "child": {"artist_id": "missing", "path": "bible/solo_main_front.png"}}))
    with pytest.raises(migration.MigrationError, match="DANGLING_REFERENCE"):
        migration.migrate(source, data, apply=True)
    assert not (data / "projects").exists()


def test_archived_structured_inputs_keep_their_bytes_without_new_bindings(legacy):
    source, data = legacy
    path = source / "projects/first-film/history/old-snapshot.json"
    path.parent.mkdir()
    original = b'{"generation_context":{"artist_ids":["old-missing"]}, "reference_images":["assets/images/deleted.png"], "identity":{"bible":[{"path":"bible/old.png"}]}}\n'
    path.write_bytes(original)
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    assert (data / "projects/first-film/history/old-snapshot.json").read_bytes() == original
    assert path.read_bytes() == original
    assert all(item["asset_id"] != "old-missing" for item in report["bindings"])
    warnings = [item for item in report["warnings"] if item["file"].endswith("history/old-snapshot.json")]
    assert {item["pointer"] for item in warnings} == {"/reference_images/0", "/identity/bible/0/path"}
    assert all(item["code"] == "MANUAL_REVIEW_ARCHIVED_INPUT" for item in warnings)


def test_history_prefix_does_not_exempt_active_inputs(legacy):
    source, data = legacy
    path = source / "projects/first-film/history2/scene.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"reference_images": ["assets/images/deleted.png"]}))
    with pytest.raises(migration.MigrationError, match="DANGLING_REFERENCE"):
        migration.migrate(source, data, apply=True)
    assert not (data / "projects").exists()


def test_full_artist_profile_snapshot_has_an_explicit_identity_owner(legacy):
    source, data = legacy
    path = source / "projects/first-film/artifacts/artist-profile.json"
    profile = yaml.safe_load((source / "artists/solo/artist.yaml").read_text())
    (source / "artists/solo/promo").mkdir()
    (source / "artists/solo/promo/cover.png").write_bytes(b"cover")
    profile["identity"]["bible"].append({"path": "promo/cover.png"})
    path.write_text(json.dumps(profile))
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    copied = json.loads((data / "projects/first-film/artifacts/artist-profile.json").read_text())
    assert all(entry["path"].startswith("assets/artists/solo/") for entry in copied["identity"]["bible"])


def test_provider_graph_links_are_preserved_with_manual_review_warning(legacy):
    source, data = legacy
    path = source / "projects/first-film/integrations/workflow.json"
    path.parent.mkdir()
    original = b'{"7":{"class_type":"ProviderGenerator","inputs":{"first_frame":["20",0]}},"20":{"class_type":"LoadImage","inputs":{"image":"remote-input.png"}}}\n'
    path.write_bytes(original)
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    assert (data / "projects/first-film/integrations/workflow.json").read_bytes() == original
    warnings = [item for item in report["warnings"] if item["file"].endswith("integrations/workflow.json")]
    assert any(item["code"] == "MANUAL_REVIEW_PROVIDER_GRAPH" for item in warnings)


def test_provider_node_schema_is_not_interpreted_as_media_inputs(legacy):
    source, data = legacy
    path = source / "projects/first-film/integrations/object-info.json"
    path.parent.mkdir()
    original = b'{"ProviderGenerator":{"input":{"optional":{"first_frame":["IMAGE",{}]}},"output":["VIDEO"]}}\n'
    path.write_bytes(original)
    report = migration.migrate(source, data, apply=True)
    assert report["status"] == "completed"
    assert (data / "projects/first-film/integrations/object-info.json").read_bytes() == original
    assert any(item["code"] == "MANUAL_REVIEW_PROVIDER_METADATA" for item in report["warnings"])


@pytest.mark.parametrize("initialise_migration_library", [True, False])
def test_other_library_alias_conflicts_are_found_before_any_copy(legacy, initialise_migration_library):
    from lib.project_assets import publish_asset, import_asset
    source, data = legacy
    plan = migration.inventory(source, data)
    version = plan["artists"][0]["version"]
    outside = source.parent / "outside-library"
    publish_asset(source / "artists/solo", "solo", version, library=outside)
    project = data / "projects/first-film"
    project.mkdir(parents=True)
    import_asset(project, "solo", version, "solo", library=outside)
    if initialise_migration_library:
        publish_asset(source / "artists/solo", "solo", version, library=data / "library")
    with pytest.raises(migration.MigrationError, match="BINDING_CONFLICT") as error:
        migration.apply_plan(plan)
    assert error.value.report["copied_files"] == 0
    assert not (data / "projects/second-film").exists()
