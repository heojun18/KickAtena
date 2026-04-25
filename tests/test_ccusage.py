from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_resume_later.ccusage import CcusageBlockProvider

FIXTURES = Path(__file__).parent / "fixtures"


class TestCcusageBlockProvider:
    def test_parse_active_block(self) -> None:
        data = json.loads((FIXTURES / "ccusage_active_block.json").read_text())
        provider = CcusageBlockProvider()
        block = provider._parse(data)
        assert block is not None
        assert block.is_active is True
        assert block.start_time == datetime(2026, 4, 25, 6, 0, tzinfo=timezone.utc)
        assert block.end_time == datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc)

    def test_parse_empty(self) -> None:
        data = json.loads((FIXTURES / "ccusage_empty.json").read_text())
        provider = CcusageBlockProvider()
        assert provider._parse(data) is None

    def test_parse_expired(self) -> None:
        data = json.loads((FIXTURES / "ccusage_expired.json").read_text())
        provider = CcusageBlockProvider()
        block = provider._parse(data)
        assert block is not None
        assert block.is_active is False

    def test_parse_malformed(self) -> None:
        data = json.loads((FIXTURES / "ccusage_malformed.json").read_text())
        provider = CcusageBlockProvider()
        assert provider._parse(data) is None

    @patch("claude_resume_later.ccusage.shutil.which", return_value="/usr/bin/ccusage")
    @patch("claude_resume_later.ccusage.subprocess.run")
    def test_call_ccusage_success(self, mock_run: MagicMock, mock_which: MagicMock) -> None:
        fixture = (FIXTURES / "ccusage_active_block.json").read_text()
        mock_run.return_value = MagicMock(stdout=fixture, returncode=0)

        provider = CcusageBlockProvider()
        result = provider._call_ccusage()
        assert result is not None
        assert result[0]["isActive"] is True
        mock_run.assert_called_once()

    @patch("claude_resume_later.ccusage.shutil.which", return_value="/usr/bin/ccusage")
    @patch("claude_resume_later.ccusage.subprocess.run", side_effect=subprocess.TimeoutExpired("ccusage", 20))
    def test_call_ccusage_timeout(self, mock_run: MagicMock, mock_which: MagicMock) -> None:
        provider = CcusageBlockProvider()
        assert provider._call_ccusage() is None

    @patch("claude_resume_later.ccusage.shutil.which", return_value=None)
    def test_ccusage_not_found(self, mock_which: MagicMock) -> None:
        provider = CcusageBlockProvider()
        assert provider._call_ccusage() is None

    @patch("claude_resume_later.ccusage.shutil.which", return_value="/usr/bin/ccusage")
    @patch("claude_resume_later.ccusage.subprocess.run")
    def test_cache_prevents_duplicate_calls(
        self, mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        cache_file = tmp_path / "cache.json"
        fixture = (FIXTURES / "ccusage_active_block.json").read_text()
        mock_run.return_value = MagicMock(stdout=fixture, returncode=0)

        provider = CcusageBlockProvider()

        with patch("claude_resume_later.ccusage.CACHE_PATH", cache_file):
            block1 = provider.get_active_block()
            block2 = provider.get_active_block()

        assert block1 is not None
        assert block2 is not None
        assert mock_run.call_count == 1
