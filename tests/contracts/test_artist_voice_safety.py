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


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "artists"
    for artist in ("amor", "fati"):
        shutil.copytree(ap.ARTISTS_DIR / artist, root / artist)
    # Fixture starts before any user-specific binding, independent of shipped status.
    p = ap.load_artist("amor", root)
    p["voice"].pop("provider_binding", None)
    p["voice"]["lock_status"] = "pending_audition"
    p["voice"]["canonical_tracks"] = []
    # Voice contracts use a reviewed identity fixture, independently of the
    # shipped Amor v2 candidate's pending human visual review.
    front = next(e for e in p["identity"]["bible"]
                 if e.get("identity_version") == 2 and e["view"] == "front")
    front["canonical"] = True
    front["reference_role"] = "identity"
    front["review_status"] = "fixture_approved"
    ap._save_artist(p)
    return root


def test_pending_voice_never_reports_ready(library):
    p = ap.load_artist("amor", library)
    assert ap.check_artist(p)
    assert not ap.voice_readiness(p)["local_reference_ready"]
    assert ap.check_artist(p, "suno_web")
    assert ap.check_artist(p, "visual_only") == []


def test_confirmed_web_binding_is_not_local_or_api_readiness(library):
    ap.bind_suno_voice("amor", kind="voice", url="https://suno.com/voice/example", confirmed=True, root=library)
    p = ap.load_artist("amor", library)
    assert p["voice"]["lock_status"] == "provider_bound"
    assert ap.check_artist(p, "suno_web") == []
    assert ap.check_artist(p)
    assert ap.check_artist(p, "suno_music")
    assert p["voice"]["persona_id"] is None
    assert ap.voice_readiness(p)["generation_routes"]["suno_music"] == "unsupported_voice_reference"


def test_name_binding_requires_confirmation(library):
    ap.bind_suno_voice("amor", kind="persona", name="Saved Amor", root=library)
    assert ap.check_artist(ap.load_artist("amor", library), "suno_web")
    ap.bind_suno_voice("amor", kind="persona", name="Saved Amor", confirmed=True, root=library)
    assert ap.check_artist(ap.load_artist("amor", library), "suno_web") == []


@pytest.mark.parametrize("artist_id", ["../outside", "/tmp/outside", "amor/fati", "AMOR", "..", "amor\\fati"])
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
    log = library / "amor" / "releases.jsonl"
    log.symlink_to(outside / "releases.jsonl")
    with pytest.raises(ValueError):
        ap.record_release("amor", {"title": "x", "project_id": "x"}, library)
    assert not (outside / "releases.jsonl").exists()


def test_profile_asset_traversal_and_reference_symlink_rejected(library, tmp_path):
    p = ap.load_artist("amor", library)
    p["identity"]["bible"][0]["path"] = "../fati/bible/fati_road_front.png"
    assert any("unsafe artist asset path" in issue for issue in ap.check_artist(p, "visual_only"))
    with pytest.raises(ValueError):
        ap.identity_prompt(p, "x")
    p = ap.load_artist("amor", library)
    image = Path(p["_dir"]) / p["identity"]["bible"][0]["path"]
    image.unlink()
    image.symlink_to(tmp_path / "outside.png")
    with pytest.raises(ValueError):
        ap.identity_prompt(p, "x")


def test_fati_supporting_turnaround_never_becomes_a_face_reference():
    p = ap.load_artist("fati")
    result = ap.identity_prompt(p, "close-up")
    assert [Path(ref).name for ref in result["reference_images"]] == ["fati_road_front_v3-no-nose-ring.png"]
    assert result["supporting_references"][0]["reference_role"] == "wardrobe_proportions"
    # Even accidentally marking a supporting reference canonical cannot mix it into faces.
    for entry in p["identity"]["bible"]:
        if entry.get("reference_role") == "wardrobe_proportions":
            entry["canonical"] = True
    assert len(ap.identity_prompt(p, "x")["reference_images"]) == 1
    group = ap.cast_prompt([ap.load_artist("amor"), p], "two singers")
    assert len(group["reference_map"]["fati"]) == 1


def test_cast_accepts_schema_valid_stage_name_without_latin():
    p = copy.deepcopy(ap.load_artist("amor"))
    p["names"].pop("latin")
    raw = {k: v for k, v in p.items() if k != "_dir"}
    validate_artifact("artist_profile", raw)
    assert f"{p['names']['stage']}:" in ap.cast_prompt([p, ap.load_artist("fati")], "duet")["prompt"]


def test_release_mapping_and_snapshot(library):
    with pytest.raises(ValueError, match="mapping"):
        ap.record_release("amor", {"title", "project_id"}, library)
    record = ap.record_release("amor", {"title": "Duet", "project_id": "mv-duet", "with": ["fati"]}, library)
    assert record["identity_version"] == ap.load_artist("amor", library)["version"]
    assert ap.read_releases("amor", library)[-1] == record


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
        ap.import_voice_reference("amor", audio, root=library)
    track = ap.import_voice_reference("amor", audio, approved=True, notes="selected take", root=library)
    p = ap.load_artist("amor", library)
    saved = library / "amor" / track["path"]
    assert saved.read_bytes() == audio.read_bytes()
    assert track["sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert track["duration_seconds"] == 1
    assert p["voice"]["lock_status"] == "locked"
    assert ap.check_artist(p) == []
    ap.import_voice_reference("amor", audio, approved=True, root=library)
    assert len(ap.load_artist("amor", library)["voice"]["canonical_tracks"]) == 1
    saved.write_bytes(b"changed")
    assert any("checksum mismatch" in issue for issue in ap.check_artist(ap.load_artist("amor", library)))


def test_invalid_audio_cannot_lock_voice(library, tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe unavailable")
    fake = tmp_path / "fake.mp3"
    fake.write_text("not audio")
    with pytest.raises(ValueError, match="decodable"):
        ap.import_voice_reference("amor", fake, approved=True, root=library)
    assert ap.load_artist("amor", library)["voice"]["lock_status"] == "pending_audition"
    assert not list((library / "amor" / "voice").iterdir())


@pytest.mark.parametrize("key", ["persona_id", "voice_id", "reference_audio", "reference_audio_path", "audio_url"])
def test_suno_rejects_reference_inputs_before_network(monkeypatch, key):
    tool = SunoMusic()
    monkeypatch.setattr(tool, "_get_api_key", lambda: pytest.fail("must fail before accessing credentials"))
    result = tool.execute({"prompt": "test", key: "reference"})
    assert not result.success
    assert "no request was sent" in result.error
    with pytest.raises(ValueError, match="does not support"):
        tool._submit({"prompt": "test", key: "reference"}, "dummy")
