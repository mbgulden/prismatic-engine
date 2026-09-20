from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prismatic.task_admission import TaskAdmissionError, TaskAdmissionStore
from prismatic.task_admission_consumer import (
    AdmissionRevalidationError,
    LauncherError,
    TaskAdmissionConsumer,
    command_launcher,
)

KEY = "runtime:TST-1:1234567890abcdef1234"
COMMIT = "a" * 40
TREE = "b" * 40


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def secure_launcher_dir() -> Iterator[Path]:
    # The production loader fail-closes on any group/world writable parent
    # directory, so the fixture must live somewhere whose whole ancestor
    # chain is owner-only. Candidate locations differ by environment
    # (CI checkout vs. local workspace), so take the first writable one
    # whose chain satisfies the same rules production enforces.
    def _chain_is_secure(path: Path) -> bool:
        for parent in path.parents:
            try:
                st = parent.lstat()
            except OSError:
                return False
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(st.st_mode) & 0o022
            ):
                return False
        return True

    candidates = [
        Path(tempfile.gettempdir()) / ".prismatic-test-launchers",
        Path(__file__).resolve().parents[1] / ".test-launchers",
        Path.home() / ".prismatic-test-launchers",
        Path("/root/.prismatic-test-launchers"),
    ]
    root: Path | None = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
            candidate.chmod(0o700)
        except OSError:
            continue
        if _chain_is_secure(candidate):
            root = candidate
            break
    if root is None:
        pytest.skip(
            "no writable directory with a fully owner-only ancestor chain "
            "for the secure-launcher fixture"
        )
    directory = Path(tempfile.mkdtemp(prefix="consumer-", dir=root))
    directory.chmod(0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
        try:
            root.rmdir()
        except OSError:
            pass


def _launcher_script(directory: Path, name: str, body: str) -> Path:
    script = directory / name
    script.write_text(f"#!{Path(sys.executable).resolve()}\n{body}")
    script.chmod(0o700)
    return script


def _git_runner(_: Path, ref: str) -> str:
    if ref == "HEAD":
        return COMMIT
    if ref == "HEAD^{tree}":
        return TREE
    if ref == "STATUS":
        return ""
    raise AssertionError(ref)


def _fixture(tmp_path: Path):
    clock = Clock()
    worktree = tmp_path / "worktree"
    task = worktree / "tasks" / "TST-1" / "TASK.md"
    task.parent.mkdir(parents=True)
    task.write_text("fixture task\n")
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "worktrees": [str(worktree)],
                "producers": ["fixture-producer"],
                "max_age_seconds": 300,
            }
        )
    )
    policy.chmod(0o600)
    payload = {
        "version": 1,
        "task_id": "TST-1",
        "base_commit": COMMIT,
        "base_tree": TREE,
        "task_file": "tasks/TST-1/TASK.md",
        "task_file_sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
        "producer_identity": "fixture-producer",
        "worktree": str(worktree),
        "writer_cap": 1,
        "idempotency_key": KEY,
        "created_at": "2026-07-24T20:00:00Z",
        "status": "admitted",
    }
    db = tmp_path / "bus.sqlite"
    store = TaskAdmissionStore(
        db_path=db, policy_path=policy, git_runner=_git_runner, now=clock
    )
    admitted = store.admit(payload, header_key=KEY, actor="operator")
    return clock, worktree, task, policy, db, admitted.record


def _admit_second(
    clock: Clock, worktree: Path, policy: Path, db: Path
) -> dict[str, object]:
    task = worktree / "tasks" / "TST-2" / "TASK.md"
    task.parent.mkdir(parents=True)
    task.write_text("second fixture task\n")
    key = "runtime:TST-2:1234567890abcdef1234"
    payload = {
        "version": 1,
        "task_id": "TST-2",
        "base_commit": COMMIT,
        "base_tree": TREE,
        "task_file": "tasks/TST-2/TASK.md",
        "task_file_sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
        "producer_identity": "fixture-producer",
        "worktree": str(worktree),
        "writer_cap": 1,
        "idempotency_key": key,
        "created_at": "2026-07-24T20:00:00Z",
        "status": "admitted",
    }
    store = TaskAdmissionStore(
        db_path=db, policy_path=policy, git_runner=_git_runner, now=clock
    )
    return store.admit(payload, header_key=key, actor="operator").record


