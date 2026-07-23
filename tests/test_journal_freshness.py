"""Freshness and Git-noise regressions for the Hermes journal collector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from prismatic.journal import extract_log_signals, git, read_recent_text


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
    log = tmp_path / "gateway.log"
    log.write_text("2026-07-20 06:20:00 ERROR historical failure\n")
    _freeze_now(monkeypatch, datetime(2026, 7, 23, 6, 20, tzinfo=timezone.utc))

    assert extract_log_signals(log) == []


def test_git_suppresses_non_repository_stderr(tmp_path: Path) -> None:
    assert git(tmp_path, ["status", "--short"]) == ""


def _freeze_now(monkeypatch, frozen_now: datetime) -> None:
    import prismatic.journal as journal

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    monkeypatch.setattr(journal.dt, "datetime", FrozenDateTime)


def test_extract_log_signals_normalizes_z_and_numeric_offsets(
    monkeypatch, tmp_path: Path
) -> None:
    now = datetime(2026, 7, 23, 6, 20, tzinfo=timezone.utc)
    _freeze_now(monkeypatch, now)
    log = tmp_path / "offsets.log"
    log.write_text(
        "2026-07-23T06:20:00Z ERROR z\n"
        "2026-07-23T08:20:00+02:00 ERROR positive\n"
        "2026-07-23T01:20:00-05:00 ERROR negative\n"
    )

    signals = extract_log_signals(log)

    assert signals[0]["count"] == 3


def test_extract_log_signals_includes_exact_bounds_and_excludes_microseconds(
    monkeypatch, tmp_path: Path
) -> None:
    now = datetime(2026, 7, 23, 6, 20, tzinfo=timezone.utc)
    _freeze_now(monkeypatch, now)
    log = tmp_path / "bounds.log"
    lower = now - timedelta(hours=24)
    upper = now + timedelta(minutes=5)
    log.write_text(
        f"{lower.isoformat()} ERROR lower included\n"
        f"{(lower - timedelta(microseconds=1)).isoformat()} ERROR lower excluded\n"
        f"{upper.isoformat()} ERROR upper included\n"
        f"{(upper + timedelta(microseconds=1)).isoformat()} ERROR upper excluded\n"
    )

    signals = extract_log_signals(log)

    assert signals[0]["count"] == 2
    assert signals[0]["latest"] == [
        f"{lower.isoformat()} ERROR lower included",
        f"{upper.isoformat()} ERROR upper included",
    ]


def test_read_recent_text_returns_complete_line_suffix_with_small_and_exact_budgets(
    tmp_path: Path,
) -> None:
    log = tmp_path / "tail.log"
    log.write_text("old\nnew\n")

    assert read_recent_text(log, limit=3) == ""
    assert read_recent_text(log, limit=4) == "new\n"
    assert read_recent_text(log, limit=8) == "old\nnew\n"


def test_read_recent_text_skips_unterminated_tail_and_oversized_complete_line(
    tmp_path: Path,
) -> None:
    log = tmp_path / "tail.log"
    log.write_text("small\n" + "x" * 20 + "\ntrailing")

    assert read_recent_text(log, limit=10) == ""
    assert read_recent_text(log, limit=30) == "small\n" + "x" * 20 + "\n"


def test_read_recent_text_excludes_ascii_unterminated_only_content(
    tmp_path: Path,
) -> None:
    log = tmp_path / "unterminated-ascii.log"
    log.write_bytes(b"unterminated")

    assert read_recent_text(log) == ""


def test_read_recent_text_excludes_multibyte_unterminated_only_content(
    tmp_path: Path,
) -> None:
    log = tmp_path / "unterminated-utf8.log"
    log.write_bytes("漢字🙂".encode())

    assert read_recent_text(log) == ""


def test_read_recent_text_preserves_multibyte_complete_boundaries(
    tmp_path: Path,
) -> None:
    log = tmp_path / "utf8.log"
    log.write_text("before\n漢字\n🙂\npartial🙂")

    assert read_recent_text(log, limit=3) == "🙂\n"
    assert read_recent_text(log, limit=5) == "漢字\n🙂\n"


def test_git_returns_stdout_only_when_successful(monkeypatch, tmp_path: Path) -> None:
    import prismatic.journal as journal

    class Result:
        returncode = 0
        stdout = "useful output\n"
        stderr = "warning that must not become telemetry\n"

    monkeypatch.setattr(journal.subprocess, "run", lambda *args, **kwargs: Result())

    assert git(tmp_path, ["status", "--short"]) == "useful output"


def test_git_preserves_failed_command_behavior(monkeypatch, tmp_path: Path) -> None:
    import prismatic.journal as journal

    class Result:
        returncode = 1
        stdout = "partial output\n"
        stderr = "failure\n"

    monkeypatch.setattr(journal.subprocess, "run", lambda *args, **kwargs: Result())

    assert git(tmp_path, ["status", "--short"]) == ""
