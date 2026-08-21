"""Freshness and Git-noise regressions for the Hermes journal collector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import random

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


def test_read_recent_text_ignores_malformed_bytes_in_older_complete_line(
    tmp_path: Path,
) -> None:
    log = tmp_path / "invalid-older.log"
    log.write_bytes(b"bad\xff\nvalid\n")

    assert read_recent_text(log) == "bad\nvalid\n"


def test_read_recent_text_ignores_malformed_bytes_in_newest_complete_line(
    tmp_path: Path,
) -> None:
    log = tmp_path / "invalid-newest.log"
    log.write_bytes(b"valid\nbad\xff\n")

    assert read_recent_text(log) == "valid\nbad\n"


def test_read_recent_text_expands_past_exact_and_long_partial_tails(
    tmp_path: Path,
) -> None:
    for limit in (3, 4, 6, 10, 20, 41):
        for tail in (b"x" * (limit * 4), b"\xff" * (limit * 4), b"x" * (limit * 8 + 1)):
            log = tmp_path / f"exact-window-{limit}-{tail[:1].hex()}-{len(tail)}.log"
            log.write_bytes(b"ok\n" + tail)
            assert read_recent_text(log, limit=limit) == "ok\n"


def test_read_recent_text_expands_to_newest_suffix_before_partial_tail(
    tmp_path: Path,
) -> None:
    log = tmp_path / "multi-before-tail.log"
    log.write_bytes(b"old\nnewest\n\n" + b"x" * 80)

    assert read_recent_text(log, limit=1) == "\n"
    assert read_recent_text(log, limit=8) == "newest\n\n"


def test_read_recent_text_expands_multibyte_records_before_malformed_tail(
    tmp_path: Path,
) -> None:
    log = tmp_path / "multibyte-before-tail.log"
    log.write_bytes("old\n漢字\n🙂\n".encode() + b"\xff" * 80)

    assert read_recent_text(log, limit=3) == "🙂\n"
    assert read_recent_text(log, limit=6) == "漢字\n🙂\n"


def _recent_text_oracle(data: bytes, limit: int) -> str:
    if limit <= 0 or not data.endswith(b"\n"):
        data = data.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in data and limit > 0 else b""
    lines = data.decode("utf-8", errors="ignore").splitlines(keepends=True)
    suffix: list[str] = []
    length = 0
    for line in reversed(lines):
        if length + len(line) > limit:
            break
        suffix.append(line)
        length += len(line)
    return "".join(reversed(suffix))


def test_read_recent_text_seeded_byte_tail_oracle(tmp_path: Path) -> None:
    randomizer = random.Random(4186)
    complete_records = [
        b"ok\n",
        b"new\n",
        b"\n",
        b"bad\xff\n",
        "漢字\n".encode(),
        "🙂\n".encode(),
    ]
    case_count = 600
    for case in range(case_count):
        records = b"".join(
            randomizer.choice(complete_records)
            for _ in range(randomizer.randrange(1, 7))
        )
        tail = bytes(
            randomizer.choice((ord("x"), 0xFF, 0x80))
            for _ in range(randomizer.randrange(0, 180))
        )
        data = records + tail
        limit = randomizer.choice((0, 1, 2, 3, 4, 6, 10, 20, 41, 80))
        log = tmp_path / f"oracle-{case}.log"
        log.write_bytes(data)

        actual = read_recent_text(log, limit=limit)
        expected = _recent_text_oracle(data, limit)

        assert actual == expected
        assert len(actual) <= max(limit, 0)
        assert not actual or actual.endswith("\n")


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
