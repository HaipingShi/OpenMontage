"""Regression tests for voice evidence, reference roles, and safe persistence."""

import copy
import hashlib
import shutil
import wave
from pathlib import Path

import pytest

from lib import artist_profile as ap
from schemas.artifacts import validate_artifact
from tools.audio.suno_music import SunoMusic
from tests.fixtures.artist_factory import write_artist, write_library


@pytest.fixture(autouse=True)
def library(tmp_path, monkeypatch):
    root = write_library(tmp_path / "artists")
    monkeypatch.setattr(ap, "ARTISTS_DIR", root)
    return root


def test_pending_voice_never_reports_ready(library):
    p = ap.load_artist("alpha", library)
    assert ap.check_artist(p)
    assert not ap.voice_readiness(p)["local_reference_ready"]
    assert ap.check_artist(p, "suno_web")
    assert ap.check_artist(p, "visual_only") == []


def test_confirmed_web_binding_is_not_local_or_api_readiness(library):
    ap.bind_suno_voice("alpha", kind="voice", url="https://suno.com/voice/example", confirmed=True, root=library)
    p = ap.load_artist("alpha", library)
    assert p["voice"]["lock_status"] == "provider_bound"
    assert ap.check_artist(p, "suno_web") == []
    assert ap.check_artist(p)
    assert ap.check_artist(p, "suno_music")
    assert p["voice"]["persona_id"] is None
    assert ap.voice_readiness(p)["generation_routes"]["suno_music"] == "unsupported_voice_reference"


def test_name_binding_requires_confirmation(library):
    ap.bind_suno_voice("alpha", kind="persona", name="Saved Alpha", root=library)
    assert ap.check_artist(ap.load_artist("alpha", library), "suno_web")
    ap.bind_suno_voice("alpha", kind="persona", name="Saved Alpha", confirmed=True, root=library)
    assert ap.check_artist(ap.load_artist("alpha", library), "suno_web") == []


@pytest.mark.parametrize("artist_id", ["../outside", "/tmp/outside", "alpha/beta", "ALPHA", "..", "alpha\\beta"])
def test_writes_reject_unsafe_artist_ids(library, artist_id):
    with pytest.raises(ValueError):
        ap.record_ledger(artist_id, {"asset": "x", "tool": "test", "human_input": "test"}, library)
    with pytest.raises(ValueError):
        ap.record_release(artist_id, {"title": "x", "project_id": "x"}, library)
    assert not (library.parent / "outside").exists()


