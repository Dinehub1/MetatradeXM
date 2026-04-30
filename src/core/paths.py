"""
paths.py — Centralized project path definitions.

ROOT_DIR is found by walking up from this file until we find a directory
containing the project's marker files (.env, .git, requirements.txt, or
config/scoring_weights.json). This makes the bot resilient to being
moved into different folder layouts.
"""
from pathlib import Path

_MARKERS = (".env", ".git", "requirements.txt", "config/scoring_weights.json")


def _find_root() -> Path:
    """Walk up from this file to find the project root."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        for marker in _MARKERS:
            if (parent / marker).exists():
                return parent
    # Fallback: 3 levels up (src/core/paths.py → project root)
    return here.parent.parent.parent


ROOT_DIR = _find_root()

DATA_DIR = ROOT_DIR / "data"
STATE_DIR = ROOT_DIR / "state"
LOG_DIR = ROOT_DIR / "logs"
CONFIG_DIR = ROOT_DIR / "config"
SKILLS_DIR = ROOT_DIR / "skills"

# Ensure runtime directories exist
for _d in [DATA_DIR, STATE_DIR, LOG_DIR, CONFIG_DIR, SKILLS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
