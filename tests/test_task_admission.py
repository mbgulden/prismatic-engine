from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prismatic.task_admission import (
    TaskAdmissionError,
    TaskAdmissionStore,
    parse_admission_json,
)

NOW = datetime(2026, 7, 24, 21, 0, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
TREE = "b" * 40
KEY = "admission:GRO-4210:0123456789abcdef"
ROOT = Path(__file__).resolve().parents[1]


def test_canonical_and_packaged_admission_schemas_are_byte_identical() -> None:
    canonical = ROOT / "schemas/task-admission.schema.json"
    packaged = ROOT / "prismatic/schemas/task-admission.schema.json"
    assert canonical.resolve() != packaged.resolve()
    assert canonical.read_bytes() == packaged.read_bytes()


def _fixture(tmp_path: Path):
    worktree = tmp_path / "worktree"
    task = worktree / "tasks" / "GRO-4210" / "TASK.md"
    task.parent.mkdir(parents=True)
    task.write_text("exact bounded task\n")
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "worktrees": [str(worktree.resolve())],
                "producers": ["agy-pnv6"],
                "max_age_seconds": 300,
            }
        )
    )
    policy.chmod(0o600)
    payload = {
        "version": 1,
        "task_id": "GRO-4210",
        "base_commit": COMMIT,
        "base_tree": TREE,
        "task_file": "tasks/GRO-4210/TASK.md",
        "task_file_sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
        "producer_identity": "agy-pnv6",
        "worktree": str(worktree.resolve()),
        "writer_cap": 1,
        "idempotency_key": KEY,
        "created_at": "2026-07-24T21:00:00Z",
        "status": "admitted",
    }

    def git_runner(_: Path, ref: str) -> str:
        if ref == "STATUS":
            return ""
        return COMMIT if ref == "HEAD" else TREE

    store = TaskAdmissionStore(
        db_path=tmp_path / "bus.sqlite",
        policy_path=policy,
        git_runner=git_runner,
        now=lambda: NOW,
    )
    return store, payload, task, policy


def test_parse_rejects_duplicate_keys_and_non_object() -> None:
    with pytest.raises(TaskAdmissionError, match="duplicate_json_key"):
        parse_admission_json(b'{"task_id":"GRO-1","task_id":"GRO-2"}')
    with pytest.raises(TaskAdmissionError, match="body_must_be_object"):
        parse_admission_json(b"[]")


def test_first_admission_is_atomic_and_exact_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    first = store.admit(payload, header_key=KEY, actor="michael")
    replay = store.admit(payload, header_key=KEY, actor="another-operator")

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.record == first.record
    assert first.record["actor"] == "michael"
    assert first.record["status"] == "admitted"
    assert first.record["writer_cap"] == 1

    connection = sqlite3.connect(store.db_path)
    assert connection.execute("SELECT count(*) FROM task_admissions").fetchone()[0] == 1
    assert (
        connection.execute("SELECT count(*) FROM task_admission_outbox").fetchone()[0]
        == 1
    )
    assert (
        connection.execute("SELECT count(*) FROM task_admission_audit").fetchone()[0]
        == 1
    )
    topic, status = connection.execute(
        "SELECT topic,status FROM task_admission_outbox"
    ).fetchone()
    assert (topic, status) == ("dashboard.task.admitted.v1", "pending")
    connection.close()


def test_exact_replay_survives_freshness_expiry_and_worktree_move(
    tmp_path: Path,
) -> None:
    store, payload, worktree, _ = _fixture(tmp_path)
    first = store.admit(payload, header_key=KEY, actor="michael")
    store.now = lambda: NOW + timedelta(days=1)
    worktree.rename(tmp_path / "moved-worktree")

    replay = store.admit(payload, header_key=KEY, actor="michael")
    assert replay.replayed is True
    assert replay.record == first.record


