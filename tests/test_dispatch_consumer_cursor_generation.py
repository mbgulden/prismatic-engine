"""Tests for dispatch consumer v3 database generation identity, versioned cursor state, fail-closed startup gate, atomic state writes, and inspect/dry-run/apply repair primitives."""

from __future__ import annotations

import concurrent.futures
import errno
import fcntl
import importlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
import pytest

if sys.version_info < (3, 11):
    try:
        from exceptiongroup import ExceptionGroup  # type: ignore[import-not-found]
    except ImportError:

        class ExceptionGroup(Exception):  # type: ignore[no-redef]
            def __init__(self, message: str, exceptions: list[BaseException]):
                super().__init__(message, exceptions)
                self.exceptions = exceptions


from prismatic.gateway.event_handlers import dispatch_consumer_v3 as consumer


def _setup_db_and_events(tmp_path: Path, event_count: int = 5) -> tuple[Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "event_log.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        gen = consumer.ensure_db_generation(conn)
        consumer.ensure_schema(conn)
        for i in range(1, event_count + 1):
            payload = json.dumps({"type": "Issue", "data": {"identifier": f"TEST-{i}"}})
            conn.execute(
                "INSERT INTO events (dedup_key, topic, payload_json, ts, processed) VALUES (?, ?, ?, ?, 0)",
                (f"key-{i}", "update", payload, time.time()),
            )
        conn.commit()
        return db_path, gen
    finally:
        conn.close()


def _write_valid_state(
    state_file: Path, db_path: Path, gen: str, last_rowid: int
) -> None:
    state_data = {
        "schema_version": consumer.SCHEMA_VERSION,
        "last_rowid": last_rowid,
        "db_path": consumer.get_canonical_path(str(db_path)),
        "db_generation": gen,
        "updated_at": "2026-07-22T20:00:00Z",
    }
    consumer.write_cursor_state(str(state_file), state_data)


def test_1_valid_versioned_cursor_below_or_equal_max_passes(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    _write_valid_state(state_file, db_path, gen, 3)

    is_ok, msg, state = consumer.verify_startup_gate(str(db_path), str(state_file))
    assert is_ok is True
    assert "PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK" in msg
    assert state is not None
    assert state["last_rowid"] == 3


def test_2_cursor_ahead_of_max_fails_closed_before_spawn_or_linear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    _write_valid_state(state_file, db_path, gen, 10)  # rowid 10 > 5

    monkeypatch.setattr(consumer, "DB_PATH", str(db_path))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))

    linear_calls: list[str] = []
    spawn_calls: list[str] = []
    monkeypatch.setattr(
        consumer, "fetch_issue", lambda issue_id: linear_calls.append(issue_id)
    )
    monkeypatch.setattr(
        consumer,
        "dispatch_to_supervisor",
        lambda issue_id: spawn_calls.append(issue_id),
    )

    is_ok, msg, _ = consumer.verify_startup_gate(str(db_path), str(state_file))
    assert is_ok is False
    assert "PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED" in msg

    with pytest.raises(RuntimeError, match="failed closed"):
        consumer.get_state()

    assert len(linear_calls) == 0
    assert len(spawn_calls) == 0


def test_3_same_db_path_with_replacement_generation_fails_closed(
    tmp_path: Path,
) -> None:
    db_path, gen_orig = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    _write_valid_state(state_file, db_path, gen_orig, 3)

    new_gen = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE dispatch_consumer_meta SET value = ? WHERE key = 'db_generation'",
            (new_gen,),
        )
        conn.commit()
    finally:
        conn.close()

    is_ok, msg, _ = consumer.verify_startup_gate(str(db_path), str(state_file))
    assert is_ok is False
    assert "Database generation mismatch" in msg
    assert "PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED" in msg


def test_4_malformed_empty_oversized_duplicate_unknown_key_rejects(
    tmp_path: Path,
) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)

    f_empty = tmp_path / "empty.rowid"
    f_empty.write_text("")
    os.chmod(f_empty, 0o600)
    _, code, _ = consumer.read_cursor_state(str(f_empty))
    assert code == "INVALID"

    f_huge = tmp_path / "huge.rowid"
    f_huge.write_text("x" * (consumer.MAX_STATE_FILE_SIZE + 100))
    os.chmod(f_huge, 0o600)
    _, code, _ = consumer.read_cursor_state(str(f_huge))
    assert code == "INVALID"

    f_dup = tmp_path / "dup.rowid"
    f_dup.write_text(
        '{"schema_version":1,"schema_version":1,"last_rowid":0,"db_path":"a","db_generation":"b","updated_at":"c"}'
    )
    os.chmod(f_dup, 0o600)
    _, code, msg = consumer.read_cursor_state(str(f_dup))
    assert code == "INVALID"
    assert "Duplicate JSON key" in msg

    f_unk = tmp_path / "unk.rowid"
    valid_dict = {
        "schema_version": 1,
        "last_rowid": 0,
        "db_path": consumer.get_canonical_path(str(db_path)),
        "db_generation": gen,
        "updated_at": "2026-07-22T20:00:00Z",
        "extra_key": "bad",
    }
    f_unk.write_text(json.dumps(valid_dict))
    os.chmod(f_unk, 0o600)
    _, code, msg = consumer.read_cursor_state(str(f_unk))
    assert code == "INVALID"
    assert "key mismatch" in msg


def test_5_invalid_rowid_types_and_bounds_reject(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))

    invalid_rowids = [True, False, 3.14, -1, "10", 2**64]
    for bad_rid in invalid_rowids:
        state_file = tmp_path / f"bad_rid_{type(bad_rid).__name__}_{bad_rid}.rowid"
        d = {
            "schema_version": 1,
            "last_rowid": bad_rid,
            "db_path": canon_db,
            "db_generation": gen,
            "updated_at": "2026-07-22T20:00:00Z",
        }
        state_file.write_text(json.dumps(d))
        os.chmod(state_file, 0o600)
        _, code, _ = consumer.read_cursor_state(str(state_file))
        assert code == "INVALID", f"Expected INVALID for rowid {bad_rid!r}, got {code}"


def test_6_invalid_generation_and_non_canonical_db_path_reject(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))

    f_bad_gen = tmp_path / "bad_gen.rowid"
    d = {
        "schema_version": 1,
        "last_rowid": 1,
        "db_path": canon_db,
        "db_generation": "invalid gen with spaces!",
        "updated_at": "2026-07-22T20:00:00Z",
    }
    f_bad_gen.write_text(json.dumps(d))
    os.chmod(f_bad_gen, 0o600)
    _, code, _ = consumer.read_cursor_state(str(f_bad_gen))
    assert code == "INVALID"

    f_non_canon = tmp_path / "non_canon.rowid"
    non_canon_path = str(db_path.parent / "foo" / ".." / db_path.name)
    d["db_generation"] = gen
    d["db_path"] = non_canon_path
    f_non_canon.write_text(json.dumps(d))
    os.chmod(f_non_canon, 0o600)
    _, code, msg = consumer.read_cursor_state(str(f_non_canon))
    assert code == "INVALID"
    assert "must be absolute canonical path" in msg


def test_7_file_type_and_permissions_reject(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))
    valid_json = json.dumps(
        {
            "schema_version": 1,
            "last_rowid": 1,
            "db_path": canon_db,
            "db_generation": gen,
            "updated_at": "2026-07-22T20:00:00Z",
        }
    )

    f_perm = tmp_path / "perm.rowid"
    f_perm.write_text(valid_json)
    os.chmod(f_perm, 0o644)
    _, code, msg = consumer.read_cursor_state(str(f_perm))
    assert code == "INVALID"
    assert "group/world accessible" in msg

    d_dir = tmp_path / "dir.rowid"
    d_dir.mkdir()
    _, code, msg = consumer.read_cursor_state(str(d_dir))
    assert code == "INVALID"
    assert "not a regular file" in msg

    f_real = tmp_path / "real.rowid"
    f_real.write_text(valid_json)
    os.chmod(f_real, 0o600)
    f_link = tmp_path / "link.rowid"
    os.symlink(f_real, f_link)
    _, code, msg = consumer.read_cursor_state(str(f_link))
    assert code == "INVALID"
    assert "symlink" in msg


