"""Song beat map for the music-video pipeline.

Turns a finished song into a `song_map` artifact — the single timing spine the
music-video pipeline hangs every shot, cut, lyric, and lip-sync window on.

It does NOT re-implement beat tracking. It loads the canonical analyzer that
ships with the `music-to-video` Layer 3 skill
(`.agents/skills/music-to-video/scripts/analyze-beatgrid.py`) so the whole repo
keeps one beat analyzer, then derives the layers a music video needs on top:

  - rhythmic confidence (is the beat grid real, or a metronome imposed on
    rubato / ambient music?),
  - song sections built from 4-bar phrases with heuristic role labels
    (intro / verse / pre_chorus / chorus / bridge / breakdown / outro) that the
    song-analysis director confirms by ear and by lyrics,
  - a per-section cut unit (in beats) and the cut points it implies,
  - lyric lines aligned to word timestamps from `transcriber` (Latin and CJK),
  - hook windows for 15s / 30s vertical cutdowns.

Everything is deterministic: same audio + same transcript -> same song_map.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

import jsonschema

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYZER_PATH = (
    REPO_ROOT / ".agents" / "skills" / "music-to-video" / "scripts" / "analyze-beatgrid.py"
)

SONG_MAP_VERSION = "1.0"

# Same thresholds the canonical analyzer uses for its energy phases, so a
# section's level means the same thing in audiomap.json and song_map.json.
LEVEL_THRESHOLDS = ((0.2, "VOID"), (0.4, "LOW"), (0.65, "MEDIUM"))

# Target on-screen shot length (seconds) per energy level for beat-cut sections.
TARGET_SHOT_SECONDS = {"HIGH": 1.5, "MEDIUM": 3.0, "LOW": 5.0, "VOID": 8.0}
CUT_UNIT_CHOICES = (1, 2, 4, 8, 16)

CUTDOWN_TARGETS = (15, 30)

_CJK_OR_WORD = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]|[a-z0-9']+")


def _level(energy: float) -> str:
    for threshold, name in LEVEL_THRESHOLDS:
        if energy < threshold:
            return name
    return "HIGH"


def _tokens(text: str) -> list[str]:
    """Tokenize lyrics/transcript text. CJK splits per character, Latin per word."""
    return _CJK_OR_WORD.findall(text.lower())


def _load_analyzer():
    spec = importlib.util.spec_from_file_location("om_analyze_beatgrid", ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load beat analyzer at {ANALYZER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SongBeatmap(BaseTool):
    name = "song_beatmap"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "librosa"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg", "python:librosa", "python:soundfile", "python:numpy"]
    install_instructions = (
        "pip install librosa soundfile numpy\n"
        "Install ffmpeg:\n"
        "  Windows: winget install ffmpeg\n"
        "  macOS: brew install ffmpeg\n"
        "  Linux: sudo apt install ffmpeg"
    )
    agent_skills = ["music-to-video"]

    capabilities = [
        "beat_grid",
        "song_sections",
        "cut_grid",
        "lyric_alignment",
        "cutdown_windows",
    ]
    supports = {
        "cjk_lyrics": True,
        "user_lyrics_alignment": True,
        "offline": True,
    }
    best_for = [
        "timing spine for music videos: beats, bars, sections, cut points",
        "aligning user-supplied lyrics to sung word timestamps",
        "finding the chorus hook window for 15s/30s vertical cutdowns",
    ]
    not_good_for = [
        "speech or narration timing (use transcriber directly)",
        "choosing music for a video (use audio_energy / music_library)",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path", "output_path"],
        "properties": {
            "input_path": {
                "type": "string",
                "description": "Path to the song master (the exact file that will be muxed).",
            },
            "output_path": {
                "type": "string",
                "description": "Where to write song_map.json, e.g. projects/<id>/artifacts/song_map.json",
            },
            "audiomap_path": {
                "type": "string",
                "description": (
                    "Optional path for the raw audiomap.json from the canonical analyzer "
                    "(consumed by the HyperFrames music-to-video skill). Defaults to "
                    "audiomap.json next to output_path."
                ),
            },
            "transcript_path": {
                "type": "string",
                "description": (
                    "Optional transcriber output JSON (word_timestamps). Run transcriber "
                    "on an isolated vocal stem when one exists; large-v3 for sung vocals."
                ),
            },
            "lyrics_text": {
                "type": "string",
                "description": (
                    "Optional authoritative lyrics, one line per row. Blank rows and "
                    "[Section] tags are ignored. Aligned to transcript words when "
                    "transcript_path is given; otherwise lines are returned untimed."
                ),
            },
            "suno_lyrics_text": {"type": "string", "description": "Lyrics from the approved Suno generation record, preferred over user lyrics."},
            "lyrics_confidence_threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.6},
            "stems": {"type": "object", "properties": {k: {"type": "string"} for k in ("vocals", "accompaniment", "model")}, "additionalProperties": False},
            "language": {"type": "string", "description": "ISO 639-1 code for the lyrics."},
            "phrase_bars": {
                "type": "integer",
                "default": 4,
                "minimum": 1,
                "description": "Bars per phrase — the atom sections are built from.",
            },
            "source_type": {
                "type": "string",
                "enum": ["user_supplied", "music_library", "ai_generated"],
                "default": "user_supplied",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "song_map_path": {"type": "string"},
            "audiomap_path": {"type": "string"},
            "summary": {"type": "string"},
            "song_map": {"type": "object"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=1024, vram_mb=0, disk_mb=50, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = ["input_path", "transcript_path", "lyrics_text", "suno_lyrics_text", "lyrics_confidence_threshold", "stems", "phrase_bars"]
    side_effects = ["writes song_map.json and audiomap.json"]
    user_visible_verification = [
        "Listen at each section boundary and confirm the label matches the song",
        "Spot-check 3 lyric lines against the vocal",
    ]

    def get_status(self) -> ToolStatus:
        if not ANALYZER_PATH.exists():
            return ToolStatus.UNAVAILABLE
        return super().get_status()

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            jsonschema.validate(inputs, self.input_schema)
        except jsonschema.ValidationError as exc:
            return ToolResult(success=False, error=f"Invalid song_beatmap input: {exc.message}")

        input_path = Path(inputs["input_path"])
        output_path = Path(inputs["output_path"])
        audiomap_path = Path(inputs.get("audiomap_path") or output_path.parent / "audiomap.json")
        if not input_path.is_file():
            return ToolResult(success=False, error=f"Song not found: {input_path}")
        # Read optional timing data before expensive analysis or any output writes.
        transcript = None
        transcript_path = inputs.get("transcript_path")
        if transcript_path:
            try:
                transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
                if not isinstance(transcript, dict):
                    raise ValueError("transcript must be a JSON object")
                _transcript_words(transcript)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return ToolResult(success=False, error=f"Invalid transcript: {exc}")

        paths = [input_path, output_path, audiomap_path]
        if transcript_path:
            paths.append(Path(transcript_path))
        if len({p.resolve() for p in paths}) != len(paths):
            return ToolResult(success=False, error="Song, transcript, and output paths must be distinct")
        if not ANALYZER_PATH.exists():
            return ToolResult(
                success=False,
                error=f"Canonical beat analyzer missing at {ANALYZER_PATH}",
            )
        if shutil.which("ffmpeg") is None:
            return ToolResult(success=False, error="ffmpeg not found on PATH")

        start = time.time()
        phrase_bars = int(inputs.get("phrase_bars", 4))

        try:
            analyzer = _load_analyzer()
            audiomap = analyzer.analyze(str(input_path), phrase_bars=phrase_bars)
        except ImportError as exc:
            return ToolResult(
                success=False,
                error=f"Beat analyzer dependencies missing ({exc}). {self.install_instructions}",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Beat analysis failed: {exc}")

        try:
            from schemas.artifacts import validate_artifact

            song_map = build_song_map(
                audiomap,
                audio_path=str(input_path),
                source_type=inputs.get("source_type", "user_supplied"),
                transcript=transcript,
                lyrics_text=inputs.get("lyrics_text"),
                suno_lyrics_text=inputs.get("suno_lyrics_text"),
                lyrics_confidence_threshold=inputs.get("lyrics_confidence_threshold", 0.6),
                stems=inputs.get("stems"),
                language=inputs.get("language"),
                audiomap_path=str(audiomap_path),
            )
            validate_artifact("song_map", song_map)
        except Exception as exc:
            return ToolResult(success=False, error=f"song_map failed schema validation: {exc}")

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            audiomap_path.parent.mkdir(parents=True, exist_ok=True)
            audiomap_path.write_text(json.dumps(audiomap, ensure_ascii=False, indent=2), encoding="utf-8")
            output_path.write_text(json.dumps(song_map, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not write song analysis: {exc}")

        return ToolResult(
            success=True,
            data={
                "song_map_path": str(output_path),
                "audiomap_path": str(audiomap_path),
                "summary": song_map["summary"],
                "song_map": song_map,
            },
            artifacts=[str(output_path), str(audiomap_path)],
            duration_seconds=round(time.time() - start, 2),
        )


# ---------------------------------------------------------------------------
# Derivation (pure functions — unit-testable without audio)
# ---------------------------------------------------------------------------


def build_song_map(
    audiomap: dict[str, Any],
    *,
    audio_path: str,
    source_type: str = "user_supplied",
    transcript: Optional[dict[str, Any]] = None,
    lyrics_text: Optional[str] = None,
    language: Optional[str] = None,
    suno_lyrics_text: Optional[str] = None,
    lyrics_confidence_threshold: float = 0.6,
    stems: Optional[dict[str, str]] = None,
    audiomap_path: Optional[str] = None,
) -> dict[str, Any]:
    duration = float(audiomap.get("audio", {}).get("duration_sec", 0.0))
    if duration <= 0:
        raise ValueError("Song duration must be positive")
    tempo = audiomap.get("tempo", {})
    grid = audiomap.get("grid", {})
    beats = [float(b) for b in grid.get("beats_sec", [])]
    downbeats = [float(b) for b in grid.get("downbeats_sec", [])]
    bpm = float(tempo.get("bpm", 0.0))
    beat_seconds = _median_gap(beats) or (60.0 / bpm if bpm > 0 else 0.5)

    beats_per_bar = int(tempo.get("beats_per_bar", 4))
    bar_seconds = beat_seconds * beats_per_bar

    rhythm = _rhythmic_confidence(audiomap, beats, bpm)
    atoms = _phrase_atoms(audiomap, duration, bar_seconds)
    sections = _build_sections(atoms, audiomap.get("energy_phases", []), bar_seconds)
    _label_sections(sections)
    for section in sections:
        _assign_cut_grid(section, rhythm, beats, downbeats, beat_seconds)

    if not 0 <= lyrics_confidence_threshold <= 1:
        raise ValueError("lyrics_confidence_threshold must be between 0 and 1")
    authoritative = suno_lyrics_text or lyrics_text
    lyrics = _build_lyrics(transcript, authoritative, language, sections)
    if suno_lyrics_text:
        lyrics["source"] = "suno_record"
    lyrics["confidence_threshold"] = lyrics_confidence_threshold
    cutdowns = _cutdown_windows(sections, downbeats, duration)

    summary = (
        f"{bpm:.0f} BPM ({rhythm}) · {len(downbeats)} bars · {len(sections)} sections "
        f"[{' '.join(s['label'] for s in sections)}] · "
        f"{len(lyrics['lines'])} lyric lines ({lyrics['source']}, "
        f"{int(lyrics['alignment_coverage'] * 100)}% timed) · {duration:.1f}s"
    )

    return {
        "version": SONG_MAP_VERSION,
        "summary": summary,
        "audio": {
            "path": audio_path,
            "duration_seconds": round(duration, 3),
            "source_type": source_type,
            **({"stems": stems} if stems else {}),
        },
        "tempo": {
            "bpm": round(bpm, 1),
            "beats_per_bar": int(tempo.get("beats_per_bar", 4)),
            "beat_seconds": round(beat_seconds, 4),
            "bar_seconds": round(beat_seconds * int(tempo.get("beats_per_bar", 4)), 4),
            "rhythmic_confidence": rhythm,
        },
        "grid": {
            "beats_sec": [round(b, 3) for b in beats],
            "downbeats_sec": [round(b, 3) for b in downbeats],
        },
        "sections": sections,
        "key_moments": [
            {"t": float(m["t"]), "kind": m["kind"], "delta": float(m["delta"])}
            for m in audiomap.get("key_moments", [])
        ],
        "hard_stops": [float(h["t"]) for h in audiomap.get("hard_stops", [])],
        "silences": [
            {"start_seconds": float(s["start"]), "end_seconds": float(s["end"])}
            for s in audiomap.get("silences", [])
        ],
        "lyrics": lyrics,
        "review_flags": [{"line_id": line["id"], "confidence": line["confidence"],
                          "reason": "low_alignment_confidence" if line["aligned"] else "unaligned"}
                         for line in lyrics["lines"]
                         if not line["aligned"] or line["confidence"] < lyrics_confidence_threshold],
        "cutdown_windows": cutdowns,
        "analysis": {
            "tool": "song_beatmap",
            "analyzer": "music-to-video/analyze-beatgrid.py",
            "audiomap_path": audiomap_path,
        },
    }


def _median_gap(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    gaps = sorted(b - a for a, b in zip(values, values[1:]))
    return gaps[len(gaps) // 2]


def _rhythmic_confidence(audiomap: dict[str, Any], beats: list[float], bpm: float) -> str:
    """'rhythmic' = cut on the grid; 'loose' = cut on bars/phrases; 'free' = phrase flow only."""
    if len(beats) < 8 or not 50 <= bpm <= 220:
        return "free"
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    mean = sum(gaps) / len(gaps)
    variance = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    cv = (variance ** 0.5) / mean if mean else 1.0

    grid_counts = audiomap.get("stats", {}).get("grid_counts", {})
    total = sum(grid_counts.values()) or 1
    on_grid = (grid_counts.get("strong", 0) + grid_counts.get("weak", 0)) / total

    if cv < 0.08 and on_grid >= 0.55:
        return "rhythmic"
    if cv < 0.15 and on_grid >= 0.4:
        return "loose"
    return "free"


def _phrase_atoms(audiomap: dict[str, Any], duration: float, bar_seconds: float) -> list[dict[str, Any]]:
    """Phrase-sized atoms covering [0, duration].

    The beat tracker often finds no beats in a quiet intro or a fading outro, so
    the analyzer's phrases can start late and its last phrase can swallow the
    whole tail. Untracked spans are tiled with the phrase grid extrapolated at
    the same tempo, so a 24 s ambient intro becomes intro phrases instead of one
    atom that gets merged into the first chorus.
    """
    phrases = audiomap.get("phrases") or []
    tracked = [
        {"start": float(p["start"]), "end": float(p["end"]), "bars": int(p.get("bars", 0))}
        for p in phrases
        if float(p["end"]) > float(p["start"])
    ]
    if not tracked:
        return _tile(0.0, duration, 8.0, bar_seconds)

    full = [a["end"] - a["start"] for a in tracked if a["bars"] == max(t["bars"] for t in tracked)]
    phrase_len = sorted(full)[len(full) // 2]

    atoms: list[dict[str, Any]] = []
    first = tracked[0]["start"]
    if first > 0.05:
        boundaries = [first]
        while boundaries[0] - phrase_len > bar_seconds * 0.5:
            boundaries.insert(0, boundaries[0] - phrase_len)
        boundaries.insert(0, 0.0)
        for a, b in zip(boundaries, boundaries[1:]):
            atoms.append({"start": a, "end": b, "bars": round((b - a) / bar_seconds)})

    tracked[-1]["end"] = max(tracked[-1]["end"], duration)
    for atom in tracked:
        if atom["end"] - atom["start"] > phrase_len * 1.5:
            atoms.extend(_tile(atom["start"], atom["end"], phrase_len, bar_seconds))
        else:
            atoms.append(atom)
    return atoms


def _tile(start: float, end: float, step: float, bar_seconds: float) -> list[dict[str, Any]]:
    out, t = [], start
    while t < end - 1e-6:
        stop = end if end - (t + step) < bar_seconds * 0.5 else t + step
        out.append({"start": t, "end": stop, "bars": round((stop - t) / bar_seconds)})
        t = stop
    return out


def _weighted_energy(start: float, end: float, phases: list[dict[str, Any]]) -> float:
    total, weight = 0.0, 0.0
    for p in phases:
        overlap = min(end, float(p["end"])) - max(start, float(p["start"]))
        if overlap > 0:
            total += overlap * float(p.get("energy", 0.0))
            weight += overlap
    return total / weight if weight else 0.0


def _build_sections(
    atoms: list[dict[str, Any]], phases: list[dict[str, Any]], bar_seconds: float
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for atom in atoms:
        energy = _weighted_energy(atom["start"], atom["end"], phases)
        level = _level(energy)
        if sections and sections[-1]["level"] == level:
            prev = sections[-1]
            span_prev = prev["end_seconds"] - prev["start_seconds"]
            span_atom = atom["end"] - atom["start"]
            prev["energy"] = (prev["energy"] * span_prev + energy * span_atom) / (span_prev + span_atom)
            prev["end_seconds"] = atom["end"]
            prev["bars"] += atom["bars"]
            continue
        sections.append(
            {
                "start_seconds": atom["start"],
                "end_seconds": atom["end"],
                "bars": atom["bars"],
                "energy": energy,
                "level": level,
            }
        )

    # A pickup shorter than two bars joins the section it leads into.
    first = sections[0] if sections else None
    if first and len(sections) > 1 and first["end_seconds"] - first["start_seconds"] < bar_seconds * 2:
        sections.pop(0)
        sections[0]["start_seconds"] = first["start_seconds"]
        sections[0]["bars"] += first["bars"]

    for i, s in enumerate(sections):
        s["id"] = f"s{i + 1:02d}"
        s["start_seconds"] = round(s["start_seconds"], 3)
        s["end_seconds"] = round(s["end_seconds"], 3)
        s["energy"] = round(s["energy"], 3)
    return sections


def _label_sections(sections: list[dict[str, Any]]) -> None:
    if not sections:
        return
    energies = sorted(s["energy"] for s in sections)
    peak = energies[-1]
    chorus_floor = max(energies[int(len(energies) * 0.7) if len(energies) > 1 else 0], peak * 0.85)

    for s in sections:
        s["label"] = "chorus" if s["energy"] >= chorus_floor and s["level"] in ("HIGH", "MEDIUM") else ""

    chorus_idx = [i for i, s in enumerate(sections) if s["label"] == "chorus"]
    for i, s in enumerate(sections):
        if s["label"]:
            continue
        choruses_before = sum(1 for c in chorus_idx if c < i)
        choruses_after = sum(1 for c in chorus_idx if c > i)
        if i == 0:
            s["label"] = "intro"
        elif i == len(sections) - 1:
            s["label"] = "outro"
        elif s["level"] in ("VOID", "LOW") and choruses_before and choruses_after:
            s["label"] = "bridge" if choruses_before >= 2 else "breakdown"
        elif (
            (i + 1) in chorus_idx
            and 0 < s["bars"] <= 8
            and sections[i - 1]["label"] not in ("intro", "chorus")
            and s["energy"] > sections[i - 1]["energy"] + 0.05
        ):
            s["label"] = "pre_chorus"
        else:
            s["label"] = "verse"

    for s in sections:
        s["label_confidence"] = "heuristic"


def _assign_cut_grid(
    section: dict[str, Any],
    rhythm: str,
    beats: list[float],
    downbeats: list[float],
    beat_seconds: float,
) -> None:
    start, end = section["start_seconds"], section["end_seconds"]
    tracked_beats = [b for b in beats if start - 1e-6 <= b < end - 1e-6]
    beat_cut = (
        rhythm in ("rhythmic", "loose")
        and section["level"] != "VOID"
        and len(tracked_beats) >= 4
    )
    section["pacing"] = "beat_cut" if beat_cut else "phrase_flow"

    if not beat_cut:
        section["cut_unit_beats"] = None
        bars_in = [d for d in downbeats if start - 1e-6 <= d < end - 1e-6]
        section["cut_points_seconds"] = [round(start, 3)] + [
            round(d, 3) for d in bars_in[4::4] if d > start + 1e-6
        ]
        if len(tracked_beats) < 4 and section["level"] != "VOID":
            section["notes"] = (
                "No tracked beats in this span (quiet or rubato); "
                "pace by phrase/lyric and confirm by ear."
            )
        return

    target_beats = TARGET_SHOT_SECONDS[section["level"]] / max(beat_seconds, 1e-6)
    unit = min(CUT_UNIT_CHOICES, key=lambda c: abs(c - target_beats))
    if rhythm == "loose":
        unit = max(unit, 4)  # loose grids cut on the bar, never inside it
    section["cut_unit_beats"] = unit

    first_bar = next((d for d in downbeats if d >= start - 1e-6), None)
    anchor = first_bar if first_bar is not None and first_bar < end else start
    in_section = [b for b in beats if anchor - 1e-6 <= b < end - 1e-6]
    points = [round(start, 3)]
    for b in in_section[::unit]:
        if b > points[-1] + 1e-3:
            points.append(round(b, 3))
    section["cut_points_seconds"] = points


def _tagged_lyric_lines(text: str) -> list[dict[str, Any]]:
    lines, singer, tag = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"\[.*\]", line):
            tag = line
            singer = line[1:-1].split(":", 1)[1].strip() if ":" in line else None
        elif not re.fullmatch(r"\(.*\)", line):
            lines.append({"text": line, "singer": singer, "section_tag": tag})
    return lines


def _transcribed_confidence(segment: dict[str, Any]) -> float:
    # Timing is not evidence of lyric accuracy. Match segment tokens against
    # timed words; absent word evidence is explicitly low confidence.
    tokens = _tokens(str(segment.get("text", "")))
    words = [token for word in segment.get("words", []) for token in _tokens(str(word.get("word", "")))]
    matched = sum(block.size for block in difflib.SequenceMatcher(a=tokens, b=words, autojunk=False).get_matching_blocks())
    return round(matched / len(tokens), 4) if tokens else 0.0


def _parse_lyric_lines(lyrics_text: str) -> list[str]:
    lines = []
    for raw in lyrics_text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\[.*\]|\(.*\)", line):
            continue
        lines.append(line)
    return lines


def _section_for(t: Optional[float], sections: list[dict[str, Any]]) -> Optional[str]:
    if t is None:
        return None
    for s in sections:
        if s["start_seconds"] <= t < s["end_seconds"]:
            return s["id"]
    return sections[-1]["id"] if sections and t == sections[-1]["end_seconds"] else None


def _transcript_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    words = transcript.get("word_timestamps")
    if not words:
        words = [w for seg in transcript.get("segments", []) for w in seg.get("words", [])]
    out = []
    for w in words:
        for tok in _tokens(str(w.get("word", ""))):
            out.append({"tok": tok, "start": float(w["start"]), "end": float(w["end"])})
    return out


def _build_lyrics(
    transcript: Optional[dict[str, Any]],
    lyrics_text: Optional[str],
    language: Optional[str],
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    lang = language or (transcript or {}).get("language")

    if lyrics_text:
        tagged_lines = _tagged_lyric_lines(lyrics_text)
        lines_text = [item["text"] for item in tagged_lines]
        words = _transcript_words(transcript) if transcript else []
        lines = _align_lines(lines_text, words)
        for line, tagged in zip(lines, tagged_lines):
            line.update({"singer": tagged["singer"], "section_tag": tagged["section_tag"]})
        source = "user_supplied"
    elif transcript:
        lines = []
        for seg in transcript.get("segments", []):
            text = str(seg.get("text", "")).strip()
            if text:
                lines.append(
                    {
                        "text": text,
                        "start_seconds": round(float(seg["start"]), 3),
                        "end_seconds": round(float(seg["end"]), 3),
                        "aligned": True,
                        "confidence": _transcribed_confidence(seg),
                    }
                )
        source = "transcribed"
    else:
        lines, source = [], "none"

    for i, line in enumerate(lines):
        line["id"] = f"l{i + 1:03d}"
        mid = None
        if line["start_seconds"] is not None and line["end_seconds"] is not None:
            mid = (line["start_seconds"] + line["end_seconds"]) / 2
        line["section_id"] = _section_for(mid, sections)

    timed = sum(1 for line in lines if line["aligned"])
    return {
        "source": source,
        "language": lang,
        "alignment_coverage": round(timed / len(lines), 3) if lines else 0.0,
        "lines": lines,
    }


def _align_lines(lines_text: list[str], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map each lyric line to transcript word times via a global token alignment."""
    lyric_tokens: list[tuple[int, str]] = []
    for li, text in enumerate(lines_text):
        lyric_tokens.extend((li, tok) for tok in _tokens(text))

    line_times: list[list[tuple[float, float]]] = [[] for _ in lines_text]
    if words and lyric_tokens:
        matcher = difflib.SequenceMatcher(
            a=[t for _, t in lyric_tokens], b=[w["tok"] for w in words], autojunk=False
        )
        for block in matcher.get_matching_blocks():
            for k in range(block.size):
                li = lyric_tokens[block.a + k][0]
                w = words[block.b + k]
                line_times[li].append((w["start"], w["end"]))

    lines = []
    for li, text in enumerate(lines_text):
        n_tokens = sum(1 for idx, _ in lyric_tokens if idx == li) or 1
        hits = line_times[li]
        # Require a third of the line's tokens to match before trusting its timing.
        if hits and len(hits) / n_tokens >= 0.34:
            lines.append(
                {
                    "text": text,
                    "confidence": round(len(hits) / n_tokens, 4),
                    "start_seconds": round(min(h[0] for h in hits), 3),
                    "end_seconds": round(max(h[1] for h in hits), 3),
                    "aligned": True,
                }
            )
        else:
            lines.append({"text": text, "start_seconds": None, "end_seconds": None, "aligned": False, "confidence": round(len(hits) / n_tokens, 4)})
    return lines


def _cutdown_windows(
    sections: list[dict[str, Any]], downbeats: list[float], duration: float
) -> list[dict[str, Any]]:
    choruses = [s for s in sections if s["label"] == "chorus"]
    if not choruses or not downbeats:
        return []
    hook = max(choruses, key=lambda s: s["energy"])
    start = hook["start_seconds"]
    # Open one bar early so the cutdown starts on the lift into the hook.
    earlier = [d for d in downbeats if d < start - 1e-6]
    if earlier:
        start = earlier[-1]
    else:
        start = next((d for d in downbeats if d >= start), duration)
    windows = []
    for target in CUTDOWN_TARGETS:
        end_candidates = [d for d in downbeats if start + target - 1e-6 <= d <= duration]
        end = end_candidates[0] if end_candidates else max(
            (d for d in downbeats if start < d <= duration), default=start
        )
        if end - start < target * 0.6:
            continue
        windows.append(
            {
                "target_seconds": target,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "section_id": hook["id"],
            }
        )
    return windows
