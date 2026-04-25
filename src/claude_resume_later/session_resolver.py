"""Resolve --latest session ID from Claude Code project directory.

Depends on Claude Code internal convention: cwd is encoded by replacing
slashes with hyphens (e.g. /work/jun/kickatena -> -work-jun-kickatena).
If this convention changes, only this module needs updating.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def resolve_latest_session(cwd: str | None = None) -> str:
    cwd = os.path.realpath(os.path.abspath(cwd or os.getcwd()))
    projects_dir = Path.home() / ".claude" / "projects"
    encoded = cwd.replace("/", "-")
    project_dir = projects_dir / encoded

    if not project_dir.is_dir():
        raise FileNotFoundError(f"No sessions found for cwd={cwd} (looked in {project_dir})")

    jsonl_files = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jsonl_files:
        raise FileNotFoundError(f"No sessions found for cwd={cwd} (no .jsonl files in {project_dir})")

    session_id = jsonl_files[0].stem
    if not _UUID_RE.match(session_id):
        raise ValueError(f"Latest session file name is not a valid UUID: {session_id}")

    return session_id