def test_idempotency_and_task_conflicts_fail_closed(tmp_path: Path) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    store.admit(payload, header_key=KEY, actor="michael")
    changed = {**payload, "created_at": "2026-07-24T21:00:01Z"}
    with pytest.raises(TaskAdmissionError, match="idempotency_conflict") as exc:
        store.admit(changed, header_key=KEY, actor="michael")
    assert exc.value.status_code == 409
    other_key = "admission:GRO-4210:fedcba9876543210"
    changed = {**payload, "idempotency_key": other_key}
    with pytest.raises(TaskAdmissionError, match="task_already_admitted"):
        store.admit(changed, header_key=other_key, actor="michael")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("writer_cap", True, "schema_validation_failed"),
        ("writer_cap", 0, "schema_validation_failed"),
        ("writer_cap", 2, "schema_validation_failed"),
        ("producer_identity", "not-allowed", "producer_not_allowed"),
        ("created_at", "2026-07-24T20:00:00Z", "created_at_stale"),
        ("base_commit", "c" * 40, "base_commit_mismatch"),
        ("base_tree", "c" * 40, "base_tree_mismatch"),
    ],
)
def test_policy_revision_cap_and_freshness_rejections(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    payload[field] = value
    with pytest.raises(TaskAdmissionError, match=code):
        store.admit(payload, header_key=KEY, actor="michael")


def test_task_file_hash_traversal_and_symlink_rejected(tmp_path: Path) -> None:
    store, payload, task, _ = _fixture(tmp_path)
    payload["task_file_sha256"] = "0" * 64
    with pytest.raises(TaskAdmissionError, match="task_file_hash_mismatch"):
        store.admit(payload, header_key=KEY, actor="michael")

    payload["task_file"] = "../outside"
    with pytest.raises(TaskAdmissionError, match="task_file_invalid"):
        store.admit(payload, header_key=KEY, actor="michael")

    link = task.parent / "LINK.md"
    link.symlink_to(task)
    payload["task_file"] = "tasks/GRO-4210/LINK.md"
    payload["task_file_sha256"] = hashlib.sha256(task.read_bytes()).hexdigest()
    with pytest.raises(TaskAdmissionError, match="task_file_symlink"):
        store.admit(payload, header_key=KEY, actor="michael")


def test_task_file_size_is_bounded(tmp_path: Path) -> None:
    store, payload, task, _ = _fixture(tmp_path)
    task.write_bytes(b"x" * (1024 * 1024 + 1))
    payload["task_file_sha256"] = "0" * 64
    with pytest.raises(TaskAdmissionError, match="task_file_too_large"):
        store.admit(payload, header_key=KEY, actor="michael")


def test_database_is_owner_only_and_unsafe_paths_fail_closed(tmp_path: Path) -> None:
    store, payload, _, policy = _fixture(tmp_path)
    store.admit(payload, header_key=KEY, actor="michael")
    for candidate in (
        store.db_path,
        Path(f"{store.db_path}-wal"),
        Path(f"{store.db_path}-shm"),
    ):
        if candidate.exists():
            assert candidate.stat().st_mode & 0o777 == 0o600

    insecure = tmp_path / "insecure.sqlite"
    insecure.write_bytes(b"")
    insecure.chmod(0o644)
    with pytest.raises(TaskAdmissionError, match="admission_database_unsafe"):
        TaskAdmissionStore(db_path=insecure, policy_path=policy)

    target = tmp_path / "target.sqlite"
    target.write_bytes(b"")
    target.chmod(0o600)
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(target)
    with pytest.raises(TaskAdmissionError, match="admission_database_unsafe"):
        TaskAdmissionStore(db_path=alias, policy_path=policy)


def test_readback_remains_available_without_admission_policy(tmp_path: Path) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    admitted = store.admit(payload, header_key=KEY, actor="michael").record
    reader = TaskAdmissionStore(db_path=store.db_path, policy_path=None)
    assert reader.get("GRO-4210") == admitted
    assert reader.list() == [admitted]


def test_worktree_alias_and_insecure_policy_rejected(tmp_path: Path) -> None:
    store, payload, _, policy = _fixture(tmp_path)
    payload["worktree"] += "/."
    with pytest.raises(TaskAdmissionError, match="worktree_not_allowed"):
        store.admit(payload, header_key=KEY, actor="michael")

    payload["worktree"] = str((tmp_path / "worktree").resolve())
    policy.chmod(0o644)
    with pytest.raises(TaskAdmissionError, match="admission_policy_unavailable"):
        store.admit(payload, header_key=KEY, actor="michael")


def test_header_key_must_match_and_unknown_fields_fail(tmp_path: Path) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    with pytest.raises(TaskAdmissionError, match="idempotency_key_mismatch"):
        store.admit(payload, header_key="x" * 32, actor="michael")
    payload["token"] = "must-not-be-stored"
    with pytest.raises(TaskAdmissionError, match="schema_validation_failed"):
        store.admit(payload, header_key=KEY, actor="michael")


def test_concurrent_identical_requests_create_one_outbox_event(tmp_path: Path) -> None:
    store, payload, _, _ = _fixture(tmp_path)

    def admit(_: int):
        return store.admit(dict(payload), header_key=KEY, actor="michael")

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(admit, range(12)))
    assert sum(not result.replayed for result in results) == 1
    assert sum(result.replayed for result in results) == 11
    connection = sqlite3.connect(store.db_path)
    assert (
        connection.execute("SELECT count(*) FROM task_admission_outbox").fetchone()[0]
        == 1
    )
    connection.close()


