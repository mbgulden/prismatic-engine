"""Freshness and Git-noise regressions for the Hermes journal collector."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from prismatic.journal import extract_log_signals, git


def test_extract_log_signals_uses_recent_tail_and_excludes_stale_head(
    monkeypatch, tmp_path: Path
) -> None:
    import prismatic.journal as journal

    frozen_now = datetime(2026, 7, 23, 6, 20, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    monkeypatch.setattr(journal.dt, "datetime", FrozenDateTime)
    log = tmp_path / "gateway.log"
    stale = "2026-06-19 13:32:27 ERROR stale gdrive failure\n"
    filler = "x" * 20000 + "\n"
    fresh = "2026-07-23 06:20:00 ERROR current collector failure\n"
    log.write_text(stale + filler + fresh)

    signals = extract_log_signals(log)

    assert len(signals) == 1
    assert signals[0]["type"] == "log_error"
    assert signals[0]["count"] == 1
    assert signals[0]["latest"] == [fresh.strip()]


def test_extract_log_signals_excludes_out_of_window_timestamp(
    monkeypatch, tmp_path: Path
) -> None:
    import prismatic.journal as journal

    log = tmp_path / "gateway.log"
    log.write_text("2026-07-20 06:20:00 ERROR historical failure\n")
    frozen_now = datetime(2026, 7, 23, 6, 20, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    monkeypatch.setattr(journal.dt, "datetime", FrozenDateTime)
    assert extract_log_signals(log) == []


def test_git_suppresses_non_repository_stderr(tmp_path: Path) -> None:
    assert git(tmp_path, ["status", "--short"]) == ""