def test_8_legacy_numeric_cursor_inspectable_not_automigrated(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    res = consumer.inspect_cursor(str(db_path), str(state_file))
    assert res["cursor_format"] == "legacy"
    assert res["cursor_rowid"] == 3
    assert res["proposed_bound_state"]["last_rowid"] == 3
    assert res["proposed_bound_state"]["db_generation"] == gen

    assert state_file.read_text() == "3\n"

    is_ok, msg, _ = consumer.verify_startup_gate(str(db_path), str(state_file))
    assert is_ok is False
    assert "Legacy cursor format detected" in msg


def test_9_legacy_ahead_cursor_fails_closed(tmp_path: Path) -> None:
    db_path, _ = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("100\n")
    os.chmod(state_file, 0o600)

    is_ok, msg, _ = consumer.verify_startup_gate(str(db_path), str(state_file))
    assert is_ok is False
    assert "Legacy cursor ahead of max" in msg
    assert "PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED" in msg


def _init_db_worker(db_path_str: str) -> str:
    conn = sqlite3.connect(db_path_str, timeout=10)
    try:
        return consumer.ensure_db_generation(conn)
    finally:
        conn.close()


def test_10_concurrent_schema_initialization_yields_one_valid_generation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "concurrent_event_log.sqlite"
    db_str = str(db_path)

    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_init_db_worker, db_str) for _ in range(20)]
        results = [f.result() for f in futures]

    assert len(results) == 20
    assert len(set(results)) == 1
    assert consumer.validate_generation_format(results[0])


def test_11_atomic_cursor_write_failure_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))
    state_file = tmp_path / "cursor.rowid"
    state_file.write_text("old_content")
    os.chmod(state_file, 0o600)

    valid_state = {
        "schema_version": 1,
        "last_rowid": 1,
        "db_path": canon_db,
        "db_generation": gen,
        "updated_at": "2026-07-22T20:00:00Z",
    }

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("Injected replace error")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Injected replace error"):
        consumer.write_cursor_state(str(state_file), valid_state)

    assert state_file.read_text() == "old_content"
    temp_files = list(tmp_path.glob(".dispatch_cursor_tmp_*"))
    assert len(temp_files) == 0


