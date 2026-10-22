"""Tests for prismatic.eventlog.aggregator (E1, gap G2, tweaks plan Phase 3).

Fixture DBs are built in tmp_path; the suite checks count reconciliation
against the sources, total ordering, CLI behavior, missing/corrupt source
handling, bad-timestamp dropping, and the read-only proof (source file
checksums unchanged across a full aggregate run).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from prismatic.eventlog.aggregator import (
    SOURCE_STATUS_ERROR,
    SOURCE_STATUS_MISSING,
    SOURCE_STATUS_OK,
    build_feed_lines,
    build_arg_parser,
    collect_admissions,
    collect_evidence,
    collect_event_bus,
    collect_kanban,
    iso_utc,
    main,
    parse_timestamp,
    resolve_source_paths,
    run_specs,
)

KINDS = ("created", "claimed", "completed")

KTS = [1700000000.0, 1700000100.0, 1700000200.0]
BUS_TS = [1700000050.0, 1700000150.0]
ADM_TS = ["2023-11-14T22:13:35Z", "2023-11-14T22:14:00Z"]
EVID_TS = ["2023-11-14T22:13:40+00:00", "2023-11-14T22:14:10+00:00"]


# ── Fixture builders ───────────────────────────────────────────────


def make_kanban_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE task_events ("
        "id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, run_id INTEGER,"
        "kind TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL)"
    )
    for i, ts in enumerate(KTS):
        conn.execute(
            "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?)",
            (i + 1, f"t_{i:04d}", 1, KINDS[i], json.dumps({"n": i}), int(ts)),
        )
    conn.commit()
    conn.close()


def make_bus_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE events ("
        "dedup_key TEXT, topic TEXT, payload_json TEXT, ts REAL, processed INTEGER)"
    )
    for i, ts in enumerate(BUS_TS):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (f"dedup:{i}", "test", json.dumps({"i": i}), ts, 0),
        )
    conn.execute(
        "CREATE TABLE task_admissions ("
        "task_id TEXT, idempotency_key TEXT, payload_sha256 TEXT, payload_json TEXT,"
        "actor TEXT, status TEXT, received_at TEXT, event_id TEXT)"
    )
    for i, ts in enumerate(ADM_TS):
        conn.execute(
            "INSERT INTO task_admissions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"TASK-{i:03d}",
                f"idem:{i}",
                "sha",
                json.dumps({"i": i}),
                "tester",
                "admitted",
                ts,
                None,
            ),
        )
    conn.commit()
    conn.close()


def make_evidence_db(path: Path, session_prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE verification_events ("
        "id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, session_id TEXT NOT NULL,"
        "cwd TEXT NOT NULL, root TEXT NOT NULL, command TEXT NOT NULL,"
        "canonical_command TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,"
        "status TEXT NOT NULL, exit_code INTEGER NOT NULL, output_summary TEXT)"
    )
    for i, ts in enumerate(EVID_TS):
        conn.execute(
            "INSERT INTO verification_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                i + 1,
                ts,
                f"{session_prefix}_{i}",
                "/tmp",
                "/tmp",
                "pytest -q",
                "pytest -q",
                "pytest",
                "all",
                "passed",
                0,
                "ok",
            ),
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def fixture_tree(tmp_path: Path) -> dict[str, Path]:
    kanban = tmp_path / "kanban.db"
    bus = tmp_path / "bus" / "event_log.sqlite"
    profiles = tmp_path / "profiles"
    make_kanban_db(kanban)
    make_bus_db(bus)
    for prof in ("fred", "kai"):
        make_evidence_db(profiles / prof / "verification_evidence.db", f"sess-{prof}")
    return {"kanban": kanban, "bus": bus, "profiles": profiles}


def all_specs(tree: dict[str, Path]):
    from prismatic.eventlog.aggregator import Collector

    def tagged(collect):
        return lambda tag, conn: collect(tag, conn)

    specs: list[tuple[str, Path | None, Collector]] = [
        ("kanban", tree["kanban"], tagged(collect_kanban)),
        ("event_bus", tree["bus"], tagged(collect_event_bus)),
        ("admissions", tree["bus"], tagged(collect_admissions)),
        (
            "evidence:fred",
            tree["profiles"] / "fred" / "verification_evidence.db",
            tagged(collect_evidence),
        ),
        (
            "evidence:kai",
            tree["profiles"] / "kai" / "verification_evidence.db",
            tagged(collect_evidence),
        ),
    ]
    return specs


def dir_checksum(path: Path) -> dict[str, str]:
    """sha256 of every file in a directory (sorted by name)."""
    out: dict[str, str] = {}
    for p in sorted(path.iterdir()):
        if p.is_file():
            out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ── Reconciliation and ordering ────────────────────────────────────


def test_counts_reconcile_against_sources(fixture_tree):
    tree = fixture_tree
    report = run_specs(all_specs(tree))
    manifest = report.build_manifest("2026-01-01T00:00:00Z")
    src = manifest["sources"]
    assert src["kanban"]["count"] == 3
    assert src["event_bus"]["count"] == 2
    assert src["admissions"]["count"] == 2
    assert src["evidence:fred"]["count"] == 2
    assert src["evidence:kai"]["count"] == 2
    assert manifest["total_events"] == 11

    # Independent SQL ground truth (aggregator must agree with the DBs).
    k = (
        sqlite3.connect(tree["kanban"])
        .execute("SELECT COUNT(*) FROM task_events")
        .fetchone()[0]
    )
    b = (
        sqlite3.connect(tree["bus"])
        .execute("SELECT COUNT(*) FROM events")
        .fetchone()[0]
    )
    a = (
        sqlite3.connect(tree["bus"])
        .execute("SELECT COUNT(*) FROM task_admissions")
        .fetchone()[0]
    )
    assert (k, b, a) == (
        src["kanban"]["count"],
        src["event_bus"]["count"],
        src["admissions"]["count"],
    )


def test_feed_is_time_ordered_and_source_tagged(fixture_tree):
    report = run_specs(all_specs(fixture_tree))
    lines = build_feed_lines(report, "2026-01-01T00:00:00Z")
    events = [line for line in lines if line["record_kind"] == "event"]
    assert len(events) == 11
    keys = [(e["ts_epoch"], e["source"], e["seq"]) for e in events]
    assert keys == sorted(keys)
    tags = {e["source"] for e in events}
    assert tags == {
        "kanban",
        "event_bus",
        "admissions",
        "evidence:fred",
        "evidence:kai",
    }
    # The manifest is the last line and matches the event count.
    assert lines[-1]["record_kind"] == "manifest"
    assert lines[-1]["total_events"] == len(events)


def test_fixture_event_bodies_carry_source_fields(fixture_tree):
    report = run_specs(all_specs(fixture_tree))
    by_source = {}
    for e in report.events:
        by_source.setdefault(e.source, []).append(e.record)
    assert by_source["kanban"][0]["kind"] == "created"
    assert by_source["kanban"][0]["task_id"] == "t_0000"
    assert by_source["kanban"][0]["payload"] == {"n": 0}
    assert by_source["event_bus"][0]["topic"] == "test"
    assert by_source["admissions"][0]["task_id"] == "TASK-000"
    assert by_source["admissions"][0]["idempotency_key"] == "idem:0"
    assert by_source["evidence:fred"][0]["status"] == "passed"
    assert by_source["evidence:fred"][0]["exit_code"] == 0
    assert by_source["evidence:kai"][0]["session_id"] == "sess-kai_0"


# ── Read-only proof ────────────────────────────────────────────────


def test_sources_byte_identical_after_run(fixture_tree, tmp_path):
    tree = fixture_tree
    before = {
        "kanban": dir_checksum(tree["kanban"].parent),
        "bus": dir_checksum(tree["bus"].parent),
    }
    # A full aggregate run, twice, must leave every source file untouched
    # (no new -wal/-shm, no checksum drift).
    report = None
    for _ in range(2):
        report = run_specs(all_specs(tree))
        assert not report.failed
    assert report is not None
    # Feed goes to a subdir (dir_checksum only sees files), so the source
    # dirs are compared without the artifact.
    out = tmp_path / "out" / "feed.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = build_feed_lines(report, "2026-01-01T00:00:00Z")
    out.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines),
        encoding="utf-8",
    )
    after_kanban = dir_checksum(tree["kanban"].parent)
    after_bus = dir_checksum(tree["bus"].parent)
    assert after_kanban == before["kanban"]
    assert after_bus == before["bus"]
    assert out.exists()


# ── Source failure modes ───────────────────────────────────────────


def test_missing_source_is_marked_missing_not_fatal(fixture_tree, tmp_path):
    tree = fixture_tree
    tree["kanban"].unlink()
    report = run_specs(all_specs(tree))
    status = {s.tag: s.status for s in report.sources}
    assert status["kanban"] == SOURCE_STATUS_MISSING
    assert report.build_manifest("x")["sources"]["kanban"]["count"] == 0
    # Other sources still fully aggregate (2 bus + 2 adm + 2 + 2 evidence).
    assert report.build_manifest("x")["total_events"] == 8
    assert not report.failed  # missing is not an error


def test_missing_evidence_db_is_missing_not_fatal(fixture_tree):
    tree = fixture_tree
    (tree["profiles"] / "fred" / "verification_evidence.db").unlink()
    report = run_specs(all_specs(tree))
    status = {s.tag: s.status for s in report.sources}
    assert status["evidence:fred"] == SOURCE_STATUS_MISSING
    assert status["evidence:kai"] == SOURCE_STATUS_OK
    assert report.build_manifest("x")["total_events"] == 9
    assert not report.failed


def test_corrupt_source_is_error_and_main_returns_2(fixture_tree, tmp_path):
    tree = fixture_tree
    bad = tree["profiles"] / "fred" / "verification_evidence.db"
    bad.write_bytes(b"this is not a sqlite database at all, just garbage bytes")
    assert (
        main(
            [
                "--kanban-db",
                str(tree["kanban"]),
                "--bus-db",
                str(tree["bus"]),
                "--evidence-root",
                str(tree["profiles"]),
                "-o",
                str(tmp_path / "feed.jsonl"),
            ]
        )
        == 2
    )
    # The feed is still written with the sources that succeeded.
    lines = (tmp_path / "feed.jsonl").read_text().splitlines()
    manifest = json.loads(lines[-1])
    assert manifest["sources"]["evidence:fred"]["status"] == SOURCE_STATUS_ERROR
    assert manifest["sources"]["evidence:fred"]["error"]
    assert manifest["sources"]["kanban"]["status"] == SOURCE_STATUS_OK
    # fred's 2 evidence rows are gone (corrupt source), the rest aggregate:
    # 3 kanban + 2 bus + 2 admissions + 2 evidence:kai = 9.
    assert manifest["total_events"] == 9


def test_bad_timestamp_row_is_dropped_and_counted(fixture_tree, tmp_path):
    tree = fixture_tree
    conn = sqlite3.connect(tree["kanban"])
    conn.execute(
        "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?)",
        (99, "t_bad", None, "created", "{}", "not-a-timestamp"),
    )
    conn.commit()
    conn.close()
    report = run_specs(all_specs(tree))
    kanban = next(s for s in report.sources if s.tag == "kanban")
    assert kanban.status == SOURCE_STATUS_OK
    assert kanban.count == 3  # good rows still in
    assert kanban.dropped == 1
    assert report.build_manifest("x")["total_events"] == 11


# ── Adversarial case: a removed source row must surface in the manifest ─


def test_removed_source_row_reports_count_difference_not_absorbed(fixture_tree):
    tree = fixture_tree
    first = run_specs(all_specs(tree))
    first_count = first.build_manifest("x")["sources"]["event_bus"]["count"]
    assert first_count == 2

    # A writer removes one events row (simulates upstream pruning).
    conn = sqlite3.connect(tree["bus"])
    conn.execute("DELETE FROM events WHERE rowid = 1")
    conn.commit()
    conn.close()

    second = run_specs(all_specs(tree))
    second_count = second.build_manifest("x")["sources"]["event_bus"]["count"]
    assert second_count == 1
    assert first_count - second_count == 1
    # Total feed count tracks it; nothing is silently absorbed.
    assert (
        second.build_manifest("x")["total_events"]
        == first.build_manifest("x")["total_events"] - 1
    )


# ── CLI and helpers ────────────────────────────────────────────────


def test_cli_main_writes_valid_jsonl(fixture_tree, tmp_path):
    tree = fixture_tree
    out = tmp_path / "nested" / "dir" / "feed.jsonl"
    rc = main(
        [
            "--kanban-db",
            str(tree["kanban"]),
            "--bus-db",
            str(tree["bus"]),
            "--evidence-root",
            str(tree["profiles"]),
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 12  # 11 events + manifest
    events = [line for line in lines if line["record_kind"] == "event"]
    manifest = [line for line in lines if line["record_kind"] == "manifest"]
    assert events and manifest
    assert lines[0]["record_kind"] == "event"
    assert lines[-1]["record_kind"] == "manifest"
    for line in events:
        assert {
            "record_kind",
            "source",
            "ts",
            "ts_epoch",
            "seq",
            "record",
        } <= line.keys()
    for m in manifest:
        assert {
            "record_kind",
            "generated_at",
            "order",
            "sources",
            "total_events",
        } <= m.keys()


def test_cli_missing_evidence_root_is_ok(fixture_tree, tmp_path):
    tree = fixture_tree
    out = tmp_path / "feed.jsonl"
    rc = main(
        [
            "--kanban-db",
            str(tree["kanban"]),
            "--bus-db",
            str(tree["bus"]),
            "--evidence-root",
            str(tmp_path / "no-such-profiles"),
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    manifest = json.loads(out.read_text().splitlines()[-1])
    assert manifest["sources"]["evidence"]["status"] == SOURCE_STATUS_MISSING
    assert manifest["total_events"] == 7


def test_parser_defaults_are_none(monkeypatch, tmp_path):
    """The parser keeps explicit flags; defaults resolve in main via resolve_source_paths."""
    monkeypatch.setenv("PRISMATIC_BUS_DB", str(tmp_path / "custom-bus.sqlite"))
    args = build_arg_parser().parse_args([])
    assert args.bus_db is None
    assert args.output is None
    assert args.kanban_db is None


def test_resolve_source_paths_precedence(monkeypatch, tmp_path):
    # Start from a clean environment for every path env var.
    for key in (
        "PRISMATIC_KANBAN_DB",
        "PRISMATIC_BUS_DB",
        "PRISMATIC_EVIDENCE_ROOT",
        "HERMES_KANBAN_DB",
        "HERMES_PROFILES_DIR",
        "HERMES_HOME",
        "HERMES_ROOT",
        "PRISMATIC_HOME",
    ):
        monkeypatch.delenv(key, raising=False)

    # 1. Explicit flags always win.
    flags = resolve_source_paths(tmp_path / "k.db", tmp_path / "b.db", tmp_path / "p")
    assert flags == (tmp_path / "k.db", tmp_path / "b.db", tmp_path / "p")

    # 2. PRISMATIC_* env beats HERMES_* env.
    monkeypatch.setenv("PRISMATIC_KANBAN_DB", str(tmp_path / "env-k.db"))
    monkeypatch.setenv("PRISMATIC_BUS_DB", str(tmp_path / "env-b.db"))
    monkeypatch.setenv("PRISMATIC_EVIDENCE_ROOT", str(tmp_path / "env-p"))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "hermes-k.db"))
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(tmp_path / "hermes-p"))
    got = resolve_source_paths(None, None, None)
    assert got == (tmp_path / "env-k.db", tmp_path / "env-b.db", tmp_path / "env-p")

    # 3. Without PRISMATIC_* the HERMES_* env wins.
    monkeypatch.delenv("PRISMATIC_KANBAN_DB")
    monkeypatch.delenv("PRISMATIC_BUS_DB")
    monkeypatch.delenv("PRISMATIC_EVIDENCE_ROOT")
    got = resolve_source_paths(None, None, None)
    assert got[0] == tmp_path / "hermes-k.db"
    assert got[2] == tmp_path / "hermes-p"

    # 4. HERMES_HOME (no HERMES_ROOT) -> <hermes>/kanban.db and <hermes>/profiles.
    monkeypatch.delenv("HERMES_KANBAN_DB")
    monkeypatch.delenv("HERMES_PROFILES_DIR")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-home"))
    got = resolve_source_paths(None, None, None)
    assert got[0] == tmp_path / "profile-home" / "kanban.db"
    assert got[2] == tmp_path / "profile-home" / "profiles"

    # 5. HERMES_ROOT beats HERMES_HOME (worker contexts set both; the root wins
    #    so the evidence root is the canonical profiles dir, not a nested one).
    monkeypatch.setenv("HERMES_ROOT", str(tmp_path / "real-hermes"))
    got = resolve_source_paths(None, None, None)
    assert got[0] == tmp_path / "real-hermes" / "kanban.db"
    assert got[2] == tmp_path / "real-hermes" / "profiles"

    # 6. Explicit HERMES_PROFILES_DIR always wins, unconditionally.
    explicit = tmp_path / "only-explicit-profiles"
    explicit.mkdir()
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(explicit))
    got = resolve_source_paths(None, None, None)
    assert got[2] == explicit

    # 7. No env at all -> ~/.hermes tree (PRISMATIC_HOME unset for bus).
    monkeypatch.delenv("HERMES_PROFILES_DIR")
    monkeypatch.delenv("HERMES_ROOT")
    monkeypatch.delenv("HERMES_HOME")
    monkeypatch.delenv("PRISMATIC_HOME", raising=False)
    got = resolve_source_paths(None, None, None)
    assert got[0] == Path.home() / ".hermes" / "kanban.db"
    assert got[2] == Path.home() / ".hermes" / "profiles"
    assert got[1] == Path.home() / ".prismatic" / "bus" / "event_log.sqlite"


def test_parse_timestamp_variants():
    assert parse_timestamp(1700000000, "x") == 1700000000.0
    assert parse_timestamp("2023-11-14T22:13:20Z", "x") == 1700000000.0
    assert parse_timestamp("2023-11-14T22:13:20+00:00", "x") == 1700000000.0
    assert parse_timestamp("2023-11-14T22:13:20", "x") == 1700000000.0  # naive = UTC
    with pytest.raises(ValueError):
        parse_timestamp("not-a-time", "x")
    with pytest.raises(ValueError):
        parse_timestamp(True, "x")
    assert iso_utc(1700000000.0) == "2023-11-14T22:13:20Z"
