"""Contracts for the music-video pipeline and its song_beatmap tool.

The derivation tests feed a synthetic audiomap (no audio, no librosa) through
`build_song_map`, so they run everywhere. The end-to-end test renders a click
track with ffmpeg and runs the real analyzer; it is skipped when ffmpeg or
librosa is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import re
from pathlib import Path

import pytest
import yaml

from lib.pipeline_loader import load_pipeline
from lib.checkpoint import CheckpointValidationError, get_next_stage, write_checkpoint
from schemas.artifacts import ARTIFACT_NAMES, validate_artifact
from tools.analysis.song_beatmap import SongBeatmap, build_song_map

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"


def _synthetic_audiomap(bpm: float = 120.0, bars: int = 48) -> dict:
    """Intro(8 bars LOW) verse(8 MED) pre(4 MED+) chorus(8 HIGH) verse(8 MED) chorus(8 HIGH) outro(4 LOW)."""
    beat = 60.0 / bpm
    beats = [round(i * beat, 3) for i in range(bars * 4)]
    downbeats = beats[::4]
    duration = bars * 4 * beat
    plan = [(8, 0.3), (8, 0.5), (4, 0.6), (8, 0.9), (8, 0.5), (8, 0.92), (4, 0.25)]
    phases, t = [], 0.0
    for n_bars, energy in plan:
        span = n_bars * 4 * beat
        level = "LOW" if energy < 0.4 else "MEDIUM" if energy < 0.65 else "HIGH"
        phases.append({"start": t, "end": t + span, "level": level, "energy": energy})
        t += span
    phrases = [
        {"index": i, "start": downbeats[b], "end": downbeats[b + 4] if b + 4 < len(downbeats) else duration, "bars": 4}
        for i, b in enumerate(range(0, len(downbeats), 4))
    ]
    return {
        "audio": {"duration_sec": duration},
        "tempo": {"bpm": bpm, "beats_per_bar": 4},
        "grid": {"beats_sec": beats, "downbeats_sec": downbeats},
        "energy_phases": phases,
        "phrases": phrases,
        "key_moments": [{"t": 40, "kind": "SURGE", "delta": 0.3}],
        "hard_stops": [],
        "silences": [],
        "stats": {"grid_counts": {"strong": 120, "weak": 60, "syncopated": 10, "off-grid": 5}},
    }


def test_song_map_is_a_registered_artifact() -> None:
    assert "song_map" in ARTIFACT_NAMES


def test_manifest_loads_and_declares_song_analysis() -> None:
    manifest = load_pipeline("music-video")
    names = [s["name"] for s in manifest["stages"]]
    assert names[:3] == ["idea", "song_analysis", "proposal"]
    song_stage = next(s for s in manifest["stages"] if s["name"] == "song_analysis")
    assert "song_beatmap" in song_stage["required_tools"]
    assert "song_map" in song_stage["produces"]


def test_every_stage_skill_exists() -> None:
    raw = yaml.safe_load((REPO_ROOT / "pipeline_defs" / "music-video.yaml").read_text(encoding="utf-8"))
    for ref in raw["required_skills"] + [s["skill"] for s in raw["stages"]]:
        assert (SKILLS_DIR / f"{ref}.md").is_file(), ref


def test_build_song_map_sections_and_grid() -> None:
    song_map = build_song_map(_synthetic_audiomap(), audio_path="master.wav")
    validate_artifact("song_map", song_map)

    sections = song_map["sections"]
    assert sections[0]["start_seconds"] == 0
    assert sections[-1]["end_seconds"] == pytest.approx(song_map["audio"]["duration_seconds"])
    for a, b in zip(sections, sections[1:]):
        assert a["end_seconds"] == pytest.approx(b["start_seconds"])

    labels = [s["label"] for s in sections]
    assert labels[0] == "intro"
    assert labels[-1] == "outro"
    assert labels.count("chorus") == 2
    assert song_map["tempo"]["rhythmic_confidence"] == "rhythmic"

    grid = set(song_map["grid"]["beats_sec"])
    for s in sections:
        assert s["pacing"] == "beat_cut"
        for t in s["cut_points_seconds"][1:]:
            assert t in grid
    chorus = next(s for s in sections if s["label"] == "chorus")
    verse = next(s for s in sections if s["label"] == "verse")
    assert chorus["cut_unit_beats"] < verse["cut_unit_beats"]


def test_cutdown_windows_start_on_a_downbeat() -> None:
    song_map = build_song_map(_synthetic_audiomap(), audio_path="master.wav")
    downbeats = set(song_map["grid"]["downbeats_sec"])
    assert {w["target_seconds"] for w in song_map["cutdown_windows"]} == {15, 30}
    for w in song_map["cutdown_windows"]:
        assert w["start_seconds"] in downbeats
        assert w["end_seconds"] in downbeats


def test_song_analysis_checkpoint_requires_map_and_approval(tmp_path: Path) -> None:
    from backlot.state import load_board_state
    from tests.contracts.test_phase0_contracts import sample_artifact

    project_id = "music-test"
    project_dir = tmp_path / project_id
    project_dir.mkdir()
    (project_dir / "project.json").write_text(json.dumps({
        "project_id": project_id, "title": "Music Test", "pipeline_type": "music-video",
    }), encoding="utf-8")
    write_checkpoint(tmp_path, project_id, "idea", "completed",
                     artifacts={"brief": sample_artifact("brief")},
                     pipeline_type="music-video", human_approved=True)
    assert get_next_stage(tmp_path, project_id, "music-video") == "song_analysis"
    with pytest.raises(CheckpointValidationError, match="canonical artifact 'song_map'"):
        write_checkpoint(tmp_path, project_id, "song_analysis", "awaiting_human",
                         artifacts={}, pipeline_type="music-video")
    song_map = build_song_map(_synthetic_audiomap(), audio_path="master.wav")
    with pytest.raises(CheckpointValidationError, match="GATE VIOLATION"):
        write_checkpoint(tmp_path, project_id, "song_analysis", "completed",
                         artifacts={"song_map": song_map}, pipeline_type="music-video")
    write_checkpoint(tmp_path, project_id, "song_analysis", "awaiting_human",
                     artifacts={"song_map": song_map}, pipeline_type="music-video")
    board = load_board_state(project_dir)
    assert board["artifacts"]["song_map"] == song_map
    assert get_next_stage(tmp_path, project_id, "music-video") == "song_analysis"
    write_checkpoint(tmp_path, project_id, "song_analysis", "completed",
                     artifacts={"song_map": song_map}, pipeline_type="music-video",
                     human_approved=True)
    assert get_next_stage(tmp_path, project_id, "music-video") == "proposal"


def test_manifest_tool_references_resolve(isolated_tool_registry) -> None:
    from lib.pipeline_loader import get_required_tools

    isolated_tool_registry.discover()
    manifest = load_pipeline("music-video")
    names = get_required_tools(manifest)
    for mode in manifest["production_modes"]:
        names.update(mode.get("required_tools", []))
        names.update(mode.get("optional_tools", []))
    assert all(isolated_tool_registry.get(name) is not None for name in names)
    assert isinstance(isolated_tool_registry.get("song_beatmap"), SongBeatmap)
    assets = next(s for s in manifest["stages"] if s["name"] == "assets")
    assert "video_selector" not in assets.get("required_tools", [])


def test_script_director_example_matches_current_schema() -> None:
    text = (SKILLS_DIR / "pipelines/music-video/script-director.md").read_text(encoding="utf-8")
    section = json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))
    validate_artifact("script", {
        "version": "1.0", "title": "Music Test", "total_duration_seconds": 48,
        "sections": [section],
    })


@pytest.mark.parametrize("params", [{}, {"input_path": "a", "output_path": "b", "phrase_bars": 0}])
def test_tool_rejects_invalid_inputs(params: dict) -> None:
    result = SongBeatmap().execute(params)
    assert not result.success
    assert "Invalid song_beatmap input" in result.error


@pytest.mark.parametrize("content", ["{broken", "[]", '{"word_timestamps": [{"word": "hi"}]}'])
def test_bad_transcript_fails_before_analysis(tmp_path: Path, monkeypatch, content: str) -> None:
    def unexpected_analysis():
        pytest.fail("Invalid timing inputs must fail before audio analysis")

    monkeypatch.setattr("tools.analysis.song_beatmap._load_analyzer", unexpected_analysis)
    master = tmp_path / "master.wav"
    master.write_bytes(b"dummy")
    transcript = tmp_path / "transcript.json"
    transcript.write_text(content, encoding="utf-8")
    output = tmp_path / "song_map.json"
    result = SongBeatmap().execute({
        "input_path": str(master), "output_path": str(output), "transcript_path": str(transcript),
    })
    assert not result.success and "Invalid transcript" in result.error
    assert not output.exists() and not (tmp_path / "audiomap.json").exists()


def test_tool_cannot_overwrite_master(tmp_path: Path) -> None:
    master = tmp_path / "master.wav"
    master.write_bytes(b"original audio")
    result = SongBeatmap().execute({"input_path": str(master), "output_path": str(master)})
    assert not result.success and "distinct" in result.error
    assert master.read_bytes() == b"original audio"


def test_lyric_at_section_boundary_belongs_to_next_section() -> None:
    song_map = build_song_map(_synthetic_audiomap(), audio_path="master.wav", transcript={
        "segments": [{"text": "next verse", "start": 15, "end": 17}],
    })
    assert song_map["lyrics"]["lines"][0]["section_id"] == song_map["sections"][1]["id"]


def test_cutdown_tail_does_not_invent_an_off_grid_endpoint() -> None:
    audiomap = _synthetic_audiomap()
    audiomap["grid"]["downbeats_sec"] = [b for b in audiomap["grid"]["downbeats_sec"] if b <= 88]
    song_map = build_song_map(audiomap, audio_path="master.wav")
    downbeats = set(song_map["grid"]["downbeats_sec"])
    for window in song_map["cutdown_windows"]:
        assert window["start_seconds"] in downbeats
        assert window["end_seconds"] in downbeats


def test_lyrics_align_to_transcript_including_cjk() -> None:
    transcript = {
        "language": "zh",
        "word_timestamps": [
            {"word": "我们", "start": 16.0, "end": 16.6},
            {"word": "一起", "start": 16.6, "end": 17.2},
            {"word": "飞", "start": 17.2, "end": 18.0},
            {"word": "hold", "start": 48.0, "end": 48.4},
            {"word": "on", "start": 48.4, "end": 48.8},
        ],
    }
    lyrics = "[Verse]\n我们一起飞\n\n[Chorus]\nHold on\nthis line was never sung"
    song_map = build_song_map(
        _synthetic_audiomap(), audio_path="master.wav", transcript=transcript, lyrics_text=lyrics
    )
    validate_artifact("song_map", song_map)
    lines = song_map["lyrics"]["lines"]
    assert [line["text"] for line in lines] == ["我们一起飞", "Hold on", "this line was never sung"]
    assert lines[0]["start_seconds"] == pytest.approx(16.0)
    assert lines[0]["end_seconds"] == pytest.approx(18.0)
    assert lines[1]["aligned"] is True
    assert lines[2]["aligned"] is False and lines[2]["start_seconds"] is None
    assert song_map["lyrics"]["alignment_coverage"] == pytest.approx(0.667, abs=1e-3)


def test_untracked_quiet_intro_is_not_merged_into_the_chorus() -> None:
    """Regression: librosa often finds no beats in a quiet intro. The analyzer's
    phrases then start late, and the whole pre-grid span used to become one
    atom that was folded into the first (loud) section."""
    audiomap = _synthetic_audiomap()
    intro_end = 16.0  # first 8 bars at 120 BPM
    audiomap["grid"]["beats_sec"] = [b for b in audiomap["grid"]["beats_sec"] if b >= intro_end]
    audiomap["grid"]["downbeats_sec"] = [b for b in audiomap["grid"]["downbeats_sec"] if b >= intro_end]
    audiomap["phrases"] = [p for p in audiomap["phrases"] if p["start"] >= intro_end]

    song_map = build_song_map(audiomap, audio_path="master.wav")
    validate_artifact("song_map", song_map)
    first = song_map["sections"][0]
    assert first["label"] == "intro"
    assert first["start_seconds"] == 0
    assert first["end_seconds"] == pytest.approx(intro_end)
    assert first["pacing"] == "phrase_flow"


def test_free_time_music_uses_phrase_flow() -> None:
    audiomap = _synthetic_audiomap()
    audiomap["stats"]["grid_counts"] = {"strong": 5, "weak": 5, "syncopated": 10, "off-grid": 80}
    song_map = build_song_map(audiomap, audio_path="master.wav")
    assert song_map["tempo"]["rhythmic_confidence"] == "free"
    assert all(s["pacing"] == "phrase_flow" for s in song_map["sections"])
    assert all(s["cut_unit_beats"] is None for s in song_map["sections"])


def _have_analyzer_runtime() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        import librosa  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _have_analyzer_runtime(), reason="needs ffmpeg + librosa + soundfile")
def test_song_beatmap_end_to_end_on_click_track(tmp_path: Path) -> None:
    track = tmp_path / "click.wav"
    # 120 BPM click (0.5 s period), 32 s, rendered with ffmpeg's aevalsrc.
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "aevalsrc='0.8*sin(2*PI*1000*t)*exp(-60*mod(t\\,0.5))':s=22050:d=32",
            str(track),
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "artifacts" / "song_map.json"
    result = SongBeatmap().execute({"input_path": str(track), "output_path": str(out)})
    assert result.success, result.error

    song_map = json.loads(out.read_text(encoding="utf-8"))
    validate_artifact("song_map", song_map)
    assert song_map["audio"]["duration_seconds"] == pytest.approx(32.0, abs=0.1)
    assert song_map["tempo"]["bpm"] == pytest.approx(120.0, rel=0.05)
    assert (tmp_path / "artifacts" / "audiomap.json").is_file()
