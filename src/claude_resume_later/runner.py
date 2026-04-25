from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import notify
from .ccusage import BlockProvider
from .models import FailureKind, Job, JobStatus
from .paths import LOG_DIR

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_INITIAL_TIMEOUT = 300  # 5 minutes
_TOTAL_TIMEOUT = 14400  # 4 hours
_MAX_PROMPT_ARG = 100_000  # 100KB
MAX_ATTEMPTS = 3
_REQUIRED_FLAGS = ["--resume", "-p", "--permission-mode"]

_help_cache: str | None = None


class ExecutionResult:
    def __init__(self, success: bool, failure_kind: FailureKind | None = None, error: str | None = None):
        self.success = success
        self.failure_kind = failure_kind
        self.error = error


def _verify_claude_flags(claude_bin: str) -> str | None:
    global _help_cache
    if _help_cache is None:
        try:
            result = subprocess.run(
                [claude_bin, "--help"],
                capture_output=True, text=True, timeout=10,
            )
            _help_cache = result.stdout + result.stderr
        except (OSError, subprocess.TimeoutExpired):
            return None
    for flag in _REQUIRED_FLAGS:
        if flag not in _help_cache:
            return f"claude binary does not support required flag: {flag}"
    return None


def execute(job: Job, provider: BlockProvider) -> ExecutionResult:
    if not _UUID_RE.match(job.session_id):
        return ExecutionResult(False, FailureKind.PERMANENT, f"Invalid session UUID: {job.session_id}")

    real_cwd = os.path.realpath(os.path.abspath(job.cwd))
    if not os.path.isdir(real_cwd):
        return ExecutionResult(False, FailureKind.PERMANENT, f"Working directory does not exist: {job.cwd}")

    claude_bin = _resolve_claude()
    if claude_bin is None:
        return ExecutionResult(False, FailureKind.PERMANENT, "claude binary not found")

    flag_err = _verify_claude_flags(claude_bin)
    if flag_err:
        return ExecutionResult(False, FailureKind.PERMANENT, flag_err)

    try:
        prompt = job.prompt_ref.resolve()
    except (OSError, ValueError) as exc:
        return ExecutionResult(False, FailureKind.PERMANENT, f"Failed to resolve prompt: {exc}")

    if "\0" in prompt:
        return ExecutionResult(False, FailureKind.PERMANENT, "Prompt contains NUL byte")

    use_stdin = len(prompt.encode("utf-8")) > _MAX_PROMPT_ARG

    if use_stdin:
        argv = [
            claude_bin, "--resume", job.session_id,
            "-p",
            "--permission-mode", "bypassPermissions",
            "--output-format", "json",
        ]
    else:
        argv = [
            claude_bin, "--resume", job.session_id,
            "-p", prompt,
            "--permission-mode", "bypassPermissions",
            "--output-format", "json",
        ]

    log_path = LOG_DIR / f"{job.id}.log"

    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            os.chmod(log_path, 0o600)

            proc = subprocess.Popen(
                argv,
                cwd=real_cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )

            if use_stdin:
                try:
                    proc.stdin.write(prompt.encode("utf-8"))
                    proc.stdin.close()
                except OSError as exc:
                    _kill_group(proc)
                    return ExecutionResult(False, FailureKind.PERMANENT, f"Failed to write prompt to stdin: {exc}")

            if not _wait_initial_output(proc, log_path, _INITIAL_TIMEOUT):
                _kill_group(proc)
                return ExecutionResult(False, FailureKind.RETRIABLE, "No output within 5 minutes (initial timeout)")

            try:
                remaining = _TOTAL_TIMEOUT - _INITIAL_TIMEOUT
                proc.wait(timeout=max(remaining, 0))
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                return ExecutionResult(False, FailureKind.RETRIABLE, "Total execution timeout (4 hours)")

        if proc.returncode == 0:
            return ExecutionResult(True)
        return ExecutionResult(False, FailureKind.RETRIABLE, f"claude exited with code {proc.returncode}")

    except FileNotFoundError:
        return ExecutionResult(False, FailureKind.PERMANENT, f"claude binary not found at {claude_bin}")
    except OSError as exc:
        return ExecutionResult(False, FailureKind.RETRIABLE, f"OS error: {exc}")


def _resolve_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude"
    if local.is_file():
        return str(local)
    return None


def _wait_initial_output(proc: subprocess.Popen, log_path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    initial_size = log_path.stat().st_size if log_path.exists() else 0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        try:
            if log_path.stat().st_size > initial_size:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