def test_admission_and_audit_rows_are_immutable(tmp_path: Path) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    store.admit(payload, header_key=KEY, actor="michael")
    connection = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.DatabaseError, match="admission_immutable"):
        connection.execute("UPDATE task_admissions SET status = 'changed'")
    with pytest.raises(sqlite3.DatabaseError, match="admission_immutable"):
        connection.execute("DELETE FROM task_admissions")
    with pytest.raises(sqlite3.DatabaseError, match="audit_immutable"):
        connection.execute("DELETE FROM task_admission_audit")
    connection.close()


def test_snapshot_mutation_before_commit_fails_without_partial_rows(
    tmp_path: Path,
) -> None:
    store, payload, task, _ = _fixture(tmp_path)
    head_calls = 0

    def mutating_runner(_: Path, ref: str) -> str:
        nonlocal head_calls
        if ref == "STATUS":
            return ""
        if ref == "HEAD":
            head_calls += 1
            if head_calls == 3:
                task.write_text("mutated before transaction commit\n")
            return COMMIT
        return TREE

    store.git_runner = mutating_runner
    with pytest.raises(TaskAdmissionError, match="task_file_hash_mismatch"):
        store.admit(payload, header_key=KEY, actor="michael")
    connection = sqlite3.connect(store.db_path)
    assert connection.execute("SELECT count(*) FROM task_admissions").fetchone()[0] == 0
    assert (
        connection.execute("SELECT count(*) FROM task_admission_outbox").fetchone()[0]
        == 0
    )
    connection.close()


def test_transaction_failure_leaves_no_partial_admission(tmp_path: Path) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    connection = store._connect()
    connection.execute(
        "CREATE TRIGGER fail_outbox BEFORE INSERT ON task_admission_outbox "
        "BEGIN SELECT RAISE(ABORT, 'forced_outbox_failure'); END"
    )
    connection.close()

    with pytest.raises(TaskAdmissionError, match="admission_storage_failed"):
        store.admit(payload, header_key=KEY, actor="michael")

    connection = sqlite3.connect(store.db_path)
    assert connection.execute("SELECT count(*) FROM task_admissions").fetchone()[0] == 0
    assert (
        connection.execute("SELECT count(*) FROM task_admission_outbox").fetchone()[0]
        == 0
    )
    assert (
        connection.execute("SELECT count(*) FROM task_admission_audit").fetchone()[0]
        == 0
    )
    connection.close()


def test_git_validation_runner_is_bounded_to_exact_revision_queries(
    tmp_path: Path,
) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    calls: list[tuple[Path, str]] = []

    def runner(worktree: Path, ref: str) -> str:
        calls.append((worktree, ref))
        if ref == "STATUS":
            return ""
        return COMMIT if ref == "HEAD" else TREE

    store.git_runner = runner
    store.admit(payload, header_key=KEY, actor="michael")
    assert [ref for _, ref in calls] == [
        "HEAD",
        "HEAD^{tree}",
        "STATUS",
        "HEAD",
        "HEAD^{tree}",
        "STATUS",
    ] * 2


def test_readback_contains_no_task_contents_or_credentials(tmp_path: Path) -> None:
    store, payload, _, _ = _fixture(tmp_path)
    store.admit(payload, header_key=KEY, actor="michael")
    record = store.get("GRO-4210")
    assert record is not None
    serialized = json.dumps(record)
    assert "exact bounded task" not in serialized
    assert "Authorization" not in serialized
    assert store.list() == [record]