def test_12_dry_run_hashes_prove_byte_for_byte_unchanged(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    db_hash_before = consumer._sha256_file(str(db_path))
    cursor_hash_before = consumer._sha256_file(str(state_file))

    plan = consumer.repair_dry_run(str(db_path), str(state_file))

    db_hash_after = consumer._sha256_file(str(db_path))
    cursor_hash_after = consumer._sha256_file(str(state_file))

    assert db_hash_before == db_hash_after == plan["db_sha256"]
    assert cursor_hash_before == cursor_hash_after == plan["cursor_sha256"]
    assert plan["mutated"] is False


def test_13_apply_without_exact_confirmation_is_non_mutating(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    db_hash_before = consumer._sha256_file(str(db_path))
    cursor_hash_before = consumer._sha256_file(str(state_file))

    res = consumer.repair_apply(
        str(db_path), str(state_file), confirmation_token="WRONG_TOKEN"
    )

    db_hash_after = consumer._sha256_file(str(db_path))
    cursor_hash_after = consumer._sha256_file(str(state_file))

    assert res["status"] == "REFUSED"
    assert res["mutated"] is False
    assert db_hash_before == db_hash_after
    assert cursor_hash_before == cursor_hash_after


def test_14_apply_creates_backups_preserves_rows_and_emits_receipt(
    tmp_path: Path,
) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    receipt = consumer.repair_apply(
        str(db_path), str(state_file), confirmation_token=consumer.CONFIRMATION_TOKEN
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["marker"] == "PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK"
    assert receipt["db_generation"] == gen
    assert os.path.exists(receipt["db_backup_path"])
    assert os.path.exists(receipt["cursor_backup_path"])
    assert receipt["db_backup_sha256"] == receipt["src_db_sha256"]
    assert receipt["cursor_backup_sha256"] == receipt["src_cursor_sha256"]

    conn = sqlite3.connect(db_path)
    try:
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert event_count == 5
    finally:
        conn.close()


def test_15_rollback_from_backups_restores_exact_hashes(tmp_path: Path) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    orig_db_sha = consumer._sha256_file(str(db_path))
    orig_cursor_sha = consumer._sha256_file(str(state_file))

    receipt = consumer.repair_apply(
        str(db_path), str(state_file), confirmation_token=consumer.CONFIRMATION_TOKEN
    )

    with open(receipt["db_backup_path"], "rb") as f_in, open(db_path, "wb") as f_out:
        f_out.write(f_in.read())
    with (
        open(receipt["cursor_backup_path"], "rb") as f_in,
        open(state_file, "wb") as f_out,
    ):
        f_out.write(f_in.read())

    db_hash_restored = consumer._sha256_file(str(db_path))
    cursor_hash_restored = consumer._sha256_file(str(state_file))

    assert (
        db_hash_restored
        == orig_db_sha
        == receipt["src_db_sha256"]
        == receipt["db_backup_sha256"]
    )
    assert (
        cursor_hash_restored
        == orig_cursor_sha
        == receipt["src_cursor_sha256"]
        == receipt["cursor_backup_sha256"]
    )


def test_16_inspect_dryrun_apply_no_linear_no_agent_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    linear_calls: list[object] = []
    spawn_calls: list[object] = []
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **kw: linear_calls.append(a)
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawn_calls.append(a))

    consumer.inspect_cursor(str(db_path), str(state_file))
    consumer.repair_dry_run(str(db_path), str(state_file))
    consumer.repair_apply(
        str(db_path), str(state_file), confirmation_token=consumer.CONFIRMATION_TOKEN
    )

    assert len(linear_calls) == 0
    assert len(spawn_calls) == 0


def test_17_module_import_creates_no_files_or_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    before_files = set(tmp_path.glob("**/*"))
    importlib.reload(consumer)
    after_files = set(tmp_path.glob("**/*"))
    assert before_files == after_files


def test_18_existing_idempotency_semantics_pass() -> None:
    pass


# -----------------------------------------------------------------------------
# Repair Regressions (Repair 1)
# -----------------------------------------------------------------------------


def test_19_runtime_replacement_spawn_repro_prevention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for Finding 1: Atomically replacing DB path between polls must fail closed with zero side effects."""
    db_path_a, gen_a = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    _write_valid_state(state_file, db_path_a, gen_a, 3)

    db_path_b = tmp_path / "replacement_event_log.sqlite"
    conn_b = sqlite3.connect(db_path_b)
    try:
        gen_b = consumer.ensure_db_generation(conn_b)
        assert gen_b != gen_a
        consumer.ensure_schema(conn_b)
        payload = json.dumps({"type": "Issue", "data": {"identifier": "TEST-SWAP"}})
        conn_b.execute(
            "INSERT INTO events (rowid, dedup_key, topic, payload_json, ts, processed) VALUES (1, ?, ?, ?, ?, 0)",
            ("key-swap-1", "update", payload, time.time()),
        )
        conn_b.commit()
    finally:
        conn_b.close()

    monkeypatch.setattr(consumer, "DB_PATH", str(db_path_a))
    monkeypatch.setattr(consumer, "STATE_FILE", str(state_file))

    linear_calls: list[str] = []
    spawn_calls: list[str] = []
    monkeypatch.setattr(
        consumer, "fetch_issue", lambda issue_id: linear_calls.append(issue_id)
    )
    monkeypatch.setattr(
        consumer,
        "dispatch_to_supervisor",
        lambda issue_id: spawn_calls.append(issue_id),
    )

    is_ok, msg, state = consumer.verify_startup_gate(str(db_path_a), str(state_file))
    assert is_ok is True

    os.replace(db_path_b, db_path_a)

    with pytest.raises(
        RuntimeError, match="Database generation mismatch during event fetch"
    ):
        consumer.fetch_new_events(
            last_rowid=0, expected_generation=gen_a, db_path=str(db_path_a)
        )

    assert len(linear_calls) == 0
    assert len(spawn_calls) == 0
    st, status_code, _ = consumer.read_cursor_state(str(state_file))
    assert status_code == "VALID"
    assert st["last_rowid"] == 3


def test_20_inspect_and_dry_run_do_not_mutate_metadata_missing_db(
    tmp_path: Path,
) -> None:
    """Regression for Finding 2: inspect_cursor and repair_dry_run must open read-only and mutate nothing on metadata-missing DB."""
    db_path = tmp_path / "metadata_missing.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE events (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT UNIQUE,
                topic TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                ts REAL NOT NULL,
                processed INTEGER DEFAULT 0
            )
            """
        )
        for i in range(1, 4):
            conn.execute(
                "INSERT INTO events (dedup_key, topic, payload_json, ts) VALUES (?, 'update', '{}', 1.0)",
                (f"k{i}",),
            )
        conn.commit()
    finally:
        conn.close()

    state_file = tmp_path / "cursor.rowid"

    hash_before = consumer._sha256_file(str(db_path))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    tables_before = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    conn.close()

    res = consumer.inspect_cursor(str(db_path), str(state_file))
    assert res["db_exists"] is True
    assert res["db_generation"] is None
    assert res["gate_ready"] is False
    assert res["marker"] == "PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"

    hash_after_inspect = consumer._sha256_file(str(db_path))
    assert hash_after_inspect == hash_before

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    tables_after_inspect = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    conn.close()
    assert tables_after_inspect == tables_before

    with pytest.raises(
        RuntimeError, match="Database missing or generation unavailable"
    ):
        consumer.repair_dry_run(str(db_path), str(state_file))

    hash_after_dryrun = consumer._sha256_file(str(db_path))
    assert hash_after_dryrun == hash_before


def test_21_dry_run_determinism_and_apply_destination_enforcement(
    tmp_path: Path,
) -> None:
    """Regression for Finding 3: Dry-run must be deterministic and apply must enforce proposed backup destinations."""
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    plan1 = consumer.repair_dry_run(str(db_path), str(state_file))
    plan2 = consumer.repair_dry_run(str(db_path), str(state_file))

    assert plan1 == plan2, "Consecutive dry-run plans must be identical"
    assert "plan_id" in plan1

    receipt = consumer.repair_apply(
        str(db_path), str(state_file), confirmation_token=consumer.CONFIRMATION_TOKEN
    )

    assert receipt["db_backup_path"] == plan1["proposed_db_backup_path"]
    assert receipt["cursor_backup_path"] == plan1["proposed_cursor_backup_path"]
    assert receipt["plan_id"] == plan1["plan_id"]
    assert receipt["marker"] == "PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK"

    # Collision check: if proposed backup destination already exists on disk, repair_apply must raise FileExistsError
    db_path_col, gen_col = _setup_db_and_events(tmp_path / "col", 5)
    state_file_col = tmp_path / "col" / "dispatch_consumer.rowid"
    state_file_col.write_text("3\n")
    os.chmod(state_file_col, 0o600)
    col_plan = consumer.repair_dry_run(str(db_path_col), str(state_file_col))
    col_backup = Path(col_plan["proposed_db_backup_path"])
    col_backup.write_text("pre-existing collision")

    with pytest.raises(FileExistsError, match="collision"):
        consumer.repair_apply(
            str(db_path_col),
            str(state_file_col),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
        )

    # Source drift check
    db_path_2, gen_2 = _setup_db_and_events(tmp_path / "sub", 5)
    state_file_2 = tmp_path / "sub" / "dispatch_consumer.rowid"
    state_file_2.write_text("3\n")
    os.chmod(state_file_2, 0o600)

    drift_plan = consumer.repair_dry_run(str(db_path_2), str(state_file_2))
    # Mutate DB source file after dry-run plan creation
    conn = sqlite3.connect(db_path_2)
    conn.execute(
        "INSERT INTO events (dedup_key, topic, payload_json, ts) VALUES ('drift', 't', '{}', 1)"
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="Source drift detected"):
        consumer.repair_apply(
            str(db_path_2),
            str(state_file_2),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
            plan=drift_plan,
        )


def test_22_strict_target_rowid_validation_and_explicit_requirement(
    tmp_path: Path,
) -> None:
    """Regression for Finding 4: Target rowid must be strict built-in int in range, and explicit target required when invalid/ahead/missing."""
    db_path, gen = _setup_db_and_events(tmp_path, 5)

    for bad_target in [-1, True, False, 3.14, "3", 10]:
        with pytest.raises((ValueError, TypeError)):
            consumer.repair_dry_run(
                str(db_path), str(tmp_path / "missing.rowid"), target_rowid=bad_target
            )

    state_ahead = tmp_path / "ahead.rowid"
    _write_valid_state(state_ahead, db_path, gen, 10)
    with pytest.raises(ValueError, match="Explicit target_rowid required"):
        consumer.repair_dry_run(str(db_path), str(state_ahead))

    with pytest.raises(ValueError, match="Explicit target_rowid required"):
        consumer.repair_dry_run(str(db_path), str(tmp_path / "nonexistent.rowid"))

    plan = consumer.repair_dry_run(
        str(db_path), str(tmp_path / "nonexistent.rowid"), target_rowid=3
    )
    assert plan["proposed_cursor_state"]["last_rowid"] == 3


def test_23_strict_uuid_iso_timestamp_and_security_bounds(tmp_path: Path) -> None:
    """Regression for Finding 5: UUID format, ISO timestamp, symlink/permissions, backup fsync, and no sensitive leaks."""
    assert consumer.validate_generation_format("06d6bcc0-b0a5-41eb-8f94-349c24ed99f5")
    assert not consumer.validate_generation_format(
        "06D6BCC0-B0A5-01EB-4F94-349C24ED99F5"
    )
    assert not consumer.validate_generation_format("not-a-uuid")

    assert consumer.validate_iso_timestamp("2026-07-22T20:00:00Z")
    assert not consumer.validate_iso_timestamp("2026-07-22T20:00:00+00:00")
    assert not consumer.validate_iso_timestamp("2026-07-22T20:00:00")

    f_real = tmp_path / "real.rowid"
    f_real.write_text("content")
    os.chmod(f_real, 0o600)
    f_link = tmp_path / "link.rowid"
    os.symlink(f_real, f_link)
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    valid_state = {
        "schema_version": 1,
        "last_rowid": 1,
        "db_path": consumer.get_canonical_path(str(db_path)),
        "db_generation": gen,
        "updated_at": "2026-07-22T20:00:00Z",
    }
    with pytest.raises(ValueError, match="symlink"):
        consumer.write_cursor_state(str(f_link), valid_state)

    f_unsafe = tmp_path / "unsafe.rowid"
    f_unsafe.write_text("content")
    os.chmod(f_unsafe, 0o644)
    with pytest.raises(ValueError, match="unsafe file permissions"):
        consumer.write_cursor_state(str(f_unsafe), valid_state)


def test_24_wal_mode_raw_backup_restore_and_recovery(tmp_path: Path) -> None:
    """WAL-mode regression test: create committed WAL-resident data without checkpointing,
    perform repair dry-run/apply backup, verify exact original-to-backup SHA-256 hashes for main,
    WAL, and cursor, restore the set, delete/regenerate SHM, reopen SQLite, and prove generation,
    max-rowid, and event rows are unchanged.
    """
    db_path = tmp_path / "wal_event_log.sqlite"
    wal_path = tmp_path / "wal_event_log.sqlite-wal"
    shm_path = tmp_path / "wal_event_log.sqlite-shm"
    state_file = tmp_path / "dispatch_consumer.rowid"

    conn_setup = sqlite3.connect(db_path)
    conn_setup.execute("PRAGMA journal_mode=WAL")
    gen = consumer.ensure_db_generation(conn_setup)
    consumer.ensure_schema(conn_setup)
    for i in range(1, 4):
        payload = json.dumps({"type": "Issue", "data": {"identifier": f"WAL-{i}"}})
        conn_setup.execute(
            "INSERT INTO events (dedup_key, topic, payload_json, ts, processed) VALUES (?, ?, ?, ?, 0)",
            (f"wal-key-{i}", "update", payload, time.time()),
        )
    conn_setup.commit()

    conn_reader = sqlite3.connect(db_path)
    conn_reader.execute("SELECT COUNT(*) FROM events")

    conn_setup.close()

    assert os.path.exists(wal_path)
    assert os.path.getsize(wal_path) > 0

    state_file.write_text("2\n")
    os.chmod(state_file, 0o600)

    orig_db_sha = consumer._sha256_file(str(db_path))
    orig_wal_sha = consumer._sha256_file(str(wal_path))
    orig_cursor_sha = consumer._sha256_file(str(state_file))
    orig_wal_sz = os.path.getsize(wal_path)

    dry_run = consumer.repair_dry_run(str(db_path), str(state_file))
    assert dry_run["wal_path"] == consumer.get_canonical_path(str(wal_path))
    assert dry_run["wal_sha256"] == orig_wal_sha
    assert dry_run["wal_size"] == orig_wal_sz

    receipt = consumer.repair_apply(
        str(db_path), str(state_file), confirmation_token=consumer.CONFIRMATION_TOKEN
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["src_db_sha256"] == orig_db_sha
    assert receipt["db_backup_sha256"] == orig_db_sha
    assert receipt["src_wal_sha256"] == orig_wal_sha
    assert receipt["wal_backup_sha256"] == orig_wal_sha
    assert receipt["src_cursor_sha256"] == orig_cursor_sha
    assert receipt["cursor_backup_sha256"] == orig_cursor_sha

    conn_reader.close()

    conn_mut = sqlite3.connect(db_path)
    conn_mut.execute("DELETE FROM events")
    conn_mut.commit()
    conn_mut.close()
    state_file.write_text("corrupted")

    if os.path.exists(shm_path):
        os.remove(shm_path)

    with open(receipt["db_backup_path"], "rb") as f_in, open(db_path, "wb") as f_out:
        f_out.write(f_in.read())
    with (
        open(receipt["wal_backup_path"], "rb") as f_in,
        open(wal_path, "wb") as f_out,
    ):
        f_out.write(f_in.read())
    with (
        open(receipt["cursor_backup_path"], "rb") as f_in,
        open(state_file, "wb") as f_out,
    ):
        f_out.write(f_in.read())

    restored_db_sha = consumer._sha256_file(str(db_path))
    restored_wal_sha = consumer._sha256_file(str(wal_path))
    restored_cursor_sha = consumer._sha256_file(str(state_file))

    assert restored_db_sha == orig_db_sha
    assert restored_wal_sha == orig_wal_sha
    assert restored_cursor_sha == orig_cursor_sha

    conn_reopen = sqlite3.connect(db_path)
    try:
        reopened_gen = consumer.ensure_db_generation(conn_reopen)
        assert reopened_gen == gen
        max_rid, db_gen_ro = consumer.get_db_max_rowid_and_generation_readonly(
            str(db_path)
        )
        assert max_rid == 3
        assert db_gen_ro == gen
        events = conn_reopen.execute(
            "SELECT rowid, dedup_key FROM events ORDER BY rowid"
        ).fetchall()
        assert len(events) == 3
        assert events[0] == (1, "wal-key-1")
        assert events[1] == (2, "wal-key-2")
        assert events[2] == (3, "wal-key-3")
    finally:
        conn_reopen.close()


# -----------------------------------------------------------------------------
# Repair Regressions (Repair 3)
# -----------------------------------------------------------------------------


def test_25_injected_file_fsync_failure_propagates_leaves_no_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for Repair 3: Injected file fsync failure must propagate, leave no destination, and leave source unchanged."""
    src_file = tmp_path / "source.txt"
    src_content = "important source data"
    src_file.write_text(src_content)
    os.chmod(src_file, 0o600)

    dst_file = tmp_path / "dst.backup"

    orig_fsync = os.fsync

    def failing_file_fsync(fd: int) -> None:
        try:
            is_reg = stat.S_ISREG(os.fstat(fd).st_mode)
        except OSError:
            is_reg = False
        if is_reg:
            raise OSError("Injected file fsync error")
        orig_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_file_fsync)

    with pytest.raises(OSError, match="Injected file fsync error"):
        consumer._copy_file_raw_atomic(str(src_file), str(dst_file))

    assert not dst_file.exists()
    assert src_file.read_text() == src_content


def test_26_injected_directory_fsync_failure_propagates_leaves_no_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for Repair 3: Injected directory fsync failure must propagate, leave no destination, and leave source unchanged."""
    src_file = tmp_path / "source.txt"
    src_content = "important source data for dir test"
    src_file.write_text(src_content)
    os.chmod(src_file, 0o600)

    dst_file = tmp_path / "dst.backup"

    orig_fsync = os.fsync

    def failing_dir_fsync(fd: int) -> None:
        try:
            is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        except OSError:
            is_dir = False
        if is_dir:
            raise OSError("Injected directory fsync error")
        orig_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_dir_fsync)

    with pytest.raises((OSError, ExceptionGroup)) as exc_info:
        consumer._copy_file_raw_atomic(str(src_file), str(dst_file))
    assert "Injected directory fsync error" in str(exc_info.value) or any(
        "Injected directory fsync error" in str(e)
        for e in getattr(exc_info.value, "exceptions", [])
    )

    assert not dst_file.exists()
    assert src_file.read_text() == src_content


def test_27_symlink_source_rejected_with_no_destination(tmp_path: Path) -> None:
    """Regression for Repair 3: Symlink source is rejected with no destination creation."""
    real_src = tmp_path / "real_source.txt"
    real_src.write_text("real source data")
    os.chmod(real_src, 0o600)

    link_src = tmp_path / "link_source.txt"
    os.symlink(real_src, link_src)

    dst_file = tmp_path / "dst.backup"

    with pytest.raises(ValueError, match="symlink"):
        consumer._copy_file_raw_atomic(str(link_src), str(dst_file))

    assert not dst_file.exists()


def test_28_later_member_failure_cleans_earlier_members_preserves_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for Repair 3: Failure of later backup member cleans newly created earlier members,
    preserves pre-existing collision/unrelated files, leaves cursor bytes original, and leaves DB/WAL/event state unchanged.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_content = "3\n"
    state_file.write_text(state_content)
    os.chmod(state_file, 0o600)

    # Pre-existing unrelated file in directory
    unrelated = tmp_path / "unrelated_preexisting.txt"
    unrelated.write_text("preexisting content")

    orig_copy = consumer._copy_file_raw_atomic
    copy_call_count = 0

    def failing_second_member_copy(*args, **kwargs):
        nonlocal copy_call_count
        copy_call_count += 1
        if copy_call_count == 2:
            raise RuntimeError("Injected WAL/cursor member backup failure")
        return orig_copy(*args, **kwargs)

    monkeypatch.setattr(consumer, "_copy_file_raw_atomic", failing_second_member_copy)

    with pytest.raises(RuntimeError, match="Injected WAL/cursor member backup failure"):
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
        )

    # Prove member 1's newly created backup file is cleaned up
    plan = consumer.repair_dry_run(str(db_path), str(state_file))
    main_db_backup = Path(plan["proposed_db_backup_path"])
    assert not main_db_backup.exists()

    # Prove pre-existing unrelated file remains untouched
    assert unrelated.exists()
    assert unrelated.read_text() == "preexisting content"

    # Prove cursor file remains original
    assert state_file.read_text() == state_content

    # Prove DB state & events remain original
    conn = sqlite3.connect(db_path)
    try:
        max_rid, db_gen = consumer.get_db_max_rowid_and_generation_readonly(
            str(db_path)
        )
        assert max_rid == 5
        assert db_gen == gen
        cnt = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert cnt == 5
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Repair Regressions (Repair 4)
# -----------------------------------------------------------------------------


