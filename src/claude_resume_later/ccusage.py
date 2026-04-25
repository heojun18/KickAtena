from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from . import notify
from .models import TokenBlock
from .paths import CACHE_PATH

CCUSAGE_PIN = "18.0.11"
_CACHE_TTL_SECONDS = 30
_SUBPROCESS_TIMEOUT = 20


@runtime_checkable
class BlockProvider(Protocol):
    def get_active_block(self) -> TokenBlock | None: ...


class CcusageBlockProvider:
    def get_active_block(self) -> TokenBlock | None:
        cached = self._read_cache()
        if cached is not None:
            return cached

        raw = self._call_ccusage()
        if raw is None:
            return None

        block = self._parse(raw)
        if block is not None:
            self._write_cache(raw)
        return block

    def _call_ccusage(self) -> list[dict] | None:
        argv = self._resolve_argv()
        if argv is None:
            notify.warn("ccusage not found; install with: npm i -g ccusage@" + CCUSAGE_PIN)
            return None
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
                check=True,
            )
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            notify.warn("ccusage timed out")
            return None
        except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
            notify.warn(f"ccusage failed: {exc}")
            return None

    def _resolve_argv(self) -> list[str] | None:
        if shutil.which("ccusage"):
            return ["ccusage", "blocks", "-a", "--json"]
        if shutil.which("npx"):
            return ["npx", f"ccusage@{CCUSAGE_PIN}", "blocks", "-a", "--json"]
        return None

    def _parse(self, data: list[dict]) -> TokenBlock | None:
        if not data:
            return None
        try:
            block = data[0]
            return TokenBlock(
                is_active=block["isActive"],
                start_time=_parse_dt(block["startTime"]),
                end_time=_parse_dt(block["endTime"]),
            )
        except (KeyError, ValueError, IndexError) as exc:
            notify.warn(f"ccusage response parse error: {exc}")
            return None

    def _read_cache(self) -> TokenBlock | None:
        if not CACHE_PATH.exists():
            return None
        try:
            mtime = os.path.getmtime(CACHE_PATH)
            if time.time() - mtime > _CACHE_TTL_SECONDS:
                return None
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            return self._parse(raw)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, data: list[dict]) -> None:
        try:
            CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.chmod(CACHE_PATH, 0o600)
        except OSError:
            pass


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
