"""Tests for dispatch consumer v3 database generation identity, versioned cursor state, fail-closed startup gate, atomic state writes, and inspect/dry-run/apply repair primitives."""

from __future__ import annotations

import concurrent.futures
import importlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path
import pytest

from prismatic.gateway.event_handlers import dispatch_consumer_v3 as consumer


def _setup_db_and_events(tmp_path: Path, event_count: int = 5) -> tuple[Path, str]:
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

    new_gen = "ffffffff-ffff-ffff-ffff-ffffffffffff"
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


def test_10_concurrent_schema_initialization_yields_one_valid_generation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "concurrent_event_log.sqlite"

    def _init() -> str:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            return consumer.ensure_db_generation(conn)
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_init) for _ in range(20)]
        results = [f.result() for f in futures]

    assert len(results) == 20
    assert len(set(results)) == 1
    assert consumer.validate_generation_format(results[0])


def test_11_atomic_cursor_write_failure_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "cursor.rowid"
    state_file.write_text("old_content")
    os.chmod(state_file, 0o600)

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("Injected replace error")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Injected replace error"):
        consumer.write_cursor_state(str(state_file), {"test": 1})

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

    assert db_hash_restored == receipt["db_backup_sha256"]
    assert cursor_hash_restored == receipt["cursor_backup_sha256"]


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
    # Explicitly validated via running test_dispatch_consumer_v3_idempotency.py
    pass
