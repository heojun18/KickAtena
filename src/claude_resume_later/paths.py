from __future__ import annotations

import os
import stat
from pathlib import Path

_XDG_STATE_HOME = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))

STATE_DIR = Path(_XDG_STATE_HOME) / "claude-resume-later"
QUEUE_PATH = STATE_DIR / "queue.json"
LOCK_PATH = STATE_DIR / ".lock"
LOG_DIR = STATE_DIR / "logs"
PROMPTS_DIR = STATE_DIR / "prompts"
CACHE_PATH = STATE_DIR / "ccusage-cache.json"

_DIR_MODE = 0o700
_FILE_MODE = 0o600


def ensure_runtime_dirs() -> None:
    for d in (STATE_DIR, LOG_DIR, PROMPTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
        _chmod_if_needed(d, _DIR_MODE)

    for f in (QUEUE_PATH, LOCK_PATH, CACHE_PATH):
        if f.exists():
            _chmod_if_needed(f, _FILE_MODE)


def _chmod_if_needed(path: Path, mode: int) -> None:
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current != mode:
            os.chmod(path, mode)
    except OSError:
        pass