def _consumer(clock: Clock, policy: Path, db: Path, identity: str = "consumer-a"):
    return TaskAdmissionConsumer(
        db_path=db,
        policy_path=policy,
        identity=identity,
        lease_seconds=30,
        now=clock,
        git_runner=_git_runner,
    )


def _receipt(request):
    return {
        "accepted": True,
        "idempotency_key": request.event_id,
        "launch_id": "launch-" + request.task_id,
    }


def _retryable_claim(consumer: TaskAdmissionConsumer, db: Path) -> tuple[str, str]:
    with pytest.raises(LauncherError, match="temporary launcher failure"):
        consumer.run_once(
            lambda _request: (_ for _ in ()).throw(
                LauncherError("temporary launcher failure")
            )
        )
    connection = sqlite3.connect(db)
    row = connection.execute(
        "select event_id,claim_id from task_admission_consumer_claims"
    ).fetchone()
    connection.close()
    assert row is not None
    return str(row[0]), str(row[1])


def _reconciliation(task_id: str, event_id: str, claim_id: str) -> dict[str, str]:
    return {
        "task_id": task_id,
        "expected_event_id": event_id,
        "expected_claim_id": claim_id,
        "expected_current_state": "retryable_failed",
        "evidence_type": "reviewed_merge_completion",
        "candidate_sha": "1" * 40,
        "candidate_tree": "2" * 40,
        "merge_sha": "3" * 40,
        "merge_tree": "4" * 40,
        "evidence_sha256": "5" * 64,
        "reason_code": "completed_via_reviewed_bounded_repair",
    }


def test_successful_one_shot_claim_revalidate_launch_and_lifecycle(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, record = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    launched = []

    def launcher(request):
        launched.append(request)
        return _receipt(request)

    result = consumer.run_once(launcher)

    assert result is not None
    assert result.status == "processed"
    assert result.event_id == record["event_id"]
    assert result.attempt == 1
    assert len(launched) == 1
    request = launched[0]
    assert request.writer_cap == 1
    assert request.event_id == record["event_id"]
    assert request.claim_id == result.claim_id
    assert request.attempt == result.attempt == 1
    assert request.as_dict()["claim_id"] == result.claim_id
    assert request.as_dict()["attempt"] == 1
    assert consumer.run_once(launcher) is None

    connection = sqlite3.connect(db)
    assert (
        connection.execute(
            "select status from task_admission_outbox where event_id=?",
            (record["event_id"],),
        ).fetchone()[0]
        == "processed"
    )
    assert (
        connection.execute(
            "select count(*) from task_admission_writer_lease"
        ).fetchone()[0]
        == 0
    )
    assert [
        row[0]
        for row in connection.execute(
            "select event from task_admission_lifecycle order by lifecycle_id"
        )
    ] == ["claimed", "validated", "launch_started", "launched"]
    receipt = json.loads(
        connection.execute(
            "select launch_receipt_json from task_admission_consumer_claims"
        ).fetchone()[0]
    )
    assert receipt == _receipt(request)
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle_immutable"):
        connection.execute("update task_admission_lifecycle set event='forged'")
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle_immutable"):
        connection.execute("delete from task_admission_lifecycle")
    connection.close()


def test_two_consumers_cannot_claim_same_or_second_row_while_cap_one_lease_active(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, record = _fixture(tmp_path)
    first = _consumer(clock, policy, db, "consumer-a")
    second = _consumer(clock, policy, db, "consumer-b")

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda c: c.claim_one(), (first, second)))

    actual = [claim for claim in claims if claim is not None]
    assert len(actual) == 1
    assert actual[0].event_id == record["event_id"]
    assert second.claim_one() is None


def test_cap_one_lease_blocks_a_distinct_pending_task(tmp_path: Path) -> None:
    clock, worktree, _, policy, db, first_record = _fixture(tmp_path)
    second_record = _admit_second(clock, worktree, policy, db)
    first = _consumer(clock, policy, db, "consumer-a")
    second = _consumer(clock, policy, db, "consumer-b")

    claim = first.claim_one()
    assert claim is not None
    assert claim.event_id in {first_record["event_id"], second_record["event_id"]}
    assert second.claim_one() is None
    pending_event = (
        second_record["event_id"]
        if claim.event_id == first_record["event_id"]
        else first_record["event_id"]
    )

    connection = sqlite3.connect(db)
    assert (
        connection.execute(
            "select status from task_admission_outbox where event_id=?",
            (pending_event,),
        ).fetchone()[0]
        == "pending"
    )
    assert (
        connection.execute(
            "select count(*) from task_admission_writer_lease"
        ).fetchone()[0]
        == 1
    )
    connection.close()


