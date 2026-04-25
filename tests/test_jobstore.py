from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from claude_resume_later.jobstore import DuplicateSessionError, JobStore
from claude_resume_later.models import Job, JobStatus, PromptRef


def _make_job(**overrides) -> Job:
    defaults = dict(
        id="aabbccdd",
        session_id="12345678-1234-1234-1234-123456789abc",
        prompt_ref=PromptRef(inline="continue"),
        cwd="/tmp/test",
        run_after=datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 4, 25, 9, 0, tzinfo=timezone.utc),
        status=JobStatus.PENDING,
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(
        queue_path=tmp_path / "queue.json",
        lock_path=tmp_path / ".lock",
    )


class TestJobStore:
    def test_add_and_load(self, store: JobStore) -> None:
        job = _make_job()
        store.add(job)
        jobs = store.load()
        assert len(jobs) == 1
        assert jobs[0].id == "aabbccdd"
        assert jobs[0].session_id == "12345678-1234-1234-1234-123456789abc"

    def test_duplicate_session_rejected(self, store: JobStore) -> None:
        store.add(_make_job(id="job1"))
        with pytest.raises(DuplicateSessionError):
            store.add(_make_job(id="job2"))

    def test_duplicate_allowed_after_completed(self, store: JobStore) -> None:
        job = _make_job(id="job1", status=JobStatus.COMPLETED)
        store.save([job])
        store.add(_make_job(id="job2"))
        assert len(store.load()) == 2

    def test_remove(self, store: JobStore) -> None:
        store.add(_make_job(id="job1"))
        assert store.remove("job1") is True
        assert store.load() == []
        assert store.remove("nonexistent") is False

    def test_update(self, store: JobStore) -> None:
        job = _make_job()
        store.add(job)
        job.status = JobStatus.RUNNING
        store.update(job)
        loaded = store.get("aabbccdd")
        assert loaded is not None
        assert loaded.status == JobStatus.RUNNING

    def test_corrupt_json_backup(self, store: JobStore) -> None:
        store._queue_path.write_text("{broken", encoding="utf-8")
        jobs = store.load()
        assert jobs == []
        backups = list(store._queue_path.parent.glob("*.corrupt-*"))
        assert len(backups) == 1

    def test_version_mismatch_backup(self, store: JobStore) -> None:
        store._queue_path.write_text(
            json.dumps({"version": 999, "jobs": []}),
            encoding="utf-8",
        )
        jobs = store.load()
        assert jobs == []

    def test_atomic_write(self, store: JobStore) -> None:
        store.add(_make_job(id="job1"))
        raw = store._queue_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["version"] == 1
        assert len(data["jobs"]) == 1

    def test_concurrent_add(self, store: JobStore) -> None:
        errors: list[Exception] = []

        def add_job(job_id: str, session_id: str) -> None:
            try:
                store.add(_make_job(id=job_id, session_id=session_id))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=add_job, args=(f"j{i}", f"{i:08d}-1234-1234-1234-123456789abc"))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(store.load()) == 5

    def test_cleanup_old(self, store: JobStore) -> None:
        old_job = _make_job(
            id="old",
            status=JobStatus.COMPLETED,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        recent_job = _make_job(
            id="recent",
            status=JobStatus.COMPLETED,
            created_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
        )
        pending_job = _make_job(id="pending", session_id="aaaaaaaa-1234-1234-1234-123456789abc")
        store.save([old_job, recent_job, pending_job])
        removed = store.cleanup_old(max_age_days=7)
        assert removed == 1
        remaining = store.load()
        assert len(remaining) == 2
        assert {j.id for j in remaining} == {"recent", "pending"}

    def test_list_jobs_filter(self, store: JobStore) -> None:
        store.save([
            _make_job(id="p1", status=JobStatus.PENDING),
            _make_job(id="c1", status=JobStatus.COMPLETED, session_id="aaaaaaaa-1234-1234-1234-123456789abc"),
        ])
        pending = store.list_jobs(JobStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == "p1"

    def test_file_permissions(self, store: JobStore) -> None:
        store.add(_make_job())
        mode = store._queue_path.stat().st_mode & 0o777
        assert mode == 0o600