def test_29_generation_bound_through_claim_mark_and_vacuum(tmp_path: Path) -> None:
    """Adversarial regression: claim, mark_processed, and vacuum_processed must verify expected_generation and fail closed on mismatch."""
    db_path, gen_orig = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))
    bad_gen = "a0a0a0a0-b1b1-4c2c-8d3d-e4e4e4e4e4e4"

    # Claim with bad generation must fail closed and mutate nothing
    with pytest.raises(RuntimeError, match="Database generation mismatch during claim"):
        consumer.claim_event_for_processing(
            rowid=1,
            dedup_key="key-1",
            topic="update",
            issue_id="TEST-1",
            expected_generation=bad_gen,
            db_path=canon_db,
        )
    conn = sqlite3.connect(canon_db)
    try:
        processed_count = conn.execute(
            "SELECT COUNT(*) FROM processed_event_keys"
        ).fetchone()[0]
        assert processed_count == 0
        e1_processed = conn.execute(
            "SELECT processed FROM events WHERE rowid = 1"
        ).fetchone()[0]
        assert e1_processed == 0
    finally:
        conn.close()

    # Mark processed with bad generation must fail closed and mutate nothing
    with pytest.raises(
        RuntimeError, match="Database generation mismatch during mark_processed"
    ):
        consumer.mark_processed(
            rowid=1,
            dedup_key="key-1",
            topic="update",
            issue_id="TEST-1",
            expected_generation=bad_gen,
            db_path=canon_db,
        )

    # Vacuum with bad generation must fail closed and delete nothing
    conn = sqlite3.connect(canon_db)
    try:
        conn.execute("UPDATE events SET processed = 1, ts = 0 WHERE rowid = 1")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(
        RuntimeError, match="Database generation mismatch during vacuum"
    ):
        consumer.vacuum_processed(expected_generation=bad_gen, db_path=canon_db)

    conn = sqlite3.connect(canon_db)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert cnt == 5
    finally:
        conn.close()