def test_claim_transaction_rolls_back_every_row_on_lifecycle_failure(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TRIGGER reject_claim_lifecycle BEFORE INSERT ON task_admission_lifecycle "
        "BEGIN SELECT RAISE(ABORT, 'fixture_reject'); END"
    )
    connection.commit()
    connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="fixture_reject"):
        consumer.claim_one()

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    assert (
        connection.execute(
            "select count(*) from task_admission_consumer_claims"
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "select count(*) from task_admission_writer_lease"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_expired_claim_is_recovered_with_stable_launch_idempotency_key(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, record = _fixture(tmp_path)
    first = _consumer(clock, policy, db, "consumer-crashed")
    abandoned = first.claim_one()
    assert abandoned is not None and abandoned.attempt == 1

    clock.advance(31)
    recovered_consumer = _consumer(clock, policy, db, "consumer-recovery")
    seen = []

    def launcher(request):
        seen.append(request.event_id)
        return _receipt(request)

    result = recovered_consumer.run_once(launcher)

    assert result is not None and result.attempt == 2
    assert seen == [record["event_id"]]
    connection = sqlite3.connect(db)
    assert [
        row[0]
        for row in connection.execute(
            "select event from task_admission_lifecycle order by lifecycle_id"
        )
    ] == ["claimed", "recovered", "validated", "launch_started", "launched"]
    connection.close()
    with pytest.raises(AdmissionRevalidationError, match="lease_lost"):
        first._transition(abandoned, "validated", "validated")


def test_expired_launch_started_claim_recovers_with_same_event_key(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, record = _fixture(tmp_path)
    crashed = _consumer(clock, policy, db, "consumer-crashed-after-start")
    claim = crashed.claim_one()
    assert claim is not None
    crashed.revalidate(claim)
    crashed._transition(claim, "validated", "validated")
    crashed._transition(claim, "launch_started", "launch_started")

    clock.advance(31)
    recovered = _consumer(clock, policy, db, "consumer-recovery")
    seen = []

    def launcher(request):
        seen.append(request.event_id)
        return _receipt(request)

    result = recovered.run_once(launcher)
    assert result is not None and result.attempt == 2
    assert seen == [record["event_id"]]
    connection = sqlite3.connect(db)
    events = [
        row[0]
        for row in connection.execute(
            "select event from task_admission_lifecycle order by lifecycle_id"
        )
    ]
    assert events == [
        "claimed",
        "validated",
        "launch_started",
        "recovered",
        "validated",
        "launch_started",
        "launched",
    ]
    connection.close()


def test_tampered_outbox_envelope_fails_terminally_without_launch(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    connection = sqlite3.connect(db)
    connection.execute("update task_admission_outbox set payload_json='{}'")
    connection.commit()
    connection.close()
    launched = []

    with pytest.raises(AdmissionRevalidationError, match="outbox_envelope_mismatch"):
        _consumer(clock, policy, db).run_once(lambda request: launched.append(request))

    assert launched == []
    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "failed"
    )
    connection.close()


def test_tuple_mutation_fails_terminally_without_launch(tmp_path: Path) -> None:
    clock, _, task, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    task.write_text("mutated after admission\n")
    launched = []

    with pytest.raises(AdmissionRevalidationError, match="task_file_digest_mismatch"):
        consumer.run_once(lambda request: launched.append(request))

    assert launched == []
    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "failed"
    )
    assert (
        connection.execute(
            "select state from task_admission_consumer_claims"
        ).fetchone()[0]
        == "terminal_failed"
    )
    assert (
        connection.execute(
            "select count(*) from task_admission_writer_lease"
        ).fetchone()[0]
        == 0
    )
    assert [
        row[0]
        for row in connection.execute("select event from task_admission_lifecycle")
    ] == ["claimed", "validation_failed"]
    connection.close()


def test_launcher_failure_requeues_and_retry_is_idempotent(tmp_path: Path) -> None:
    clock, _, _, policy, db, record = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)

    def broken(_request):
        raise LauncherError("temporary launcher failure")

    with pytest.raises(LauncherError):
        consumer.run_once(broken)

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    assert (
        connection.execute(
            "select state from task_admission_consumer_claims"
        ).fetchone()[0]
        == "retryable_failed"
    )
    connection.close()

    seen = []

    def repaired(request):
        seen.append(request.event_id)
        return _receipt(request)

    result = consumer.run_once(repaired)
    assert result is not None and result.attempt == 2
    assert seen == [record["event_id"]]


def test_third_launcher_failure_terminalizes_instead_of_poison_requeue(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    attempts = []

    def broken(request):
        attempts.append(request.attempt)
        raise LauncherError("temporary launcher failure")

    for expected in (1, 2, 3):
        with pytest.raises(LauncherError):
            consumer.run_once(broken)
        connection = sqlite3.connect(db)
        status, state = connection.execute(
            "select o.status,c.state from task_admission_outbox o "
            "join task_admission_consumer_claims c using(event_id)"
        ).fetchone()
        connection.close()
        assert status == ("failed" if expected == 3 else "pending")
        assert state == ("terminal_failed" if expected == 3 else "retryable_failed")

    assert attempts == [1, 2, 3]
    connection = sqlite3.connect(db)
    lifecycle = [
        row[0]
        for row in connection.execute(
            "select event from task_admission_lifecycle order by lifecycle_id"
        )
    ]
    connection.close()
    assert lifecycle[-1] == "launch_failed"
    assert consumer.run_once(broken) is None


def test_launcher_receipt_must_bind_stable_event_id(tmp_path: Path) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)

    with pytest.raises(LauncherError, match="launcher_idempotency_mismatch"):
        consumer.run_once(
            lambda request: {
                "accepted": True,
                "idempotency_key": "wrong",
                "launch_id": "launch-1",
            }
        )

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    connection.close()


def test_long_launch_renews_singleton_lease(tmp_path: Path) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    consumer.lease_seconds = 3
    renewals = 0
    original_renew = consumer._renew

    def counted_renew(claim):
        nonlocal renewals
        renewals += 1
        original_renew(claim)

    consumer._renew = counted_renew  # type: ignore[method-assign]

    def slow_launcher(request):
        time.sleep(1.2)
        return _receipt(request)

    result = consumer.run_once(slow_launcher)
    assert result is not None
    assert renewals >= 2  # heartbeat plus final ownership proof


def test_command_launcher_uses_owner_only_provider_map_and_strict_receipt(
    tmp_path: Path, secure_launcher_dir: Path
) -> None:
    clock, _, _, policy, db, record = _fixture(tmp_path)
    launcher_script = _launcher_script(
        secure_launcher_dir,
        "launcher",
        "import json,sys\n"
        "r=json.load(sys.stdin)\n"
        "print(json.dumps({'accepted':True,'idempotency_key':r['event_id'],'launch_id':'cmd-'+r['task_id']}))\n",
    )
    config = tmp_path / "launcher.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "producers": {
                    "fixture-producer": {
                        "command": [str(launcher_script)],
                        "timeout_seconds": 10,
                    }
                },
            }
        )
    )
    config.chmod(0o600)
    result = _consumer(clock, policy, db).run_once(command_launcher(config))
    assert result is not None
    assert result.event_id == record["event_id"]
    assert result.launch_id == "cmd-TST-1"

    config.chmod(0o644)
    with pytest.raises(LauncherError, match="launcher_config_unavailable"):
        command_launcher(config)(
            type(
                "Request",
                (),
                {
                    "producer_identity": "fixture-producer",
                    "as_dict": lambda self: {},
                },
            )()
        )


