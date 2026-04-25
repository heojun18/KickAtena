from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import api
from .ccusage import CcusageBlockProvider
from .jobstore import DuplicateSessionError, JobStore
from .models import JobStatus
from .paths import LOG_DIR


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="claude-resume-later",
        description="Schedule Claude Code session resumption after token block resets",
    )
    sub = parser.add_subparsers(dest="command")

    # add
    p_add = sub.add_parser("add", help="Register a job to resume later")
    session_group = p_add.add_mutually_exclusive_group(required=True)
    session_group.add_argument("--session", help="Session UUID to resume")
    session_group.add_argument("--latest", action="store_true", help="Auto-select latest session for current cwd")
    prompt_group = p_add.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Prompt text to send on resume")
    prompt_group.add_argument("--prompt-file", help="File containing the prompt")
    p_add.add_argument("--run-after", help="ISO8601 datetime to run after (debug/E2E)")

    # list
    p_list = sub.add_parser("list", help="List jobs")
    p_list.add_argument("--status", choices=[s.value for s in JobStatus], help="Filter by status")
    p_list.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

    # status
    p_status = sub.add_parser("status", help="Show job details")
    p_status.add_argument("job_id", help="Job ID")
    p_status.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a pending job")
    p_cancel.add_argument("job_id", help="Job ID")

    # run-due
    sub.add_parser("run-due", help="Execute all due jobs (called by systemd timer)")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "add":
            _cmd_add(args)
        elif args.command == "list":
            _cmd_list(args)
        elif args.command == "status":
            _cmd_status(args)
        elif args.command == "cancel":
            _cmd_cancel(args)
        elif args.command == "run-due":
            _cmd_run_due()
    except (ValueError, FileNotFoundError, DuplicateSessionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_add(args: argparse.Namespace) -> None:
    run_after = None
    if args.run_after:
        run_after = datetime.fromisoformat(args.run_after.replace("Z", "+00:00"))

    provider = CcusageBlockProvider()
    job = api.add_job(
        session_id=args.session,
        latest=args.latest,
        prompt=args.prompt,
        prompt_file=args.prompt_file,
        run_after=run_after,
        provider=provider,
    )
    print(f"Job {job.id} added. Session: {job.session_id}, run_after: {job.run_after.isoformat()}")


def _cmd_list(args: argparse.Namespace) -> None:
    status = JobStatus(args.status) if args.status else None
    jobs = api.list_jobs(status=status)

    if args.as_json:
        print(json.dumps({"jobs": [j.to_dict() for j in jobs]}, ensure_ascii=False, indent=2))
        return

    if not jobs:
        print("No jobs found.")
        return

    for j in jobs:
        print(f"  {j.id}  {j.status:<10}  session={j.session_id[:8]}...  run_after={j.run_after.isoformat()}  cwd={j.cwd}")


def _cmd_status(args: argparse.Namespace) -> None:
    job = api.get_job(args.job_id)
    if job is None:
        print(f"Job not found: {args.job_id}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        print(json.dumps(job.to_dict(), ensure_ascii=False, indent=2))
        return

    print(f"Job:       {job.id}")
    print(f"Session:   {job.session_id}")
    print(f"Status:    {job.status}")
    print(f"CWD:       {job.cwd}")
    print(f"Run after: {job.run_after.isoformat()}")
    print(f"Created:   {job.created_at.isoformat()}")
    print(f"Attempts:  {job.attempts}")
    if job.last_error:
        print(f"Error:     {job.last_error}")
    if job.last_failure_kind:
        print(f"Failure:   {job.last_failure_kind}")

    log_path = LOG_DIR / f"{job.id}.log"
    if log_path.exists():
        print(f"\n--- Last 20 lines of log ({log_path}) ---")
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-20:]:
            print(f"  {line}")


def _cmd_cancel(args: argparse.Namespace) -> None:
    api.cancel_job(args.job_id)
    print(f"Job {args.job_id} cancelled.")


def _cmd_run_due() -> None:
    import fcntl
    from .paths import LOCK_PATH, ensure_runtime_dirs

    ensure_runtime_dirs()

    try:
        lock_fd = open(LOCK_PATH, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        print("Another run-due instance is running, exiting.", file=sys.stderr)
        sys.exit(0)

    _warn_nfs()

    try:
        provider = CcusageBlockProvider()
        completed, failed = api.run_due(provider=provider)
    finally:
        lock_fd.close()


def _warn_nfs() -> None:
    import os
    from . import notify
    from .paths import STATE_DIR
    try:
        mount_info = open("/proc/mounts", encoding="utf-8").read()
        state_str = str(STATE_DIR)
        for line in mount_info.splitlines():
            parts = line.split()
            if len(parts) >= 3 and state_str.startswith(parts[1]) and parts[2] == "nfs":
                notify.warn("State directory appears to be on NFS — flock may not be reliable")
                break
    except OSError:
        pass