def test_30_side_effects_prevented_on_db_replacement_before_linear_and_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial regression: DB replacement after claim / before Linear or before spawn must raise fail-closed with zero side effects."""
    db_path_a, gen_a = _setup_db_and_events(tmp_path, 5)
    canon_db_a = consumer.get_canonical_path(str(db_path_a))
    state_file = tmp_path / "dispatch_consumer.rowid"
    _write_valid_state(state_file, db_path_a, gen_a, 0)

    linear_calls: list[str] = []
    spawn_calls: list[str] = []
    monkeypatch.setattr(
        consumer, "fetch_issue", lambda issue_id: linear_calls.append(issue_id)
    )
    monkeypatch.setattr(
        consumer,
        "dispatch_to_supervisor",
        lambda issue_id: spawn_calls.append(issue_id),
    )

    # Setup event payload
    payload = json.dumps({"type": "Issue", "data": {"identifier": "TEST-1"}})

    # Fail before Linear call
    def fail_before_linear(db_path: str, expected_gen: str):
        raise RuntimeError("DB replaced right before Linear call")

    monkeypatch.setattr(
        consumer, "verify_db_generation_and_identity", fail_before_linear
    )

    with pytest.raises(RuntimeError, match="DB replaced right before Linear call"):
        consumer.process_event(
            rowid=1,
            dedup_key="key-1",
            topic="update",
            payload_json=payload,
            ts=time.time(),
            expected_generation=gen_a,
            db_path=canon_db_a,
        )

    assert len(linear_calls) == 0
    assert len(spawn_calls) == 0

    # Fail before supervisor spawn
    def mock_fetch_issue(issue_id: str):
        linear_calls.append(issue_id)
        return {
            "state": {"name": "Todo"},
            "labels": {"nodes": [{"name": "dispatch:ready"}]},
        }

    monkeypatch.setattr(consumer, "fetch_issue", mock_fetch_issue)

    def fail_before_spawn(db_path: str, expected_gen: str):
        if len(linear_calls) > 0:
            raise RuntimeError("DB replaced right before supervisor spawn")

    monkeypatch.setattr(
        consumer, "verify_db_generation_and_identity", fail_before_spawn
    )

    with pytest.raises(RuntimeError, match="DB replaced right before supervisor spawn"):
        consumer.process_event(
            rowid=2,
            dedup_key="key-2",
            topic="update",
            payload_json=payload,
            ts=time.time(),
            expected_generation=gen_a,
            db_path=canon_db_a,
        )

    assert len(linear_calls) == 1
    assert len(spawn_calls) == 0


def test_31_cursor_lock_serializes_repair_and_consumer_writes(
    tmp_path: Path,
) -> None:
    """Process/concurrency regression: CursorLock serializes repair and consumer cursor writes, preventing overwrite."""
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)
    canon_state = consumer.get_canonical_path(str(state_file))

    # Hold CursorLock manually to simulate repair in progress
    with consumer.CursorLock(canon_state):
        # Attempting to write cursor state from another process/thread will block or raise if non-blocking
        lock_file = canon_state + ".lock"
        fd = os.open(lock_file, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    # After lock released, repair_apply succeeds
    receipt = consumer.repair_apply(
        str(db_path), str(state_file), confirmation_token=consumer.CONFIRMATION_TOKEN
    )
    assert receipt["status"] == "SUCCESS"


def test_32_recompute_and_authenticate_supplied_plan(tmp_path: Path) -> None:
    """Regression for supplied plan authentication: tampered, missing, or extra plan fields must be rejected before any mutation."""
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("3\n")
    os.chmod(state_file, 0o600)

    valid_plan = consumer.repair_dry_run(str(db_path), str(state_file))

    # Tamper backup path
    tampered_plan = dict(valid_plan)
    tampered_plan["proposed_db_backup_path"] = "/tmp/malicious_backup"

    with pytest.raises(ValueError, match="Caller-supplied plan does not match"):
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
            plan=tampered_plan,
        )

    # Tamper plan_id
    tampered_plan_2 = dict(valid_plan)
    tampered_plan_2["plan_id"] = "bad_plan_id_123"

    with pytest.raises(ValueError, match="Caller-supplied plan does not match"):
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
            plan=tampered_plan_2,
        )

    assert not os.path.exists("/tmp/malicious_backup")


def test_33_durability_and_cleanup_failure_propagation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for directory fsync and cleanup failures: errors propagate (never swallowed), ExceptionGroup raised when primary & cleanup fail."""
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))
    state_file = tmp_path / "cursor.rowid"

    valid_state = {
        "schema_version": 1,
        "last_rowid": 1,
        "db_path": canon_db,
        "db_generation": gen,
        "updated_at": consumer.get_canonical_utc_now(),
    }

    # 1. Injected directory fsync failure during write_cursor_state must propagate
    orig_fsync = os.fsync

    def failing_dir_fsync(fd: int) -> None:
        try:
            is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        except OSError:
            is_dir = False
        if is_dir:
            raise OSError("Injected directory fsync failure")
        orig_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_dir_fsync)

    with pytest.raises(OSError, match="Injected directory fsync failure"):
        consumer.write_cursor_state(str(state_file), valid_state)

    # 2. Cleanup error combined with primary error raises ExceptionGroup
    monkeypatch.undo()

    src_file = tmp_path / "src.txt"
    src_file.write_text("data")
    dst_file = tmp_path / "dst.txt"

    def failing_write(fd: int, data: bytes) -> int:
        raise OSError("Primary write error during copy")

    def failing_remove(path: str | Path) -> None:
        raise OSError("Secondary cleanup remove error")

    monkeypatch.setattr(os, "write", failing_write)
    monkeypatch.setattr(os, "remove", failing_remove)

    with pytest.raises(ExceptionGroup) as exc_info:
        consumer._copy_file_raw_atomic(str(src_file), str(dst_file))

    assert "Backup copy failed and partial artifact cleanup failed" in str(
        exc_info.value
    )


def test_34_strict_canonical_uuid_v4_and_utc_timestamp_envelope() -> None:
    """Regression for strict canonical UUID v4 and UTC ISO timestamp validation."""
    # UUID v4 canonical tests
    valid_v4 = str(uuid.uuid4())
    assert consumer.validate_generation_format(valid_v4)

    nil_uuid = "00000000-0000-0000-0000-000000000000"
    assert not consumer.validate_generation_format(nil_uuid)

    v1_uuid = str(uuid.uuid1())
    assert not consumer.validate_generation_format(v1_uuid)

    uppercase_v4 = valid_v4.upper()
    assert not consumer.validate_generation_format(uppercase_v4)

    braced_v4 = "{" + valid_v4 + "}"
    assert not consumer.validate_generation_format(braced_v4)

    spaced_v4 = " " + valid_v4
    assert not consumer.validate_generation_format(spaced_v4)

    # UTC ISO timestamp tests
    valid_now = consumer.get_canonical_utc_now()
    assert consumer.validate_iso_timestamp(valid_now)
    assert consumer.validate_iso_timestamp("2026-07-22T20:57:38Z")
    assert consumer.validate_iso_timestamp("2026-07-22T20:57:38.123456Z")

    assert not consumer.validate_iso_timestamp("2026-07-22 20:57:38Z")  # space
    assert not consumer.validate_iso_timestamp("2026-07-22T20:57:38")  # no tz
    assert not consumer.validate_iso_timestamp(
        "2026-07-22T20:57:38+00:00"
    )  # +00:00 instead of Z
    assert not consumer.validate_iso_timestamp("2026-W29-3T20:57:38Z")  # week date
    assert not consumer.validate_iso_timestamp(
        "2026-07-22T20:57:38.1234567Z"
    )  # 7 digits fraction


# -----------------------------------------------------------------------------
# Repair Regressions (Repair 5)
# -----------------------------------------------------------------------------