def test_interpreter_script_argument_bypass_is_rejected(tmp_path: Path) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    mutable_script = tmp_path / "world-writable-launcher.py"
    mutable_script.write_text("raise SystemExit('must not execute')\n")
    mutable_script.chmod(0o666)
    config = tmp_path / "launcher.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "producers": {
                    "fixture-producer": {
                        "command": [
                            str(Path(sys.executable).resolve()),
                            str(mutable_script),
                        ],
                        "timeout_seconds": 5,
                    }
                },
            }
        )
    )
    config.chmod(0o600)
    with pytest.raises(LauncherError, match="launcher_config_invalid"):
        _consumer(clock, policy, db).run_once(command_launcher(config))
    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    connection.close()


def test_launcher_executable_in_world_writable_parent_is_rejected(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    launcher_script = tmp_path / "direct-launcher"
    launcher_script.write_text("#!/bin/sh\nexit 0\n")
    launcher_script.chmod(0o755)
    config = tmp_path / "launcher.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "producers": {
                    "fixture-producer": {
                        "command": [str(launcher_script)],
                        "timeout_seconds": 5,
                    }
                },
            }
        )
    )
    config.chmod(0o600)
    with pytest.raises(LauncherError, match="launcher_executable_invalid"):
        _consumer(clock, policy, db).run_once(command_launcher(config))
    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    connection.close()


