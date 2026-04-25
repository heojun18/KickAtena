from __future__ import annotations

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Job


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)
    _desktop_notify("claude-resume-later warning", msg)


def fail(job: Job) -> None:
    msg = f"Job {job.id} (session {job.session_id}) failed after {job.attempts} attempt(s): {job.last_error}"
    print(f"[FAIL] {msg}", file=sys.stderr)
    _desktop_notify("claude-resume-later failure", msg)


def _desktop_notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "--app-name=claude-resume-later", title, body],
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