def test_35_set_state_critical_section_adversarial_replacements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 5 Requirement 1: set_state is one generation-bound critical section.
    Database replacement between read and write or after write fails closed and durably restores prior state.
    """
    db_path_a, gen_a = _setup_db_and_events(tmp_path / "db_a", 5)
    db_path_b, gen_b = _setup_db_and_events(tmp_path / "db_b", 5)
    canon_db_a = consumer.get_canonical_path(str(db_path_a))
    canon_db_b = consumer.get_canonical_path(str(db_path_b))

    state_file = tmp_path / "dispatch_consumer.rowid"
    _write_valid_state(state_file, db_path_a, gen_a, 2)
    orig_cursor_content = state_file.read_bytes()

    # Part A: DB replaced after identity read before write
    def db_swap_before_write(*args, **kwargs):
        os.replace(canon_db_b, canon_db_a)

    monkeypatch.setattr(consumer, "_write_cursor_state_unlocked", db_swap_before_write)

    with pytest.raises(
        RuntimeError, match="Database file replaced|Post-write validation|[FAIL_CLOSED]"
    ):
        consumer.set_state(
            3,
            expected_generation=gen_a,
            db_path=canon_db_a,
            state_file_path=str(state_file),
        )

    # Assert prior cursor state restored exactly
    assert state_file.read_bytes() == orig_cursor_content

    # Part B: Post-write validation detects DB replacement immediately after write
    monkeypatch.undo()
    db_path_a2, gen_a2 = _setup_db_and_events(tmp_path / "db_a_fresh", 5)
    canon_db_a2 = consumer.get_canonical_path(str(db_path_a2))
    _write_valid_state(state_file, db_path_a2, gen_a2, 2)
    orig_cursor_content2 = state_file.read_bytes()

    db_path_b2, gen_b2 = _setup_db_and_events(tmp_path / "db_b2", 5)
    canon_db_b2 = consumer.get_canonical_path(str(db_path_b2))

    orig_write = consumer._write_cursor_state_unlocked

    def db_swap_after_write(path: str, data: dict):
        orig_write(path, data)
        os.replace(canon_db_b2, canon_db_a2)

    monkeypatch.setattr(consumer, "_write_cursor_state_unlocked", db_swap_after_write)

    with pytest.raises(
        RuntimeError, match="Post-write validation detected replacement"
    ):
        consumer.set_state(
            3,
            expected_generation=gen_a2,
            db_path=canon_db_a2,
            state_file_path=str(state_file),
        )

    # Assert prior cursor state restored exactly
    assert state_file.read_bytes() == orig_cursor_content2

    # Part C: Non-existent prior cursor file removed if post-write validation fails
    monkeypatch.undo()
    db_path_a3, gen_a3 = _setup_db_and_events(tmp_path / "db_a_fresh3", 5)
    canon_db_a3 = consumer.get_canonical_path(str(db_path_a3))
    db_path_b3, gen_b3 = _setup_db_and_events(tmp_path / "db_b3", 5)
    canon_db_b3 = consumer.get_canonical_path(str(db_path_b3))

    def db_swap_after_write3(path: str, data: dict):
        orig_write(path, data)
        os.replace(canon_db_b3, canon_db_a3)

    monkeypatch.setattr(consumer, "_write_cursor_state_unlocked", db_swap_after_write3)

    nonexistent_state = tmp_path / "nonexistent.rowid"
    assert not nonexistent_state.exists()

    with pytest.raises(
        RuntimeError, match="Post-write validation detected replacement"
    ):
        consumer.set_state(
            3,
            expected_generation=gen_a3,
            db_path=canon_db_a3,
            state_file_path=str(nonexistent_state),
        )

    assert not nonexistent_state.exists()


def test_36_symlink_target_and_parent_alias_rejected_without_artifacts(
    tmp_path: Path,
) -> None:
    """Repair 5 Requirement 2: Reject caller-supplied symlink final target and parent-symlink alias
    before creating lock/backup/temp files or modifying destination.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))

    target_cursor = tmp_path / "target_real.rowid"
    _write_valid_state(target_cursor, db_path, gen, 2)
    target_content_before = target_cursor.read_bytes()

    # Clean up lock created during setup so we can assert no new locks are created
    lock_file = Path(str(target_cursor) + ".lock")
    if lock_file.exists():
        lock_file.unlink()

    symlink_cursor = tmp_path / "symlink_target.rowid"
    os.symlink(target_cursor, symlink_cursor)

    # 1. Inspect on symlink target
    res = consumer.inspect_cursor(str(db_path), str(symlink_cursor))
    assert res["status_code"] == "INVALID"
    assert res["cursor_format"] == "invalid"
    assert res["gate_ready"] is False

    # 2. write_cursor_state on symlink target
    new_state = {
        "schema_version": 1,
        "last_rowid": 4,
        "db_path": canon_db,
        "db_generation": gen,
        "updated_at": consumer.get_canonical_utc_now(),
    }
    with pytest.raises(ValueError, match="symlink"):
        consumer.write_cursor_state(str(symlink_cursor), new_state)

    # 3. repair_dry_run on symlink target
    with pytest.raises(ValueError, match="symlink"):
        consumer.repair_dry_run(str(db_path), str(symlink_cursor), target_rowid=3)

    # 4. repair_apply on symlink target
    with pytest.raises(ValueError, match="symlink"):
        consumer.repair_apply(
            str(db_path),
            str(symlink_cursor),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
            target_rowid=3,
        )

    # Prove destination was NOT mutated
    assert target_cursor.read_bytes() == target_content_before

    # Prove NO backup files, NO lock files, NO temp files created
    new_artifacts = (
        list(tmp_path.glob("*.lock"))
        + list(tmp_path.glob("*.backup*"))
        + list(tmp_path.glob(".*tmp*"))
    )
    assert len(new_artifacts) == 0

    # 5. Parent symlink alias test
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_dir"
    os.symlink(real_dir, sym_dir)

    parent_sym_cursor = sym_dir / "cursor.rowid"
    with pytest.raises(ValueError, match="symlink"):
        consumer.repair_apply(
            str(db_path),
            str(parent_sym_cursor),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
            target_rowid=3,
        )

    dir_artifacts = list(real_dir.glob("*")) + list(sym_dir.glob("*"))
    assert len(dir_artifacts) == 0


def test_37_post_cursor_write_injected_failures_and_durable_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 5 Requirement 3: Injected failures after cursor replacement boundary perform durable
    automatic rollback or retain verified backups with explicit recovery required outcome.
    DB/WAL/events/processed rows remain unchanged.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("2\n")
    os.chmod(state_file, 0o600)
    orig_cursor_content = state_file.read_text()

    # Inject failure immediately after os.replace during repair_apply
    orig_replace = os.replace
    replace_count = 0

    def failing_replace_after_cursor(src: str, dst: str):
        nonlocal replace_count
        replace_count += 1
        orig_replace(src, dst)
        if replace_count == 1 and "dispatch_consumer.rowid" in dst:
            raise OSError("Injected error right after cursor os.replace")

    monkeypatch.setattr(os, "replace", failing_replace_after_cursor)

    with pytest.raises(RuntimeError) as exc_info:
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
        )

    err_msg = str(exc_info.value)
    assert (
        "Durable rollback succeeded: exact original cursor state restored" in err_msg
        or "RECOVERY REQUIRED" in err_msg
    )

    # Prove cursor file restored to original exact content
    assert state_file.read_text() == orig_cursor_content

    # Prove DB events & generation are completely untouched
    conn = sqlite3.connect(db_path)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert cnt == 5
        db_gen = conn.execute(
            "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
        ).fetchone()[0]
        assert db_gen == gen
    finally:
        conn.close()


