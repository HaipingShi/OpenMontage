"""Application paths and independently owned user data — single source of truth.

The projects root is the most load-bearing path in the system: checkpoints
are written under it, tool events are attributed against it, and the Backlot
board watches it. Define it once.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _data_path(variable: str, default: Path) -> Path:
    value = os.environ.get(variable, '').strip()
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        raise ValueError(f'{variable} must be an absolute data path')
    return path.resolve()


# Reading configuration never creates directories or migrates user files.
DATA_DIR = _data_path('OPENMONTAGE_DATA_DIR', Path.home() / 'OpenMontageData')
PROJECTS_DIR = _data_path('OPENMONTAGE_PROJECTS_DIR', DATA_DIR / 'projects')
LIBRARY_DIR = _data_path('OPENMONTAGE_LIBRARY_DIR', DATA_DIR / 'library')
ARTISTS_DIR = LIBRARY_DIR / 'artists'

# Migration accepts this location explicitly; runtime does not guess which
# checkout contains a user's project or a same-named reusable character.
LEGACY_PROJECTS_DIR = REPO_ROOT / 'projects'
