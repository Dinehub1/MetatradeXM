"""
paths.py — Centralized project path definitions.
"""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT_DIR / "data"
STATE_DIR = ROOT_DIR / "state"
LOG_DIR = ROOT_DIR / "logs"
CONFIG_DIR = ROOT_DIR / "config"
SKILLS_DIR = ROOT_DIR / "skills"

# Ensure runtime directories exist
for _d in [DATA_DIR, STATE_DIR, LOG_DIR, CONFIG_DIR, SKILLS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
