from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Any
import uuid


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureKind(StrEnum):
    RETRIABLE = "retriable"
    PERMANENT = "permanent"


@dataclass
class PromptRef:
    inline: str | None = None
    file: str | None = None

    def resolve(self) -> str:
        if self.inline is not None:
            return self.inline
        if self.file is not None:
            with open(self.file, encoding="utf-8") as f:
                return f.read()
        raise ValueError("PromptRef has neither inline nor file")

    def to_dict(self) -> dict[str, str]:
        if self.inline is not None:
            return {"inline": self.inline}
        if self.file is not None:
            return {"file": self.file}
        raise ValueError("PromptRef has neither inline nor file")

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> PromptRef:
        return cls(inline=d.get("inline"), file=d.get("file"))


@dataclass
class Job:
    id: str
    session_id: str
    prompt_ref: PromptRef
    cwd: str
    run_after: datetime
    created_at: datetime
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    last_failure_kind: FailureKind | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "session_id": self.session_id,
            "prompt_ref": self.prompt_ref.to_dict(),
            "cwd": self.cwd,
            "run_after": self.run_after.isoformat(),
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "last_failure_kind": self.last_failure_kind.value if self.last_failure_kind else None,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            prompt_ref=PromptRef.from_dict(d["prompt_ref"]),
            cwd=d["cwd"],
            run_after=_parse_dt(d["run_after"]),
            created_at=_parse_dt(d["created_at"]),
            status=JobStatus(d["status"]),
            attempts=d.get("attempts", 0),
            last_error=d.get("last_error"),
            last_failure_kind=FailureKind(d["last_failure_kind"]) if d.get("last_failure_kind") else None,
        )


@dataclass(frozen=True)
class TokenBlock:
    is_active: bool
    start_time: datetime
    end_time: datetime


def new_job_id() -> str:
    return uuid.uuid4().hex[:8]


def _parse_dt(s: str) -> datetime:
    # Z → +00:00 for fromisoformat compatibility
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