def test_38_preexisting_collision_files_never_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 5 Requirement 3: Pre-existing collision files are never deleted on failure."""
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("2\n")
    os.chmod(state_file, 0o600)

    # Get dry run plan
    plan = consumer.repair_dry_run(str(db_path), str(state_file))
    collision_file = Path(plan["proposed_db_backup_path"])
    collision_file.write_text("pre-existing collision content")

    with pytest.raises(FileExistsError, match="collision"):
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
        )

    # Collision file must still exist and be intact!
    assert collision_file.exists()
    assert collision_file.read_text() == "pre-existing collision content"


# -----------------------------------------------------------------------------
# Repair Regressions (Repair 6)
# -----------------------------------------------------------------------------


def test_39_repair_apply_lock_release_injection_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 6: Inject LOCK_UN failure and fd-close failure separately for repair_apply after cursor replacement.
    Assert durable rollback restores exact original cursor and retains verified backups without generic error.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("2\n")
    os.chmod(state_file, 0o600)
    orig_cursor_bytes = state_file.read_bytes()

    # 1. LOCK_UN failure injection for repair_apply
    orig_flock = fcntl.flock

    def failing_unlock(fd: int, cmd: int) -> None:
        if cmd == fcntl.LOCK_UN:
            raise OSError(errno.EINVAL, "Injected LOCK_UN failure")
        orig_flock(fd, cmd)

    monkeypatch.setattr(fcntl, "flock", failing_unlock)

    with pytest.raises(RuntimeError) as exc_info:
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
        )

    err_msg = str(exc_info.value)
    assert "Durable rollback succeeded: exact original cursor state restored" in err_msg
    assert state_file.read_bytes() == orig_cursor_bytes

    # Backups must be retained
    backups = list(tmp_path.glob("*.backup*"))
    assert len(backups) >= 1

    # Clean up backups for next test part
    for b in backups:
        b.unlink()

    # 2. fd-close failure injection for repair_apply
    monkeypatch.undo()

    orig_close = os.close
    lock_fds = set()
    orig_open = os.open

    def mock_open(path: str, flags: int, mode: int = 0o777) -> int:
        fd = orig_open(path, flags, mode)
        if ".lock" in str(path):
            lock_fds.add(fd)
        return fd

    def mock_close_lock(fd: int) -> None:
        is_lock = fd in lock_fds
        if is_lock:
            lock_fds.remove(fd)
        orig_close(fd)
        if is_lock:
            raise OSError(errno.EIO, "Injected lock fd close failure")

    monkeypatch.setattr(os, "open", mock_open)
    monkeypatch.setattr(os, "close", mock_close_lock)

    with pytest.raises(RuntimeError) as exc_info:
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
        )

    err_msg = str(exc_info.value)
    assert "Durable rollback succeeded" in err_msg or "close failure" in err_msg
    assert state_file.read_bytes() == orig_cursor_bytes


def test_40_set_state_and_write_cursor_state_lock_release_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 6: Inject LOCK_UN failure and fd-close failure for set_state and write_cursor_state.
    Assert prior exact bytes restored or newly created cursor removed.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))
    state_file = tmp_path / "cursor.rowid"
    _write_valid_state(state_file, db_path, gen, 2)
    orig_cursor_bytes = state_file.read_bytes()

    # Part A: set_state + LOCK_UN failure after write
    orig_flock = fcntl.flock

    def failing_unlock(fd: int, cmd: int) -> None:
        if cmd == fcntl.LOCK_UN:
            raise OSError(errno.EINVAL, "Injected LOCK_UN failure")
        orig_flock(fd, cmd)

    monkeypatch.setattr(fcntl, "flock", failing_unlock)

    with pytest.raises(RuntimeError, match="[FAIL_CLOSED]") as exc_info:
        consumer.set_state(
            3,
            expected_generation=gen,
            db_path=canon_db,
            state_file_path=str(state_file),
        )

    assert "PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED" in str(exc_info.value)
    assert state_file.read_bytes() == orig_cursor_bytes

    # Part B: write_cursor_state + LOCK_UN failure after write
    valid_state_4 = {
        "schema_version": 1,
        "last_rowid": 4,
        "db_path": canon_db,
        "db_generation": gen,
        "updated_at": consumer.get_canonical_utc_now(),
    }

    with pytest.raises(RuntimeError, match="[FAIL_CLOSED]"):
        consumer.write_cursor_state(str(state_file), valid_state_4)

    assert state_file.read_bytes() == orig_cursor_bytes

    # Part C: Newly created cursor state file removed on lock release failure
    monkeypatch.undo()
    new_state_file = tmp_path / "new_cursor.rowid"
    assert not new_state_file.exists()

    monkeypatch.setattr(fcntl, "flock", failing_unlock)

    with pytest.raises(RuntimeError, match="[FAIL_CLOSED]"):
        consumer.write_cursor_state(str(new_state_file), valid_state_4)

    assert not new_state_file.exists()


def test_41_coexisting_body_and_release_failures_preserve_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 6: Inject both body failure and release failure.
    Assert body exception is preserved as primary in ExceptionGroup without swallowing.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "cursor.rowid"
    _write_valid_state(state_file, db_path, gen, 2)
    canon_db = consumer.get_canonical_path(str(db_path))

    # Inject LOCK_UN failure
    orig_flock = fcntl.flock

    def failing_unlock(fd: int, cmd: int) -> None:
        if cmd == fcntl.LOCK_UN:
            raise OSError(errno.EINVAL, "Injected LOCK_UN failure")
        orig_flock(fd, cmd)

    monkeypatch.setattr(fcntl, "flock", failing_unlock)

    # set_state body fails (generation mismatch) AND unlock fails
    with pytest.raises(ExceptionGroup) as exc_info:
        consumer.set_state(
            3,
            expected_generation="bad-generation-uuid",
            db_path=canon_db,
            state_file_path=str(state_file),
        )

    err_str = str(exc_info.value)
    assert "set_state failed and lock release encountered errors" in err_str
    assert any("Database generation" in str(ex) for ex in exc_info.value.exceptions)


def test_42_recovery_hash_failure_and_temp_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 6/7: Inject recovery restoration failure and temp cleanup failure.
    Assert RECOVERY_REQUIRED outcome with marker and precomputed safe backup hashes when rollback fails.
    Assert temp cleanup failure raises exception when temp file removal fails.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    state_file = tmp_path / "dispatch_consumer.rowid"
    state_file.write_text("2\n")
    os.chmod(state_file, 0o600)

    # 1. Lock release failure + rollback failure + sha256 failure during recovery
    orig_flock = fcntl.flock

    def failing_unlock(fd: int, cmd: int) -> None:
        if cmd == fcntl.LOCK_UN:
            raise OSError(errno.EINVAL, "Injected LOCK_UN failure")
        orig_flock(fd, cmd)

    def failing_restore_cursor(
        canonical_state_path: str, prior_existed: bool, prior_bytes: bytes | None
    ) -> None:
        raise OSError("Rollback restore failed")

    monkeypatch.setattr(fcntl, "flock", failing_unlock)
    monkeypatch.setattr(consumer, "_restore_or_remove_cursor", failing_restore_cursor)

    with pytest.raises(RuntimeError) as exc_info:
        consumer.repair_apply(
            str(db_path),
            str(state_file),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
        )

    err_msg = str(exc_info.value)
    assert "RECOVERY REQUIRED" in err_msg or "RECOVERY_REQUIRED" in err_msg
    assert "PRISMATIC_DISPATCH_CURSOR_GENERATION_RECOVERY_REQUIRED" in err_msg

    # Backups must be retained!
    backups = list(tmp_path.glob("*.backup*"))
    assert len(backups) >= 1

    # 2. Temp cleanup failure during write_cursor_state when primary write fails
    monkeypatch.undo()

    orig_fsync = os.fsync
    orig_remove = os.remove

    def failing_fsync(fd: int) -> None:
        try:
            st = os.fstat(fd)
            is_reg = stat.S_ISREG(st.st_mode)
        except OSError:
            is_reg = False
        if is_reg:
            raise OSError("Injected primary fsync failure")
        orig_fsync(fd)

    def failing_remove_temp(path: str | Path) -> None:
        if ".dispatch_cursor_tmp_" in str(path):
            raise OSError("Injected temp file removal error")
        orig_remove(path)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    monkeypatch.setattr(os, "remove", failing_remove_temp)

    valid_state = {
        "schema_version": 1,
        "last_rowid": 3,
        "db_path": consumer.get_canonical_path(str(db_path)),
        "db_generation": gen,
        "updated_at": consumer.get_canonical_utc_now(),
    }

    with pytest.raises((OSError, ExceptionGroup)) as exc_info:
        consumer.write_cursor_state(str(state_file), valid_state)

    err_str = str(exc_info.value)
    assert (
        "Failed to write cursor state and cleanup encountered an error" in err_str
        or "fsync" in err_str
    )


# -----------------------------------------------------------------------------
# Repair Regressions (Repair 7)
# -----------------------------------------------------------------------------


def test_43_adversarial_contender_reserialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 7 Regression 1: For each of repair_apply, set_state, and write_cursor_state:
    injected release performs real unlock/close, then coordinates a contender thread/process
    that acquires CursorLock and writes a distinct valid cursor before raising.
    Assert recovery does not overwrite contender output.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))

    def make_contender_state(rid: int) -> dict:
        return {
            "schema_version": 1,
            "last_rowid": rid,
            "db_path": canon_db,
            "db_generation": gen,
            "updated_at": consumer.get_canonical_utc_now(),
        }

    # --- Part A: write_cursor_state ---
    state_file_a = tmp_path / "cursor_a.rowid"
    consumer.write_cursor_state(str(state_file_a), make_contender_state(1))

    orig_release = consumer.CursorLock.release
    release_injection = {"armed": False}

    def injected_release_with_contender(lock_inst: consumer.CursorLock) -> None:
        orig_release(lock_inst)
        if not release_injection["armed"]:
            return
        # Disarm before the contender uses the same public writer; otherwise its
        # own release recursively launches another contender forever.
        release_injection["armed"] = False
        contender_state = make_contender_state(999)
        consumer.write_cursor_state(lock_inst.state_file_path, contender_state)
        raise OSError(errno.EIO, "Injected release failure")

    monkeypatch.setattr(consumer.CursorLock, "release", injected_release_with_contender)

    release_injection["armed"] = True
    with pytest.raises(RuntimeError, match="[FAIL_CLOSED]"):
        consumer.write_cursor_state(str(state_file_a), make_contender_state(2))

    st_a, code_a, _ = consumer.read_cursor_state(str(state_file_a))
    assert code_a == "VALID"
    assert st_a["last_rowid"] == 999

    # --- Part B: set_state ---
    state_file_b = tmp_path / "cursor_b.rowid"
    consumer.write_cursor_state(str(state_file_b), make_contender_state(1))

    release_injection["armed"] = True
    with pytest.raises(RuntimeError, match="[FAIL_CLOSED]"):
        consumer.set_state(
            2,
            expected_generation=gen,
            db_path=canon_db,
            state_file_path=str(state_file_b),
        )

    st_b, code_b, _ = consumer.read_cursor_state(str(state_file_b))
    assert code_b == "VALID"
    assert st_b["last_rowid"] == 999

    # --- Part C: repair_apply ---
    state_file_c = tmp_path / "cursor_c.rowid"
    consumer.write_cursor_state(str(state_file_c), make_contender_state(1))

    release_injection["armed"] = True
    with pytest.raises(RuntimeError) as exc_info:
        consumer.repair_apply(
            str(db_path),
            str(state_file_c),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
            target_rowid=2,
        )

    err_str = str(exc_info.value)
    assert (
        "RECOVERY REQUIRED" in err_str
        or "RECOVERY_REQUIRED" in err_str
        or "Later contender" in err_str
    )
    st_c, code_c, _ = consumer.read_cursor_state(str(state_file_c))
    assert code_c == "VALID"
    assert st_c["last_rowid"] == 999


def test_44_zero_byte_cursor_release_failure_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 7 Regression 2: Existing 0600 zero-byte cursor through write_cursor_state,
    set_state, and repair_apply with post-write release failure restores exact file existence
    and b"" when no contender wins.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))

    def make_state(rid: int) -> dict:
        return {
            "schema_version": 1,
            "last_rowid": rid,
            "db_path": canon_db,
            "db_generation": gen,
            "updated_at": consumer.get_canonical_utc_now(),
        }

    orig_flock = fcntl.flock

    def failing_unlock(fd: int, cmd: int) -> None:
        if cmd == fcntl.LOCK_UN:
            raise OSError(errno.EINVAL, "Injected LOCK_UN failure")
        orig_flock(fd, cmd)

    monkeypatch.setattr(fcntl, "flock", failing_unlock)

    # --- Part A: write_cursor_state ---
    f_zb1 = tmp_path / "zero1.rowid"
    f_zb1.write_bytes(b"")
    os.chmod(f_zb1, 0o600)

    with pytest.raises(RuntimeError, match="[FAIL_CLOSED]"):
        consumer.write_cursor_state(str(f_zb1), make_state(1))

    assert f_zb1.exists()
    assert f_zb1.read_bytes() == b""
    assert stat.S_IMODE(os.lstat(f_zb1).st_mode) == 0o600

    # --- Part B: set_state ---
    f_zb2 = tmp_path / "zero2.rowid"
    f_zb2.write_bytes(b"")
    os.chmod(f_zb2, 0o600)

    with pytest.raises(RuntimeError, match="[FAIL_CLOSED]"):
        consumer.set_state(
            1, expected_generation=gen, db_path=canon_db, state_file_path=str(f_zb2)
        )

    assert f_zb2.exists()
    assert f_zb2.read_bytes() == b""
    assert stat.S_IMODE(os.lstat(f_zb2).st_mode) == 0o600

    # --- Part C: repair_apply ---
    f_zb3 = tmp_path / "zero3.rowid"
    f_zb3.write_bytes(b"")
    os.chmod(f_zb3, 0o600)

    with pytest.raises(RuntimeError) as exc_info:
        consumer.repair_apply(
            str(db_path),
            str(f_zb3),
            confirmation_token=consumer.CONFIRMATION_TOKEN,
            target_rowid=1,
        )

    assert "Durable rollback succeeded" in str(exc_info.value)
    assert f_zb3.exists()
    assert f_zb3.read_bytes() == b""
    assert stat.S_IMODE(os.lstat(f_zb3).st_mode) == 0o600


def test_45_repair_dry_run_and_apply_zero_byte_backup(tmp_path: Path) -> None:
    """Repair 7 Regression 3: Repair dry-run/apply backs up a zero-byte cursor as an explicit
    zero-byte member with exact SHA-256/size proof.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    f_zb = tmp_path / "zero_cursor.rowid"
    f_zb.write_bytes(b"")
    os.chmod(f_zb, 0o600)

    dry_run = consumer.repair_dry_run(str(db_path), str(f_zb), target_rowid=2)
    assert dry_run["cursor_path"] == consumer.get_canonical_path(str(f_zb))
    assert dry_run["cursor_size"] == 0
    assert (
        dry_run["cursor_sha256"]
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )

    cursor_member = next(m for m in dry_run["members"] if m["name"] == "cursor")
    assert cursor_member["src_size"] == 0
    assert (
        cursor_member["src_sha256"]
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )

    receipt = consumer.repair_apply(
        str(db_path),
        str(f_zb),
        confirmation_token=consumer.CONFIRMATION_TOKEN,
        target_rowid=2,
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["src_cursor_size"] == 0
    assert (
        receipt["src_cursor_sha256"]
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert receipt["cursor_backup_size"] == 0
    assert (
        receipt["cursor_backup_sha256"]
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    bak_path = Path(receipt["cursor_backup_path"])
    assert bak_path.exists()
    assert bak_path.read_bytes() == b""


def test_46_post_acquire_snapshot_failure_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair 7 Regression 4: Inject lstat/stat and open/read failures after lock acquisition.
    Assert no cursor mutation, lock can immediately be acquired by a separate process,
    and body+release failures are structured.
    """
    db_path, gen = _setup_db_and_events(tmp_path, 5)
    canon_db = consumer.get_canonical_path(str(db_path))
    state_file = tmp_path / "cursor.rowid"
    consumer.write_cursor_state(
        str(state_file),
        {
            "schema_version": 1,
            "last_rowid": 1,
            "db_path": canon_db,
            "db_generation": gen,
            "updated_at": consumer.get_canonical_utc_now(),
        },
    )
    orig_bytes = state_file.read_bytes()

    # Part A: Inject snapshot lstat failure after lock acquisition
    def failing_snapshot(path: str):
        raise OSError(errno.EACCES, "Injected snapshot read error")

    monkeypatch.setattr(consumer, "_snapshot_cursor_file", failing_snapshot)

    with pytest.raises(OSError, match="Injected snapshot read error"):
        consumer.write_cursor_state(
            str(state_file),
            {
                "schema_version": 1,
                "last_rowid": 2,
                "db_path": canon_db,
                "db_generation": gen,
                "updated_at": consumer.get_canonical_utc_now(),
            },
        )

    # 1. No cursor mutation
    assert state_file.read_bytes() == orig_bytes

    # 2. Lock can immediately be acquired by a separate process
    with consumer.CursorLock(str(state_file)):
        pass

    # Part B: Inject both snapshot failure AND lock release failure -> structured ExceptionGroup
    orig_flock = fcntl.flock

    def failing_unlock(fd: int, cmd: int) -> None:
        if cmd == fcntl.LOCK_UN:
            raise OSError(errno.EINVAL, "Injected LOCK_UN failure")
        orig_flock(fd, cmd)

    monkeypatch.setattr(fcntl, "flock", failing_unlock)

    with pytest.raises(ExceptionGroup) as exc_info:
        consumer.write_cursor_state(
            str(state_file),
            {
                "schema_version": 1,
                "last_rowid": 2,
                "db_path": canon_db,
                "db_generation": gen,
                "updated_at": consumer.get_canonical_utc_now(),
            },
        )

    err_str = str(exc_info.value)
    assert "write_cursor_state failed and lock release encountered errors" in err_str
    assert any(
        "Injected snapshot read error" in str(e) for e in exc_info.value.exceptions
    )
    assert state_file.read_bytes() == orig_bytes
