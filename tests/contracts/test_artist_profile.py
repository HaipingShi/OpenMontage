"""Contracts for persistent digital artists (artists/<id>/ + lib.artist_profile)."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from lib import artist_profile as ap
from schemas.artifacts import ARTIFACT_NAMES, validate_artifact

REPO_ROOT = Path(__file__).resolve().parents[2]


def _raw(artist_id: str) -> dict:
    with open(REPO_ROOT / "artists" / artist_id / "artist.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_schema_registered():
    assert "artist_profile" in ARTIFACT_NAMES


def test_every_shipped_artist_validates_and_is_listed():
    artists = ap.list_artists()
    assert "wanyu" in artists
    assert "_template" not in artists
    for artist_id in artists:
        profile = ap.load_artist(artist_id)
        assert profile["id"] == artist_id


def test_template_validates_against_schema():
    validate_artifact("artist_profile", _raw("_template"))


def test_wanyu_bible_files_exist_and_voice_pending():
    profile = ap.load_artist("wanyu")
    assert any("voice local reference not ready" in issue for issue in ap.check_artist(profile))
    assert ap.check_artist(profile, voice_route="visual_only") == []
    assert profile["voice"]["lock_status"] == "pending_audition"
    assert profile["status"] == "draft"


def test_only_canonical_images_become_references():
    profile = ap.load_artist("wanyu")
    refs = ap.identity_prompt(profile, "medium shot on a pier at night")["reference_images"]
    names = [Path(p).name for p in refs]
    assert names[0] == "wanyu_harbor_front.png"
    assert "wanyu_harbor_expressions.png" not in names
    assert "keyvisual_harbor_night.png" not in names
    assert all(Path(p).is_file() for p in refs)


def test_identity_prompt_is_built_from_canon():
    profile = ap.load_artist("wanyu")
    result = ap.identity_prompt(profile, "close-up singing, eyes closed", look="harbor")
    assert result["prompt"].startswith(profile["identity"]["prompt_core"].strip())
    assert "chore jacket" in result["prompt"]
    assert "close-up singing" in result["prompt"]
    assert result["locked_traits"] == profile["identity"]["locked_traits"]
    with pytest.raises(KeyError):
        ap.identity_prompt(profile, "x", look="does-not-exist")


def test_music_style_keeps_signature_and_cap():
    profile = ap.load_artist("wanyu")
    signature = profile["voice"]["style_signature"].strip()
    assert ap.music_style(profile) == signature
    style = ap.music_style(profile, "rainy bridge, 84 BPM, " + "x" * 400)
    assert style.startswith(signature)
    assert len(style) <= ap.STYLE_LIMIT


def test_schema_rejects_overlong_style_and_bad_id():
    raw = _raw("wanyu")
    too_long = copy.deepcopy(raw)
    too_long["voice"]["style_signature"] = "a" * 201
    with pytest.raises(Exception):
        validate_artifact("artist_profile", too_long)
    bad_id = copy.deepcopy(raw)
    bad_id["id"] = "Wan Yu"
    with pytest.raises(Exception):
        validate_artifact("artist_profile", bad_id)


def test_check_flags_locked_voice_without_track_and_unlicensed_clone(tmp_path):
    shutil.copytree(REPO_ROOT / "artists" / "wanyu", tmp_path / "wanyu")
    profile = ap.load_artist("wanyu", root=tmp_path)
    profile["voice"]["lock_status"] = "locked"
    profile["voice"]["source"] = "licensed_clone"
    issues = ap.check_artist(profile)
    assert any("no canonical_tracks" in i for i in issues)
    assert any("license_holder" in i for i in issues)


def test_check_flags_missing_bible_image(tmp_path):
    shutil.copytree(REPO_ROOT / "artists" / "wanyu", tmp_path / "wanyu")
    (tmp_path / "wanyu" / "bible" / "wanyu_harbor_front.png").unlink()
    issues = ap.check_artist(ap.load_artist("wanyu", root=tmp_path))
    assert any("wanyu_harbor_front.png" in i for i in issues)


def test_ledger_and_releases_are_append_only(tmp_path):
    shutil.copytree(REPO_ROOT / "artists" / "wanyu", tmp_path / "wanyu")
    before = len(ap.read_ledger("wanyu", root=tmp_path))
    ap.record_ledger("wanyu", {"asset": "voice/canonical_01.mp3", "tool": "suno_music", "human_input": "picked take 2"}, root=tmp_path)
    assert len(ap.read_ledger("wanyu", root=tmp_path)) == before + 1
    ap.record_release("wanyu", {"title": "第十四号码头", "project_id": "mv-001"}, root=tmp_path)
    releases = ap.read_releases("wanyu", root=tmp_path)
    assert releases[-1]["title"] == "第十四号码头" and "date" in releases[-1]
    with pytest.raises(ValueError):
        ap.record_ledger("wanyu", {"asset": "x"}, root=tmp_path)


def test_shipped_ledger_is_valid_jsonl():
    for entry in ap.read_ledger("wanyu"):
        for key in ap.LEDGER_REQUIRED:
            assert entry.get(key), entry
    assert json.dumps(ap.read_ledger("wanyu"), ensure_ascii=False)


@pytest.mark.parametrize("artist_id", ["amor", "fati"])
def test_amor_fati_profiles_preserve_current_identity_review_state(artist_id):
    profile = ap.load_artist(artist_id)
    assert any("voice local reference not ready" in issue for issue in ap.check_artist(profile))
    visual_issues = ap.check_artist(profile, voice_route="visual_only")
    if artist_id == "amor":
        assert visual_issues == ["look 'road' has no canonical bible image"]
        assert profile["identity"]["visual_lock_status"] == "pending_v2_reference_review"
    else:
        assert visual_issues == []
        assert profile["identity"]["visual_lock_status"] == "v3_user_approved"
    assert profile["voice"]["lock_status"] == "provider_bound"
    assert ap.check_artist(profile, voice_route="suno_web") == visual_issues
    assert profile["voice"]["provider_binding"]["confirmed"]
    refs = [Path(p).name for p in ap.identity_prompt(profile, "walking at dusk")["reference_images"]]
    assert refs == ([] if artist_id == "amor" else ["fati_road_front_v3-no-nose-ring.png"])
    assert "keyvisual_rooftop_dusk.png" not in refs


def test_permanent_fati_profile_has_no_song_test_dependencies():
    profile = ap.load_artist("fati")
    assert [look["id"] for look in profile["identity"]["looks"]] == ["road"]
    assert all("zhenjiao" not in entry["path"] for entry in profile["identity"]["bible"])


def test_amor_and_fati_reference_each_other():
    amor, fati = ap.load_artist("amor"), ap.load_artist("fati")
    assert [c["artist_id"] for c in amor["collaborators"]] == ["fati"]
    assert [c["artist_id"] for c in fati["collaborators"]] == ["amor"]
    assert amor["collaborators"][0]["duet_style"] == fati["collaborators"][0]["duet_style"]


def test_cast_prompt_keeps_people_separate():
    amor, fati = ap.load_artist("amor"), ap.load_artist("fati")
    result = ap.cast_prompt([amor, fati], "both laughing on a rooftop at dusk")
    assert "AMOR:" in result["prompt"] and "FATI:" in result["prompt"]
    assert "both laughing on a rooftop" in result["prompt"]
    assert set(result["reference_map"]) == {"amor", "fati"}
    assert all(Path(p).name.startswith("amor_") for p in result["reference_map"]["amor"])
    assert all(Path(p).name.startswith("fati_") for p in result["reference_map"]["fati"])
    assert result["locked_traits"]["fati"] == fati["identity"]["locked_traits"]
    assert "merged faces" in result["negative_prompt"]
    with pytest.raises(ValueError):
        ap.cast_prompt([amor], "x")


def test_duet_style_fits_and_contains_both_voices():
    amor, fati = ap.load_artist("amor"), ap.load_artist("fati")
    style = ap.duet_style([amor, fati], "sunset tempo 112 BPM, " + "x" * 300)
    assert len(style) <= ap.STYLE_LIMIT
    assert amor["voice"]["duet_descriptor"] in style
    assert fati["voice"]["duet_descriptor"] in style
    assert style.startswith(amor["collaborators"][0]["duet_style"])
    with pytest.raises(ValueError):
        ap.duet_style([amor, ap.load_artist("wanyu")])


def test_check_flags_missing_collaborator(tmp_path):
    shutil.copytree(REPO_ROOT / "artists" / "amor", tmp_path / "amor")
    issues = ap.check_artist(ap.load_artist("amor", root=tmp_path))
    assert any("collaborator 'fati'" in i for i in issues)


def test_pipeline_skills_reference_artist_profile():
    stage_dir = REPO_ROOT / "skills" / "pipelines" / "music-video"
    for name in ("idea-director.md", "asset-director.md", "song-analysis-director.md", "publish-director.md"):
        assert "artist_profile" in (stage_dir / name).read_text(encoding="utf-8"), name
    assert (REPO_ROOT / "skills" / "meta" / "digital-artist.md").is_file()
