"""Template-derived fictional test assets; never reads a user's artist catalog.

All profile text, PNGs and voice state are synthetic regression data. Only the
distributed blank template is read from source control.
"""
from __future__ import annotations

import copy
from pathlib import Path

from PIL import Image
import yaml

from schemas.artifacts import validate_artifact

TEMPLATE = Path(__file__).resolve().parents[2] / "artists/_template/artist.yaml"


def template_profile() -> dict:
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def write_artist(root: Path, artist_id: str = "solo", *, canonical: bool = True,
                 provider_bound: bool = False, collaborator: str | None = None,
                 supporting: bool = True) -> dict:
    """Write a valid local profile and distinct tiny PNGs under an explicit root."""
    profile = copy.deepcopy(template_profile())
    profile.update(id=artist_id, version=3, status="draft")
    profile["names"].update(stage=f"Fixture {artist_id}", latin=artist_id.upper())
    profile["identity"].update(
        prompt_core=f"{artist_id.upper()}, an original fictional adult with neutral features and synthetic short hair.",
        locked_traits=["Synthetic short hair", "Plain test wristband on the right wrist"],
        visible_traits={"hands": ["Plain test wristband on the right wrist"], "back": ["Synthetic short hair"]},
        visual_lock_status="fixture_approved" if canonical else "fixture_pending_review",
        negative_prompt="different face, text, watermark",
        looks=[{"id": "main", "name": "Fixture look", "era": "test", "wardrobe": "plain test jacket"}],
        bible=[{"path": f"bible/{artist_id}_main_front.png", "look": "main", "view": "front",
                "canonical": canonical, "identity_version": 3, "reference_role": "identity",
                "review_status": "fixture_approved" if canonical else "fixture_candidate"},
               {"path": f"bible/{artist_id}_main_front_candidate.png", "look": "main", "view": "front",
                "canonical": False, "identity_version": 2, "reference_role": "identity", "review_status": "fixture_candidate"},
               {"path": f"bible/{artist_id}_main_expressions.png", "look": "main", "view": "expressions",
                "canonical": False, "reference_role": "identity"}])
    if supporting:
        profile["identity"]["bible"].append({"path": f"bible/{artist_id}_main_turnaround.png", "look": "main",
            "view": "turnaround", "canonical": False, "reference_role": "wardrobe_proportions"})
    profile["voice"].update(style_signature="fictional test vocal, clear delivery, simple acoustic backing",
                            duet_descriptor=f"{artist_id} fixture vocal", lock_status="pending_audition",
                            canonical_tracks=[], persona_id=None)
    if provider_bound:
        profile["voice"].update(lock_status="provider_bound", provider_binding={"kind": "voice",
            "name": f"Fixture {artist_id}", "url": "", "source_song_url": "", "confirmed": True,
            "confirmed_on": "2026-01-01"})
    if collaborator:
        profile["collaborators"] = [{"artist_id": collaborator, "relation": "fictional test collaborator", "duet_style": "fictional test duet"}]
    validate_artifact("artist_profile", profile)
    directory = Path(root) / artist_id
    directory.mkdir(parents=True, exist_ok=True)
    for index, entry in enumerate(profile["identity"]["bible"]):
        path = directory / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (24, 24), (index * 50, 80, 180)).save(path)
    (directory / "artist.yaml").write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return profile


def write_library(root: Path, *, pending_alpha: bool = False, provider_bound: bool = False) -> Path:
    write_artist(root, "solo", supporting=False)
    write_artist(root, "alpha", canonical=not pending_alpha, provider_bound=provider_bound, collaborator="beta")
    write_artist(root, "beta", provider_bound=provider_bound, collaborator="alpha")
    return Path(root)