def test_command_launcher_output_is_bounded_and_requeues(
    tmp_path: Path, secure_launcher_dir: Path
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    launcher_script = _launcher_script(
        secure_launcher_dir,
        "loud-launcher",
        "import sys\nsys.stdout.write('x' * 65537)\n",
    )
    config = tmp_path / "launcher.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "producers": {
                    "fixture-producer": {
                        "command": [str(launcher_script)],
                        "timeout_seconds": 5,
                    }
                },
            }
        )
    )
    config.chmod(0o600)
    with pytest.raises(LauncherError, match="launcher_output_too_large"):
        _consumer(clock, policy, db).run_once(command_launcher(config))
    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    assert (
        connection.execute(
            "select count(*) from task_admission_writer_lease"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_command_launcher_timeout_requeues_without_orphaning_lease(
    tmp_path: Path, secure_launcher_dir: Path
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    launcher_script = _launcher_script(
        secure_launcher_dir,
        "slow-launcher",
        "import time\ntime.sleep(60)\n",
    )
    config = tmp_path / "launcher.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "producers": {
                    "fixture-producer": {
                        "command": [str(launcher_script)],
                        "timeout_seconds": 1,
                    }
                },
            }
        )
    )
    config.chmod(0o600)
    started = time.monotonic()
    with pytest.raises(LauncherError, match="launcher_command_timeout"):
        _consumer(clock, policy, db).run_once(command_launcher(config))
    assert time.monotonic() - started < 5
    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    assert (
        connection.execute(
            "select count(*) from task_admission_writer_lease"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_database_and_lifecycle_files_remain_owner_only(tmp_path: Path) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    consumer.run_once(_receipt)
    for candidate in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if candidate.exists():
            assert stat.S_IMODE(candidate.stat().st_mode) == 0o600
            assert candidate.stat().st_uid == os.geteuid()
            assert not candidate.is_symlink()


def test_terminal_reconciliation_is_atomic_idempotent_and_not_claimable(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, record = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)

    first = consumer.terminal_reconcile(payload)
    assert first.replayed is False
    assert first.record["outbox_status"] == "failed"
    assert first.record["claim_state"] == "terminal_failed"
    assert first.record["launch_receipt"] is None

    connection = sqlite3.connect(db)
    assert connection.execute(
        "select status,claimed_at,processed_at from task_admission_outbox"
    ).fetchone() == ("failed", None, None)
    assert connection.execute(
        "select state,last_error_code,launch_receipt_json "
        "from task_admission_consumer_claims"
    ).fetchone() == (
        "terminal_failed",
        "externally_completed_reviewed_repair",
        None,
    )
    lifecycle = connection.execute(
        "select event,detail_sha256 from task_admission_lifecycle order by lifecycle_id"
    ).fetchall()
    connection.close()
    assert [row[0] for row in lifecycle] == [
        "claimed",
        "validated",
        "launch_started",
        "launch_failed",
        "terminal_reconciled",
    ]
    assert lifecycle[-1][1] == first.record["detail_sha256"]

    replay = consumer.terminal_reconcile(payload)
    assert replay.replayed is True
    assert replay.record == first.record
    connection = sqlite3.connect(db)
    assert (
        connection.execute(
            "select count(*) from task_admission_lifecycle "
            "where event='terminal_reconciled'"
        ).fetchone()[0]
        == 1
    )
    connection.close()

    launched: list[object] = []
    assert consumer.run_once(lambda request: launched.append(request)) is None
    assert launched == []
    assert record["event_id"] == event_id


def test_terminal_reconciliation_conflicting_replay_rolls_back(tmp_path: Path) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    consumer.terminal_reconcile(payload)
    conflict = dict(payload)
    conflict["merge_sha"] = "6" * 40

    with pytest.raises(TaskAdmissionError, match="terminal_reconciliation_conflict"):
        consumer.terminal_reconcile(conflict)

    connection = sqlite3.connect(db)
    assert (
        connection.execute(
            "select count(*) from task_admission_lifecycle "
            "where event='terminal_reconciled'"
        ).fetchone()[0]
        == 1
    )
    assert connection.execute(
        "select state,launch_receipt_json from task_admission_consumer_claims"
    ).fetchone() == ("terminal_failed", None)
    connection.close()


def test_terminal_reconciliation_exact_replay_rejects_active_writer_lease(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    consumer.terminal_reconcile(payload)
    connection = sqlite3.connect(db)
    timestamp = "2026-07-24T21:00:00Z"
    connection.execute(
        "insert into task_admission_writer_lease "
        "(slot,event_id,claim_id,lease_expires_at,updated_at) values (1,?,?,?,?)",
        (event_id, claim_id, timestamp, timestamp),
    )
    connection.commit()
    connection.close()

    with pytest.raises(TaskAdmissionError, match="terminal_reconciliation_conflict"):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "failed"
    )
    assert connection.execute(
        "select state,launch_receipt_json from task_admission_consumer_claims"
    ).fetchone() == ("terminal_failed", None)
    assert (
        connection.execute(
            "select count(*) from task_admission_lifecycle "
            "where event='terminal_reconciled'"
        ).fetchone()[0]
        == 1
    )
    connection.close()


def test_terminal_reconciliation_exact_replay_rejects_completion_contradiction(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    consumer.terminal_reconcile(payload)
    connection = sqlite3.connect(db)
    attempt = connection.execute(
        "select attempt_count from task_admission_consumer_claims"
    ).fetchone()[0]
    connection.execute(
        "insert into task_admission_lifecycle "
        "(event_id,task_id,claim_id,event,attempt,consumer_identity,detail_sha256,created_at) "
        "values (?,?,?,?,?,?,?,?)",
        (
            event_id,
            "TST-1",
            claim_id,
            "launched",
            attempt,
            "contradiction-fixture",
            None,
            "2026-07-24T21:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    with pytest.raises(TaskAdmissionError, match="terminal_reconciliation_conflict"):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "failed"
    )
    assert connection.execute(
        "select state,launch_receipt_json from task_admission_consumer_claims"
    ).fetchone() == ("terminal_failed", None)
    assert (
        connection.execute(
            "select count(*) from task_admission_lifecycle "
            "where event='terminal_reconciled'"
        ).fetchone()[0]
        == 1
    )
    connection.close()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ({"expected_claim_id": "9" * 32}, "terminal_reconciliation_tuple_mismatch"),
        (
            {"expected_current_state": "completed"},
            "terminal_reconciliation_state_invalid",
        ),
        ({"candidate_sha": "A" * 40}, "terminal_reconciliation_git_object_invalid"),
        (
            {"evidence_sha256": "0" * 63},
            "terminal_reconciliation_evidence_digest_invalid",
        ),
        (
            {"reason_code": "operator_override"},
            "terminal_reconciliation_reason_invalid",
        ),
        ({"evidence_type": "merge"}, "terminal_reconciliation_evidence_type_invalid"),
    ],
)
def test_terminal_reconciliation_rejects_malformed_or_mismatched_tuple(
    tmp_path: Path, mutation: dict[str, str], error: str
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    payload.update(mutation)

    with pytest.raises(TaskAdmissionError, match=error):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    assert connection.execute(
        "select state,launch_receipt_json from task_admission_consumer_claims"
    ).fetchone() == ("retryable_failed", None)
    assert (
        connection.execute(
            "select count(*) from task_admission_lifecycle "
            "where event='terminal_reconciled'"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_terminal_reconciliation_rejects_active_writer_or_claim_lease(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    connection = sqlite3.connect(db)
    future = "2026-07-24T21:00:00Z"
    connection.execute(
        "insert into task_admission_writer_lease "
        "(slot,event_id,claim_id,lease_expires_at,updated_at) values (1,?,?,?,?)",
        (event_id, claim_id, future, future),
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        TaskAdmissionError, match="terminal_reconciliation_writer_lease_active"
    ):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    connection.execute("delete from task_admission_writer_lease")
    connection.execute(
        "update task_admission_outbox set status='claimed',claimed_at=?", (future,)
    )
    connection.execute(
        "update task_admission_consumer_claims set lease_expires_at='not-a-timestamp'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(
        TaskAdmissionError, match="terminal_reconciliation_claim_lease_invalid"
    ):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    connection.execute(
        "update task_admission_consumer_claims set lease_expires_at=?", (future,)
    )
    connection.commit()
    connection.close()
    with pytest.raises(
        TaskAdmissionError, match="terminal_reconciliation_claim_lease_active"
    ):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "claimed"
    )
    assert (
        connection.execute(
            "select state from task_admission_consumer_claims"
        ).fetchone()[0]
        == "retryable_failed"
    )
    connection.close()


def test_terminal_reconciliation_accepts_expired_claim_without_writer_lease(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    connection = sqlite3.connect(db)
    expired = "2026-07-24T19:59:59Z"
    connection.execute(
        "update task_admission_outbox set status='claimed',claimed_at=?", (expired,)
    )
    connection.execute(
        "update task_admission_consumer_claims set lease_expires_at=?", (expired,)
    )
    connection.commit()
    connection.close()

    result = consumer.terminal_reconcile(payload)
    assert result.replayed is False
    assert result.record["outbox_status"] == "failed"


def test_terminal_reconciliation_rejects_receipt_or_completion_conflict(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    connection = sqlite3.connect(db)
    connection.execute(
        "update task_admission_consumer_claims set launch_receipt_json='{}'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        TaskAdmissionError, match="terminal_reconciliation_launch_receipt_present"
    ):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    connection.execute(
        "update task_admission_consumer_claims set launch_receipt_json=NULL"
    )
    claim = connection.execute(
        "select attempt_count from task_admission_consumer_claims"
    ).fetchone()
    connection.execute(
        "insert into task_admission_lifecycle "
        "(event_id,task_id,claim_id,event,attempt,consumer_identity,detail_sha256,created_at) "
        "values (?,?,?,?,?,?,?,?)",
        (
            event_id,
            "TST-1",
            claim_id,
            "launched",
            claim[0],
            "fixture",
            None,
            "2026-07-24T20:00:00Z",
        ),
    )
    connection.commit()
    connection.close()
    with pytest.raises(
        TaskAdmissionError, match="terminal_reconciliation_completion_conflict"
    ):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    assert connection.execute(
        "select state,launch_receipt_json from task_admission_consumer_claims"
    ).fetchone() == ("retryable_failed", None)
    assert (
        connection.execute(
            "select count(*) from task_admission_lifecycle "
            "where event='terminal_reconciled'"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_terminal_reconciliation_lifecycle_failure_rolls_back_every_change(
    tmp_path: Path,
) -> None:
    clock, _, _, policy, db, _ = _fixture(tmp_path)
    consumer = _consumer(clock, policy, db)
    event_id, claim_id = _retryable_claim(consumer, db)
    payload = _reconciliation("TST-1", event_id, claim_id)
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TRIGGER reject_terminal_reconciliation "
        "BEFORE INSERT ON task_admission_lifecycle "
        "WHEN NEW.event='terminal_reconciled' "
        "BEGIN SELECT RAISE(ABORT, 'fixture_reject'); END"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        TaskAdmissionError, match="terminal_reconciliation_storage_failed"
    ):
        consumer.terminal_reconcile(payload)

    connection = sqlite3.connect(db)
    assert (
        connection.execute("select status from task_admission_outbox").fetchone()[0]
        == "pending"
    )
    assert connection.execute(
        "select state,last_error_code,launch_receipt_json "
        "from task_admission_consumer_claims"
    ).fetchone() == ("retryable_failed", "LauncherError", None)
    assert (
        connection.execute(
            "select count(*) from task_admission_lifecycle "
            "where event='terminal_reconciled'"
        ).fetchone()[0]
        == 0
    )
    connection.close()
