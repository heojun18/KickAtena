from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from claude_resume_later.cli import main
from claude_resume_later.models import Job, JobStatus, PromptRef


class TestCliParsing:
    def test_no_command_exits(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    def test_add_requires_session_or_latest(self) -> None:
        with pytest.raises(SystemExit):
            main(["add", "--prompt", "hello"])

    def test_add_requires_prompt(self) -> None:
        with pytest.raises(SystemExit):
            main(["add", "--session", "12345678-1234-1234-1234-123456789abc"])

    @patch("claude_resume_later.cli.api.add_job")
    def test_add_success(self, mock_add: MagicMock) -> None:
        mock_add.return_value = Job(
            id="abc123",
            session_id="12345678-1234-1234-1234-123456789abc",
            prompt_ref=PromptRef(inline="hello"),
            cwd="/tmp",
            run_after=datetime(2026, 5, 1, tzinfo=timezone.utc),
            created_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
        )

        main(["add", "--session", "12345678-1234-1234-1234-123456789abc", "--prompt", "hello"])
        mock_add.assert_called_once()

    @patch("claude_resume_later.cli.api.add_job")
    def test_add_with_run_after(self, mock_add: MagicMock) -> None:
        mock_add.return_value = Job(
            id="abc123",
            session_id="12345678-1234-1234-1234-123456789abc",
            prompt_ref=PromptRef(inline="hello"),
            cwd="/tmp",
            run_after=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
        )

        main([
            "add", "--session", "12345678-1234-1234-1234-123456789abc",
            "--prompt", "hello", "--run-after", "2026-05-01T12:00:00Z",
        ])
        call_kwargs = mock_add.call_args[1]
        assert call_kwargs["run_after"] == datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    @patch("claude_resume_later.cli.api.list_jobs", return_value=[])
    def test_list_empty(self, mock_list: MagicMock) -> None:
        main(["list"])
        mock_list.assert_called_once()

    @patch("claude_resume_later.cli.api.list_jobs")
    def test_list_json(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            Job(
                id="abc",
                session_id="12345678-1234-1234-1234-123456789abc",
                prompt_ref=PromptRef(inline="x"),
                cwd="/tmp",
                run_after=datetime(2026, 5, 1, tzinfo=timezone.utc),
                created_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
            )
        ]
        main(["list", "--json"])

    @patch("claude_resume_later.cli.api.cancel_job")
    def test_cancel(self, mock_cancel: MagicMock) -> None:
        main(["cancel", "abc123"])
        mock_cancel.assert_called_once_with("abc123")

    @patch("claude_resume_later.cli.api.get_job")
    def test_status_not_found(self, mock_get: MagicMock) -> None:
        mock_get.return_value = None
        with pytest.raises(SystemExit) as exc_info:
            main(["status", "nonexistent"])
        assert exc_info.value.code == 1

    @patch("claude_resume_later.cli.api.add_job", side_effect=FileNotFoundError("No sessions found for cwd=/tmp"))
    def test_add_latest_empty_dir(self, mock_add: MagicMock) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["add", "--latest", "--prompt", "continue"])
        assert exc_info.value.code == 1
