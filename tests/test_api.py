from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from claude_resume_later import api
from claude_resume_later.ccusage import BlockProvider
from claude_resume_later.jobstore import DuplicateSessionError, JobStore
from claude_resume_later.models import FailureKind, Job, JobStatus, PromptRef, TokenBlock
from claude_resume_later.runner import ExecutionResult


def _mock_provider(
    end_time: datetime | None = None,
    is_active: bool = True,
    start_time: datetime | None = None,
) -> Mock:
    provider = Mock(spec=BlockProvider)
    if end_time is None:
        provider.get_active_block.return_value = None
    else:
        provider.get_active_block.return_value = TokenBlock(
            is_active=is_active,
            start_time=start_time or datetime(2026, 4, 25, 6, 0, tzinfo=timezone.utc),
            end_time=end_time,
        )
    return provider


class TestAddJob:
    def test_add_with_session(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        provider = _mock_provider(
            end_time=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
        )

        job = api.add_job(
            session_id="12345678-1234-1234-1234-123456789abc",
            prompt="continue",
            provider=provider,
            store=store,
        )

        assert job.session_id == "12345678-1234-1234-1234-123456789abc"
        assert job.status == JobStatus.PENDING
        assert job.run_after == datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc)

    def test_add_invalid_uuid(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        with pytest.raises(ValueError, match="Invalid session UUID"):
            api.add_job(session_id="not-valid", prompt="hi", provider=_mock_provider(), store=store)

    def test_add_no_session_or_latest(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        with pytest.raises(ValueError, match="--session or --latest"):
            api.add_job(prompt="hi", provider=_mock_provider(), store=store)

    def test_add_nul_in_prompt(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        with pytest.raises(ValueError, match="NUL"):
            api.add_job(
                session_id="12345678-1234-1234-1234-123456789abc",
                prompt="hello\0world",
                provider=_mock_provider(),
                store=store,
            )

    def test_add_both_prompt_and_file(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        with pytest.raises(ValueError, match="not both"):
            api.add_job(
                session_id="12345678-1234-1234-1234-123456789abc",
                prompt="hi",
                prompt_file="/tmp/x.txt",
                provider=_mock_provider(),
                store=store,
            )

    def test_add_with_run_after(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        run_after = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        job = api.add_job(
            session_id="12345678-1234-1234-1234-123456789abc",
            prompt="continue",
            run_after=run_after,
            provider=_mock_provider(),
            store=store,
        )
        assert job.run_after == run_after

    def test_add_ccusage_failure_fallback(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        provider = _mock_provider()  # returns None

        job = api.add_job(
            session_id="12345678-1234-1234-1234-123456789abc",
            prompt="continue",
            provider=provider,
            store=store,
        )
        now = datetime.now(timezone.utc)
        assert job.run_after > now
        assert job.run_after < now + timedelta(hours=2)

    def test_add_large_prompt_stored_as_file(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        large_prompt = "x" * 2000

        with patch("claude_resume_later.api.PROMPTS_DIR", tmp_path / "prompts"):
            (tmp_path / "prompts").mkdir()
            job = api.add_job(
                session_id="12345678-1234-1234-1234-123456789abc",
                prompt=large_prompt,
                run_after=datetime(2026, 5, 1, tzinfo=timezone.utc),
                provider=_mock_provider(),
                store=store,
            )

        assert job.prompt_ref.file is not None
        assert job.prompt_ref.inline is None


class TestListAndGet:
    def test_list_empty(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        assert api.list_jobs(store=store) == []

    def test_get_not_found(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        assert api.get_job("nonexistent", store=store) is None


class TestCancelJob:
    def test_cancel_pending(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        job = api.add_job(
            session_id="12345678-1234-1234-1234-123456789abc",
            prompt="continue",
            run_after=datetime(2026, 5, 1, tzinfo=timezone.utc),
            provider=_mock_provider(),
            store=store,
        )
        api.cancel_job(job.id, store=store)
        assert store.get(job.id) is None

    def test_cancel_running_rejected(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        job = Job(
            id="runjob",
            session_id="12345678-1234-1234-1234-123456789abc",
            prompt_ref=PromptRef(inline="x"),
            cwd="/tmp",
            run_after=datetime(2026, 5, 1, tzinfo=timezone.utc),
            created_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
            status=JobStatus.RUNNING,
        )
        store.save([job])
        with pytest.raises(ValueError, match="Cannot cancel running"):
            api.cancel_job("runjob", store=store)

    def test_cancel_not_found(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        with pytest.raises(ValueError, match="not found"):
            api.cancel_job("nope", store=store)


def _make_due_job(store: JobStore, **overrides) -> Job:
    defaults = dict(
        id="duejob1",
        session_id="12345678-1234-1234-1234-123456789abc",
        prompt_ref=PromptRef(inline="continue"),
        cwd="/tmp",
        run_after=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 4, 25, 6, 0, tzinfo=timezone.utc),
        status=JobStatus.PENDING,
    )
    defaults.update(overrides)
    job = Job(**defaults)
    store.save([job])
    return job


class TestRunDue:
    def test_no_due_jobs(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        provider = _mock_provider(end_time=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc))
        completed, failed = api.run_due(provider=provider, store=store)
        assert completed == 0
        assert failed == 0

    @patch("claude_resume_later.api.runner.execute")
    def test_due_job_executed_on_block_reset(self, mock_execute: Mock, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        _make_due_job(store)

        provider = _mock_provider(
            end_time=datetime(2026, 4, 25, 16, 0, tzinfo=timezone.utc),
            start_time=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
        )
        mock_execute.return_value = ExecutionResult(success=True)

        with patch("claude_resume_later.api.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            completed, failed = api.run_due(provider=provider, store=store)

        assert completed == 1
        assert failed == 0
        updated = store.get("duejob1")
        assert updated is not None
        assert updated.status == JobStatus.COMPLETED

    @patch("claude_resume_later.api.runner.execute")
    def test_due_job_skipped_same_block(self, mock_execute: Mock, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        _make_due_job(store)

        provider = _mock_provider(
            end_time=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
            start_time=datetime(2026, 4, 25, 5, 0, tzinfo=timezone.utc),
        )

        with patch("claude_resume_later.api.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 25, 10, 30, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            completed, failed = api.run_due(provider=provider, store=store)

        assert completed == 0
        assert failed == 0
        mock_execute.assert_not_called()

    def test_due_job_skipped_block_none(self, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        _make_due_job(store)

        provider = _mock_provider()

        with patch("claude_resume_later.api.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            completed, failed = api.run_due(provider=provider, store=store)

        assert completed == 0
        assert failed == 0

    @patch("claude_resume_later.api.runner.execute")
    def test_permanent_failure_sets_failed(self, mock_execute: Mock, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        _make_due_job(store)

        provider = _mock_provider(
            end_time=datetime(2026, 4, 25, 16, 0, tzinfo=timezone.utc),
            start_time=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
        )
        mock_execute.return_value = ExecutionResult(
            success=False, failure_kind=FailureKind.PERMANENT, error="cwd gone",
        )

        with patch("claude_resume_later.api.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            completed, failed = api.run_due(provider=provider, store=store)

        assert completed == 0
        assert failed == 1
        updated = store.get("duejob1")
        assert updated is not None
        assert updated.status == JobStatus.FAILED
        assert updated.last_failure_kind == FailureKind.PERMANENT

    @patch("claude_resume_later.api.runner.execute")
    def test_retriable_failure_reschedules(self, mock_execute: Mock, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        _make_due_job(store)

        provider = _mock_provider(
            end_time=datetime(2026, 4, 25, 16, 0, tzinfo=timezone.utc),
            start_time=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
        )
        mock_execute.return_value = ExecutionResult(
            success=False, failure_kind=FailureKind.RETRIABLE, error="timeout",
        )

        with patch("claude_resume_later.api.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            completed, failed = api.run_due(provider=provider, store=store)

        assert completed == 0
        assert failed == 0
        updated = store.get("duejob1")
        assert updated is not None
        assert updated.status == JobStatus.PENDING
        assert updated.attempts == 1

    @patch("claude_resume_later.api.runner.execute")
    def test_retriable_max_attempts_sets_failed(self, mock_execute: Mock, tmp_path: Path) -> None:
        store = JobStore(queue_path=tmp_path / "q.json", lock_path=tmp_path / ".lock")
        _make_due_job(store, attempts=2)

        provider = _mock_provider(
            end_time=datetime(2026, 4, 25, 16, 0, tzinfo=timezone.utc),
            start_time=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
        )
        mock_execute.return_value = ExecutionResult(
            success=False, failure_kind=FailureKind.RETRIABLE, error="timeout again",
        )

        with patch("claude_resume_later.api.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            completed, failed = api.run_due(provider=provider, store=store)

        assert completed == 0
        assert failed == 1
        updated = store.get("duejob1")
        assert updated is not None
        assert updated.status == JobStatus.FAILED
        assert updated.attempts == 3
