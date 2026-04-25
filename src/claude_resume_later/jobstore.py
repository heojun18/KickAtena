from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import notify
from .models import Job, JobStatus
from .paths import LOCK_PATH, LOG_DIR, QUEUE_PATH, ensure_runtime_dirs

QUEUE_VERSION = 1


class DuplicateSessionError(Exception):
    pass


class JobStore:
    def __init__(self, queue_path: Path | None = None, lock_path: Path | None = None) -> None:
        self._queue_path = queue_path or QUEUE_PATH
        self._lock_path = lock_path or LOCK_PATH

    @contextmanager
    def _lock(self) -> Iterator[None]:
        ensure_runtime_dirs()
        fd = os.open(str(self._lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def load(self) -> list[Job]:
        if not self._queue_path.exists():
            return []
        try:
            raw = self._queue_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if data.get("version") != QUEUE_VERSION:
                self._backup_corrupt("version_mismatch")
                return []
            return [Job.from_dict(j) for j in data["jobs"]]
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            self._backup_corrupt(str(type(exc).__name__))
            return []

    def save(self, jobs: list[Job]) -> None:
        ensure_runtime_dirs()
        data = {
            "version": QUEUE_VERSION,
            "jobs": [j.to_dict() for j in jobs],
        }
        content = json.dumps(data, ensure_ascii=False, indent=2)
        dir_path = self._queue_path.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
            os.fchmod(fd, 0o600)
            os.close(fd)
            os.replace(tmp_path, str(self._queue_path))
        except BaseException:
            os.close(fd) if not _fd_closed(fd) else None
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def add(self, job: Job) -> None:
        with self._lock():
            jobs = self.load()
            for existing in jobs:
                if (
                    existing.session_id == job.session_id
                    and existing.status in (JobStatus.PENDING, JobStatus.RUNNING)
                ):
                    raise DuplicateSessionError(
                        f"Session {job.session_id} already has a {existing.status} job (id={existing.id})"
                    )
            jobs.append(job)
            self.save(jobs)

    def update(self, job: Job) -> None:
        with self._lock():
            jobs = self.load()
            for i, j in enumerate(jobs):
                if j.id == job.id:
                    jobs[i] = job
                    break
            self.save(jobs)

    def remove(self, job_id: str) -> bool:
        with self._lock():
            jobs = self.load()
            before = len(jobs)
            jobs = [j for j in jobs if j.id != job_id]
            if len(jobs) < before:
                self.save(jobs)
                return True
            return False

    def get(self, job_id: str) -> Job | None:
        jobs = self.load()
        for j in jobs:
            if j.id == job_id:
                return j
        return None

    def list_jobs(self, status: JobStatus | None = None) -> list[Job]:
        jobs = self.load()
        if status is not None:
            return [j for j in jobs if j.status == status]
        return jobs

    def cleanup_old(self, max_age_days: int = 7) -> int:
        now = datetime.now(timezone.utc)
        with self._lock():
            jobs = self.load()
            keep = []
            removed = 0
            for j in jobs:
                if j.status == JobStatus.COMPLETED:
                    age = (now - j.created_at).days
                    if age >= max_age_days:
                        removed += 1
                        log_path = LOG_DIR / f"{j.id}.log"
                        try:
                            log_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                        continue
                keep.append(j)
            if removed:
                self.save(keep)
            return removed

    def _backup_corrupt(self, reason: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self._queue_path.with_suffix(f".corrupt-{ts}")
        try:
            self._queue_path.rename(backup)
        except OSError:
            pass
        notify.warn(f"Queue corrupted ({reason}), backed up to {backup}. Starting with empty queue.")


def _fd_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
        return False
    except OSError:
        return True
