from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from claude_resume_later.ccusage import BlockProvider
from claude_resume_later.models import FailureKind, Job, JobStatus, PromptRef, TokenBlock
from claude_resume_later.runner import ExecutionResult, execute


def _make_job(tmp_path: Path, **overrides) -> Job:
    defaults = dict(
        id="testjob1",
        session_id="12345678-1234-1234-1234-123456789abc",
        prompt_ref=PromptRef(inline="continue"),
        cwd=str(tmp_path),
        run_after=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 4, 25, 9, 0, tzinfo=timezone.utc),
        status=JobStatus.RUNNING,
    )
    defaults.update(overrides)
    return Job(**defaults)


def _mock_provider() -> Mock:
    return Mock(spec=BlockProvider)


class TestRunner:
    def test_invalid_uuid_rejected(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, session_id="not-a-uuid")
        result = execute(job, _mock_provider())
        assert not result.success
        assert result.failure_kind == FailureKind.PERMANENT
        assert "Invalid session UUID" in (result.error or "")

    def test_cwd_not_exist(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, cwd="/nonexistent/path/xyz")
        result = execute(job, _mock_provider())
        assert not result.success
        assert result.failure_kind == FailureKind.PERMANENT

    @patch("claude_resume_later.runner._resolve_claude", return_value=None)
    def test_claude_not_found(self, mock_resolve: MagicMock, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        result = execute(job, _mock_provider())
        assert not result.success
        assert result.failure_kind == FailureKind.PERMANENT
        assert "not found" in (result.error or "")

    @patch("claude_resume_later.runner._verify_claude_flags", return_value=None)
    @patch("claude_resume_later.runner._resolve_claude", return_value="/usr/bin/claude")
    @patch("claude_resume_later.runner.subprocess.Popen")
    def test_success(self, mock_popen: MagicMock, mock_resolve: MagicMock, mock_verify: MagicMock, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.poll.return_value = 0
        proc_mock.pid = 12345
        proc_mock.wait.return_value = 0
        mock_popen.return_value = proc_mock

        job = _make_job(tmp_path)

        with patch("claude_resume_later.runner.LOG_DIR", log_dir):
            result = execute(job, _mock_provider())

        assert result.success

    @patch("claude_resume_later.runner._verify_claude_flags", return_value=None)
    @patch("claude_resume_later.runner._resolve_claude", return_value="/usr/bin/claude")
    @patch("claude_resume_later.runner.subprocess.Popen")
    def test_nonzero_exit(self, mock_popen: MagicMock, mock_resolve: MagicMock, mock_verify: MagicMock, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        proc_mock = MagicMock()
        proc_mock.returncode = 1
        proc_mock.poll.return_value = 1
        proc_mock.pid = 12345
        proc_mock.wait.return_value = 1
        mock_popen.return_value = proc_mock

        job = _make_job(tmp_path)

        with patch("claude_resume_later.runner.LOG_DIR", log_dir):
            result = execute(job, _mock_provider())

        assert not result.success
        assert result.failure_kind == FailureKind.RETRIABLE
        assert "exited with code 1" in (result.error or "")

    @patch("claude_resume_later.runner._verify_claude_flags", return_value=None)
    @patch("claude_resume_later.runner._resolve_claude", return_value="/usr/bin/claude")
    @patch("claude_resume_later.runner.subprocess.Popen", side_effect=FileNotFoundError("claude"))
    def test_file_not_found(self, mock_popen: MagicMock, mock_resolve: MagicMock, mock_verify: MagicMock, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        job = _make_job(tmp_path)

        with patch("claude_resume_later.runner.LOG_DIR", log_dir):
            result = execute(job, _mock_provider())

        assert not result.success
        assert result.failure_kind == FailureKind.PERMANENT

    def test_nul_in_prompt(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, prompt_ref=PromptRef(inline="hello\0world"))

        with patch("claude_resume_later.runner._resolve_claude", return_value="/usr/bin/claude"), \
             patch("claude_resume_later.runner._verify_claude_flags", return_value=None):
            result = execute(job, _mock_provider())

        assert not result.success
        assert result.failure_kind == FailureKind.PERMANENT
        assert "NUL" in (result.error or "")

    @patch("claude_resume_later.runner._verify_claude_flags", return_value=None)
    @patch("claude_resume_later.runner._resolve_claude", return_value="/usr/bin/claude")
    @patch("claude_resume_later.runner._INITIAL_TIMEOUT", 0.1)
    @patch("claude_resume_later.runner.subprocess.Popen")
    def test_initial_timeout(self, mock_popen: MagicMock, mock_resolve: MagicMock, mock_verify: MagicMock, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        proc_mock.pid = 12345
        mock_popen.return_value = proc_mock

        job = _make_job(tmp_path)

        with patch("claude_resume_later.runner.LOG_DIR", log_dir):
            with patch("claude_resume_later.runner.os.getpgid", return_value=12345):
                with patch("claude_resume_later.runner.os.killpg"):
                    result = execute(job, _mock_provider())

        assert not result.success
        assert result.failure_kind == FailureKind.RETRIABLE
        assert "initial timeout" in (result.error or "")

    @patch("claude_resume_later.runner._verify_claude_flags", return_value=None)
    @patch("claude_resume_later.runner._resolve_claude", return_value="/usr/bin/claude")
    @patch("claude_resume_later.runner.subprocess.Popen")
    def test_total_timeout(self, mock_popen: MagicMock, mock_resolve: MagicMock, mock_verify: MagicMock, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        proc_mock.pid = 12345
        proc_mock.wait.side_effect = subprocess.TimeoutExpired("claude", 14400)
        mock_popen.return_value = proc_mock

        job = _make_job(tmp_path)

        with patch("claude_resume_later.runner.LOG_DIR", log_dir), \
             patch("claude_resume_later.runner._wait_initial_output", return_value=True), \
             patch("claude_resume_later.runner.os.getpgid", return_value=12345), \
             patch("claude_resume_later.runner.os.killpg"):
            result = execute(job, _mock_provider())

        assert not result.success
        assert result.failure_kind == FailureKind.RETRIABLE
        assert "Total execution timeout" in (result.error or "")

    @patch("claude_resume_later.runner._verify_claude_flags", return_value=None)
    @patch("claude_resume_later.runner._resolve_claude", return_value="/usr/bin/claude")
    @patch("claude_resume_later.runner.subprocess.Popen")
    def test_non_json_stdout_saved_to_log(self, mock_popen: MagicMock, mock_resolve: MagicMock, mock_verify: MagicMock, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.poll.return_value = 0
        proc_mock.pid = 12345
        proc_mock.wait.return_value = 0
        mock_popen.return_value = proc_mock

        job = _make_job(tmp_path)

        with patch("claude_resume_later.runner.LOG_DIR", log_dir):
            log_path = log_dir / f"{job.id}.log"
            result = execute(job, _mock_provider())

        assert result.success
        assert log_path.exists()