def test_symlinked_artist_and_log_cannot_escape(library, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ap.artist_dir("escape", library)
    log = library / "alpha" / "releases.jsonl"
    log.symlink_to(outside / "releases.jsonl")
    with pytest.raises(ValueError):
        ap.record_release("alpha", {"title": "x", "project_id": "x"}, library)
    assert not (outside / "releases.jsonl").exists()


def test_profile_asset_traversal_and_reference_symlink_rejected(library, tmp_path):
    p = ap.load_artist("alpha", library)
    p["identity"]["bible"][0]["path"] = "../beta/bible/beta_main_front.png"
    assert any("unsafe artist asset path" in issue for issue in ap.check_artist(p, "visual_only"))
    with pytest.raises(ValueError):
        ap.identity_prompt(p, "x")
    p = ap.load_artist("alpha", library)
    image = Path(p["_dir"]) / p["identity"]["bible"][0]["path"]
    image.unlink()
    image.symlink_to(tmp_path / "outside.png")
    with pytest.raises(ValueError):
        ap.identity_prompt(p, "x")


def test_supporting_turnaround_never_becomes_a_face_reference():
    p = ap.load_artist("beta")
    result = ap.identity_prompt(p, "close-up")
    assert [Path(ref).name for ref in result["reference_images"]] == ["beta_main_front.png"]
    assert result["supporting_references"][0]["reference_role"] == "wardrobe_proportions"
    # Even accidentally marking a supporting reference canonical cannot mix it into faces.
    for entry in p["identity"]["bible"]:
        if entry.get("reference_role") == "wardrobe_proportions":
            entry["canonical"] = True
    assert len(ap.identity_prompt(p, "x")["reference_images"]) == 1
    group = ap.cast_prompt([ap.load_artist("alpha"), p], "two singers")
    assert len(group["reference_map"]["beta"]) == 1


def test_cast_accepts_schema_valid_stage_name_without_latin():
    p = copy.deepcopy(ap.load_artist("alpha"))
    p["names"].pop("latin")
    raw = {k: v for k, v in p.items() if k != "_dir"}
    validate_artifact("artist_profile", raw)
    assert f"{p['names']['stage']}:" in ap.cast_prompt([p, ap.load_artist("beta")], "duet")["prompt"]


def test_release_mapping_and_snapshot(library):
    with pytest.raises(ValueError, match="mapping"):
        ap.record_release("alpha", {"title", "project_id"}, library)
    record = ap.record_release("alpha", {"title": "Duet", "project_id": "mv-duet", "with": ["beta"]}, library)
    assert record["identity_version"] == ap.load_artist("alpha", library)["version"]
    assert ap.read_releases("alpha", library)[-1] == record


def test_import_audio_is_verified_copied_and_hash_checked(library, tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe unavailable")
    audio = tmp_path / "reference.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 8000)
    with pytest.raises(ValueError, match="approval"):
        ap.import_voice_reference("alpha", audio, root=library)
    track = ap.import_voice_reference("alpha", audio, approved=True, notes="selected take", root=library)
    p = ap.load_artist("alpha", library)
    saved = library / "alpha" / track["path"]
    assert saved.read_bytes() == audio.read_bytes()
    assert track["sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert track["duration_seconds"] == 1
    assert p["voice"]["lock_status"] == "locked"
    assert ap.check_artist(p) == []
    ap.import_voice_reference("alpha", audio, approved=True, root=library)
    assert len(ap.load_artist("alpha", library)["voice"]["canonical_tracks"]) == 1
    saved.write_bytes(b"changed")
    assert any("checksum mismatch" in issue for issue in ap.check_artist(ap.load_artist("alpha", library)))


def test_invalid_audio_cannot_lock_voice(library, tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe unavailable")
    fake = tmp_path / "fake.mp3"
    fake.write_text("not audio")
    with pytest.raises(ValueError, match="decodable"):
        ap.import_voice_reference("alpha", fake, approved=True, root=library)
    assert ap.load_artist("alpha", library)["voice"]["lock_status"] == "pending_audition"
    assert not list((library / "alpha" / "voice").iterdir())


@pytest.fixture
def imported_artist(tmp_path):
    from lib.project_assets import publish_asset, import_asset
    source = tmp_path / "source"
    write_artist(source, "solo")
    project = tmp_path / "film"
    project.mkdir()
    library = tmp_path / "versions"
    publish_asset(source / "solo", "performer", "v1", library=library)
    import_asset(project, "performer", "v1", "lead", library=library)
    return project, project / "assets/artists"


@pytest.mark.parametrize("entrypoint", ["bind_api", "bind_cli_root", "audio_api", "audio_cli_root"])
def test_all_voice_mutations_reject_imported_snapshots_before_copy(imported_artist, tmp_path, monkeypatch, entrypoint):
    from lib.project_assets import load_project_artist
    project, root = imported_artist
    before = (root / "lead/artist.yaml").read_bytes()
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"never opened for an immutable imported artist")
    monkeypatch.setattr(ap.shutil, "copyfile", lambda *args: pytest.fail("Immutable artist audio must be rejected before copying"))
    with pytest.raises(ValueError, match="fork"):
        if entrypoint == "bind_api":
            ap.bind_suno_voice("lead", kind="voice", name="Selected take", confirmed=True, root=root)
        elif entrypoint == "bind_cli_root":
            ap._main(["--root", str(root), "bind-suno", "lead", "--kind", "voice", "--name", "Selected take", "--confirm"])
        elif entrypoint == "audio_api":
            ap.import_voice_reference("lead", audio, approved=True, root=root)
        else:
            ap._main(["--root", str(root), "import-voice", "lead", str(audio), "--approve"])
    assert (root / "lead/artist.yaml").read_bytes() == before
    assert not (root / "lead/ledger.jsonl").exists()
    assert not (root / "lead/voice").exists()
    assert load_project_artist(project, "lead")["voice"]["lock_status"] == "pending_audition"


def test_direct_api_private_fork_voice_edits_preserve_import_and_usage_logs(imported_artist):
    from lib.project_assets import fork_asset, load_project_artist
    project, root = imported_artist
    imported_bytes = (root / "lead/artist.yaml").read_bytes()
    fork_asset(project, "lead", "private")
    ap.bind_suno_voice("private", kind="voice", name="Private take", confirmed=True, root=root)
    assert load_project_artist(project, "private")["voice"]["provider_binding"]["name"] == "Private take"
    ap.record_ledger("lead", {"asset": "shot", "tool": "fixture", "human_input": "Fixture usage"}, root=root)
    ap.record_release("lead", {"title": "Fixture", "project_id": "film"}, root=root)
    assert (root / "lead/artist.yaml").read_bytes() == imported_bytes
    assert load_project_artist(project, "lead")["id"] == "lead"


@pytest.mark.parametrize("key", ["persona_id", "voice_id", "reference_audio", "reference_audio_path", "audio_url"])
def test_suno_rejects_reference_inputs_before_network(monkeypatch, key):
    tool = SunoMusic()
    monkeypatch.setattr(tool, "_get_api_key", lambda: pytest.fail("must fail before accessing credentials"))
    result = tool.execute({"prompt": "test", key: "reference"})
    assert not result.success
    assert "no request was sent" in result.error
    with pytest.raises(ValueError, match="does not support"):
        tool._submit({"prompt": "test", key: "reference"}, "dummy")
