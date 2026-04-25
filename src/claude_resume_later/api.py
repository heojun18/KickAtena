from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import notify, runner
from .ccusage import BlockProvider, CcusageBlockProvider
from .jobstore import DuplicateSessionError, JobStore
from .models import FailureKind, Job, JobStatus, PromptRef, new_job_id
from .paths import LOG_DIR, PROMPTS_DIR, ensure_runtime_dirs
from .session_resolver import resolve_latest_session

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PROMPT_INLINE_MAX = 1024


def add_job(
    *,
    session_id: str | None = None,
    latest: bool = False,
    prompt: str | None = None,
    prompt_file: str | None = None,
    run_after: datetime | None = None,
    provider: BlockProvider | None = None,
    store: JobStore | None = None,
) -> Job:
    ensure_runtime_dirs()
    store = store or JobStore()
    provider = provider or CcusageBlockProvider()

    if latest:
        session_id = resolve_latest_session()
    if not session_id:
        raise ValueError("Either --session or --latest is required")
    if not _UUID_RE.match(session_id):
        raise ValueError(f"Invalid session UUID format: {session_id}")

    prompt_text = _resolve_prompt_input(prompt, prompt_file)
    if "\0" in prompt_text:
        raise ValueError("Prompt contains NUL bytes")

    cwd = os.path.realpath(os.path.abspath(os.getcwd()))
    if not os.path.isdir(cwd):
        raise ValueError(f"Current directory does not exist: {cwd}")

    if run_after is None:
        run_after = _determine_run_after(provider)

    job_id = new_job_id()
    prompt_ref = _store_prompt(job_id, prompt_text)

    job = Job(
        id=job_id,
        session_id=session_id,
        prompt_ref=prompt_ref,
        cwd=cwd,
        run_after=run_after,
        created_at=datetime.now(timezone.utc),
    )

    store.add(job)
    return job


def list_jobs(
    status: JobStatus | None = None,
    store: JobStore | None = None,
) -> list[Job]:
    store = store or JobStore()
    return store.list_jobs(status)


def get_job(job_id: str, store: JobStore | None = None) -> Job | None:
    store = store or JobStore()
    return store.get(job_id)


def cancel_job(job_id: str, store: JobStore | None = None) -> None:
    store = store or JobStore()
    job = store.get(job_id)
    if job is None:
        raise ValueError(f"Job not found: {job_id}")
    if job.status == JobStatus.RUNNING:
        raise ValueError(
            f"Cannot cancel running job {job_id}. Wait for completion or kill the PID manually. "
            "Check: journalctl --user -u claude-resume-later"
        )
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        store.remove(job_id)
        return
    store.remove(job_id)


def run_due(
    provider: BlockProvider | None = None,
    store: JobStore | None = None,
) -> tuple[int, int]:
    ensure_runtime_dirs()
    store = store or JobStore()
    provider = provider or CcusageBlockProvider()

    store.cleanup_old()

    now = datetime.now(timezone.utc)
    all_jobs = store.list_jobs()
    due = [j for j in all_jobs if j.status == JobStatus.PENDING and j.run_after <= now]

    if not due:
        return 0, 0

    block = provider.get_active_block()

    completed = 0
    failed = 0

    for job in due:
        if block is None:
            continue
        if not (block.start_time > job.created_at):
            continue

        job.status = JobStatus.RUNNING
        store.update(job)

        result = runner.execute(job, provider)

        if result.success:
            job.status = JobStatus.COMPLETED
            completed += 1
        else:
            job.last_error = result.error
            job.last_failure_kind = result.failure_kind

            if result.failure_kind == FailureKind.PERMANENT:
                job.attempts = runner.MAX_ATTEMPTS
                job.status = JobStatus.FAILED
                notify.fail(job)
                failed += 1
            elif job.attempts + 1 >= runner.MAX_ATTEMPTS:
                job.attempts += 1
                job.status = JobStatus.FAILED
                notify.fail(job)
                failed += 1
            else:
                job.attempts += 1
                job.status = JobStatus.PENDING
                new_run_after = _determine_run_after(provider)
                job.run_after = new_run_after

        store.update(job)

    print(f"[SUMMARY] completed={completed} failed={failed}", file=sys.stderr)
    return completed, failed


def _resolve_prompt_input(prompt: str | None, prompt_file: str | None) -> str:
    if prompt and prompt_file:
        raise ValueError("Specify either --prompt or --prompt-file, not both")
    if prompt:
        return prompt
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8")
    raise ValueError("Either --prompt or --prompt-file is required")


def _store_prompt(job_id: str, text: str) -> PromptRef:
    if len(text.encode("utf-8")) <= _PROMPT_INLINE_MAX:
        return PromptRef(inline=text)
    path = PROMPTS_DIR / f"{job_id}.txt"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    return PromptRef(file=str(path))


def _determine_run_after(provider: BlockProvider) -> datetime:
    block = provider.get_active_block()
    if block is not None and block.is_active:
        return block.end_time
    notify.warn("Could not determine block end time, defaulting to now + 1h")
    return datetime.now(timezone.utc) + timedelta(hours=1)
