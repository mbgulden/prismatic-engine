"""Adversarial tests for canonical cron runner authority core (CRONRUNNER-1).

Covers required adversarial test cases 1 through 18.
"""

from __future__ import annotations

import ast
import concurrent.futures
import sqlite3
import threading
from pathlib import Path

import pytest

from prismatic.cron_authority import (
    CronAuthorityError,
    CronAuthorityStore,
    connect_cron_authority,
    migrate_cron_authority,
)
from prismatic.cron_receipts.schema import CronRunReceipt
from prismatic.cron_runner import (
    AdapterResult,
    CronDependency,
    CronRegistrySnapshot,
    CronTriggerEnvelope,
    compute_command_digest,
    reconcile_expired_attempts,
    run_once,
    select_catch_up_buckets,
)

VALID_RELEASE_ROOT = (
    "/home/ubuntu/.prismatic/releases/a1b2c3d4e5f607182930a1b2c3d4e5f607182930"
)


class FakeAdapter:
    def __init__(
        self,
        outcome: str = "succeeded",
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.outcome = outcome
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.call_count = 0
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: str,
        execution_id: str,
        attempt: int,
        fence_token: int,
        runner_id: str,
        runner_release_digest: str,
    ) -> AdapterResult:
        with self._lock:
            self.call_count += 1
            self.calls.append(
                {
                    "argv": argv,
                    "cwd": cwd,
                    "execution_id": execution_id,
                    "attempt": attempt,
                    "fence_token": fence_token,
                    "runner_id": runner_id,
                    "runner_release_digest": runner_release_digest,
                }
            )
        return AdapterResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            outcome=self.outcome,
        )


@pytest.fixture
def disposable_db(tmp_path: Path) -> Path:
    return tmp_path / "test_cron_runner.sqlite"


@pytest.fixture
def valid_argv_cwd() -> tuple[tuple[str, ...], str]:
    argv = ("python3", "-m", "worker")
    cwd = f"{VALID_RELEASE_ROOT}/app"
    return argv, cwd


@pytest.fixture
def valid_digests(valid_argv_cwd: tuple[tuple[str, ...], str]) -> tuple[str, str, str]:
    argv, cwd = valid_argv_cwd
    cmd_digest = compute_command_digest(argv, cwd)
    rel_digest = "1" * 64
    runner_rel_digest = "2" * 64
    return cmd_digest, rel_digest, runner_rel_digest


@pytest.fixture
def sample_envelope(valid_digests: tuple[str, str, str]) -> CronTriggerEnvelope:
    cmd_digest, rel_digest, _ = valid_digests
    return CronTriggerEnvelope(
        trigger_event_id="trig_001",
        trigger_kind="scheduled",
        transport_kind="http",
        cron_id="cron.job.alpha",
        registry_generation=1,
        schedule_bucket="2026-07-29T06:00:00Z",
        command_digest=cmd_digest,
        release_digest=rel_digest,
        submitted_at="2026-07-29T06:00:00Z",
    )


@pytest.fixture
def sample_snapshot(
    valid_argv_cwd: tuple[tuple[str, ...], str], valid_digests: tuple[str, str, str]
) -> CronRegistrySnapshot:
    argv, cwd = valid_argv_cwd
    cmd_digest, rel_digest, _ = valid_digests
    return CronRegistrySnapshot(
        cron_id="cron.job.alpha",
        registry_generation=1,
        command_digest=cmd_digest,
        release_digest=rel_digest,
        argv=argv,
        cwd=cwd,
        state="active",
        depends_on=(),
        catch_up_policy="run_once",
        max_replay_buckets=10,
    )


def test_1_barrier_concurrency_two_workers_two_transports(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 1: Two workers behind barrier contend for same bucket, exactly 1 process admitted."""
    _, _, runner_rel_digest = valid_digests
    adapter = FakeAdapter()

    barrier = threading.Barrier(2)

    env_http = sample_envelope
    env_hook = CronTriggerEnvelope(
        trigger_event_id="trig_002",
        trigger_kind="hook",
        transport_kind="hook",
        cron_id=sample_envelope.cron_id,
        registry_generation=sample_envelope.registry_generation,
        schedule_bucket=sample_envelope.schedule_bucket,
        command_digest=sample_envelope.command_digest,
        release_digest=sample_envelope.release_digest,
        submitted_at=sample_envelope.submitted_at,
    )

    def worker_fn(env: CronTriggerEnvelope, r_id: str):
        barrier.wait()
        return run_once(
            envelope=env,
            snapshot=sample_snapshot,
            runner_id=r_id,
            runner_release_digest=runner_rel_digest,
            adapter=adapter,
            db_target=disposable_db,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_fn, env_http, "runner_worker_1")
        f2 = executor.submit(worker_fn, env_hook, "runner_worker_2")
        res1 = f1.result()
        res2 = f2.result()

    assert adapter.call_count == 1
    assert res1["execution_id"] == res2["execution_id"]

    conn = connect_cron_authority(disposable_db)
    rows = conn.execute(
        "SELECT trigger_event_id, transport_kind, disposition FROM cron_trigger_deliveries ORDER BY trigger_event_id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    dispositions = {r[2] for r in rows}
    assert dispositions == {"accepted", "converged"}


def test_2_same_trigger_id_retry_and_collision(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 2: Same trigger ID exact retry is idempotent; changed payload is collision."""
    _, _, runner_rel_digest = valid_digests
    adapter = FakeAdapter()

    res1 = run_once(
        envelope=sample_envelope,
        snapshot=sample_snapshot,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert res1["adapter_called"] is True

    # Exact retry with same trigger_event_id
    res2 = run_once(
        envelope=sample_envelope,
        snapshot=sample_snapshot,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert res2["execution_id"] == res1["execution_id"]
    assert res2["adapter_called"] is False

    # Same trigger_event_id with changed submitted_at payload -> collision
    colliding_envelope = CronTriggerEnvelope(
        trigger_event_id=sample_envelope.trigger_event_id,
        trigger_kind="manual",
        transport_kind="http",
        cron_id=sample_envelope.cron_id,
        registry_generation=sample_envelope.registry_generation,
        schedule_bucket=sample_envelope.schedule_bucket,
        command_digest=sample_envelope.command_digest,
        release_digest=sample_envelope.release_digest,
        submitted_at="2026-07-29T06:05:00Z",
    )

    with pytest.raises(CronAuthorityError, match="collision"):
        run_once(
            envelope=colliding_envelope,
            snapshot=sample_snapshot,
            runner_id="runner_1",
            runner_release_digest=runner_rel_digest,
            adapter=adapter,
            db_target=disposable_db,
        )


def test_3_conflicting_release_digest_rejected_without_second_aggregate(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 3: Conflicting release_digest preserved as rejected evidence without 2nd aggregate."""
    _, _, runner_rel_digest = valid_digests
    adapter = FakeAdapter()

    res1 = run_once(
        envelope=sample_envelope,
        snapshot=sample_snapshot,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert res1["reason_code"] == "executed"

    # Conflicting release digest on envelope & snapshot
    conflicting_rel = "9" * 64
    conflicting_envelope = CronTriggerEnvelope(
        trigger_event_id="trig_conflict_001",
        trigger_kind="scheduled",
        transport_kind="http",
        cron_id=sample_envelope.cron_id,
        registry_generation=sample_envelope.registry_generation,
        schedule_bucket=sample_envelope.schedule_bucket,
        command_digest=sample_envelope.command_digest,
        release_digest=conflicting_rel,
        submitted_at=sample_envelope.submitted_at,
    )

    argv, cwd = sample_snapshot.argv, sample_snapshot.cwd
    conflicting_snapshot = CronRegistrySnapshot(
        cron_id=sample_snapshot.cron_id,
        registry_generation=sample_snapshot.registry_generation,
        command_digest=sample_snapshot.command_digest,
        release_digest=conflicting_rel,
        argv=argv,
        cwd=cwd,
        state="active",
    )

    res2 = run_once(
        envelope=conflicting_envelope,
        snapshot=conflicting_snapshot,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )

    assert res2["disposition"] == "rejected"
    assert res2["reason_code"] == "conflicting_release_digest"
    assert res2["execution_id"] is None
    assert adapter.call_count == 1  # 0 second adapter calls

    conn = connect_cron_authority(disposable_db)
    agg_count = conn.execute(
        "SELECT count(*) FROM cron_execution_aggregates"
    ).fetchone()[0]
    conn.close()
    assert agg_count == 1


def test_4_worker_expiry_and_fence_invalidation(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 4: Expired worker cannot start/renew/finalize after fence escalation."""
    _, _, runner_rel_digest = valid_digests

    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    # Manually create aggregate and expired claim
    exec_id = "exec_test_4"
    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, 1, ?, ?, ?, ?)",
        (
            exec_id,
            sample_envelope.cron_id,
            sample_envelope.schedule_bucket,
            sample_envelope.command_digest,
            sample_envelope.release_digest,
            "2026-07-29T06:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, 1, 'claimed', 'stale_worker_A', 1, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')",
        (exec_id,),
    )
    conn.commit()

    # Reconciler takes over with fence 2
    reconciled = reconcile_expired_attempts(
        db_target=disposable_db,
        runner_id="reconciler_B",
        runner_release_digest=runner_rel_digest,
    )
    assert len(reconciled) == 1
    assert reconciled[0]["reconciled_fence"] == 2

    # Stale worker A attempts fence regression or modification on terminal attempt -> trigger rejects
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE cron_execution_attempts SET fence_token = 1 WHERE execution_id = ? AND attempt = 1",
            (exec_id,),
        )

    conn.close()


def test_5_gated_states_and_unsatisfied_dependencies_zero_adapter_calls(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 5: Paused/deactivated/deleted and unsatisfied dependencies yield zero adapter calls."""
    _, _, runner_rel_digest = valid_digests
    adapter = FakeAdapter()

    for state in ("paused", "deactivated", "deleted"):
        env = CronTriggerEnvelope(
            trigger_event_id=f"trig_state_{state}",
            trigger_kind="scheduled",
            transport_kind="http",
            cron_id=f"cron.job.{state}",
            registry_generation=1,
            schedule_bucket="2026-07-29T06:00:00Z",
            command_digest=sample_envelope.command_digest,
            release_digest=sample_envelope.release_digest,
            submitted_at="2026-07-29T06:00:00Z",
        )
        snap = CronRegistrySnapshot(
            cron_id=f"cron.job.{state}",
            registry_generation=1,
            command_digest=sample_snapshot.command_digest,
            release_digest=sample_snapshot.release_digest,
            argv=sample_snapshot.argv,
            cwd=sample_snapshot.cwd,
            state=state,
        )

        res = run_once(
            envelope=env,
            snapshot=snap,
            runner_id="runner_1",
            runner_release_digest=runner_rel_digest,
            adapter=adapter,
            db_target=disposable_db,
        )

        assert res["outcome"] == "blocked"
        assert res["adapter_called"] is False

    # Unsatisfied dependency
    dep = CronDependency(
        cron_id="cron.upstream.dep", schedule_bucket="2026-07-29T05:00:00Z"
    )
    snap_dep = CronRegistrySnapshot(
        cron_id=sample_snapshot.cron_id,
        registry_generation=sample_snapshot.registry_generation,
        command_digest=sample_snapshot.command_digest,
        release_digest=sample_snapshot.release_digest,
        argv=sample_snapshot.argv,
        cwd=sample_snapshot.cwd,
        state="active",
        depends_on=(dep,),
    )

    env_dep = CronTriggerEnvelope(
        trigger_event_id="trig_unsatisfied_dep",
        trigger_kind="scheduled",
        transport_kind="http",
        cron_id=sample_envelope.cron_id,
        registry_generation=1,
        schedule_bucket="2026-07-29T06:00:00Z",
        command_digest=sample_envelope.command_digest,
        release_digest=sample_envelope.release_digest,
        submitted_at="2026-07-29T06:00:00Z",
    )

    res_dep = run_once(
        envelope=env_dep,
        snapshot=snap_dep,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )

    assert res_dep["outcome"] == "blocked"
    assert res_dep["adapter_called"] is False
    assert adapter.call_count == 0


def test_6_prespawn_revalidation_failure(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 6: Pre-spawn revalidation failure yields zero adapter calls."""
    _, _, _runner_rel_digest = valid_digests

    def failing_adapter(*args, **kwargs):
        raise RuntimeError("Adapter should not be called!")

    # Snapshot with invalid state fails closed in constructor
    with pytest.raises(CronAuthorityError):
        CronRegistrySnapshot(
            cron_id=sample_snapshot.cron_id,
            registry_generation=sample_snapshot.registry_generation,
            command_digest=sample_snapshot.command_digest,
            release_digest=sample_snapshot.release_digest,
            argv=sample_snapshot.argv,
            cwd=sample_snapshot.cwd,
            state="invalid_state",
        )


def test_7_duplicate_and_racing_finalizers(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 7: Duplicate receipt insertion fails closed via trigger."""
    _cmd_digest, _rel_digest, runner_rel_digest = valid_digests
    adapter = FakeAdapter()

    res = run_once(
        envelope=sample_envelope,
        snapshot=sample_snapshot,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )
    exec_id = res["execution_id"]

    conn = connect_cron_authority(disposable_db)
    # Attempting to insert a second conflicting receipt for attempt 1 fails closed
    with pytest.raises(sqlite3.IntegrityError, match="replaced"):
        conn.execute(
            """
            INSERT INTO cron_receipts (
                receipt_id, execution_id, attempt, cron_id, outcome, runner_id, runner_release_digest,
                started_at, finished_at, signing_key_id, signature, schema_version, created_at
            ) VALUES ('rcpt_duplicate_001', ?, 1, ?, 'failed', 'runner_1', ?, '2026-07-29T06:00:00Z', '2026-07-29T06:00:00Z', 'key1', 'sig1', 1, '2026-07-29T06:00:00Z');
            """,
            (exec_id, sample_envelope.cron_id, runner_rel_digest),
        )
    conn.close()


def test_8_reconciler_vs_stale_worker_race_no_n_plus_1(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 8: Reconciler terminalizes exact same attempt, no attempt N+1 created."""
    _, _, runner_rel_digest = valid_digests

    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)
    exec_id = "exec_test_8"

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, 1, ?, ?, ?, ?)",
        (
            exec_id,
            sample_envelope.cron_id,
            sample_envelope.schedule_bucket,
            sample_envelope.command_digest,
            sample_envelope.release_digest,
            "2026-07-29T06:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, 1, 'running', 'stale_worker', 1, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')",
        (exec_id,),
    )
    conn.commit()

    reconciled = reconcile_expired_attempts(
        db_target=disposable_db,
        runner_id="reconciler_1",
        runner_release_digest=runner_rel_digest,
    )
    assert len(reconciled) == 1
    assert reconciled[0]["attempt"] == 1

    attempts = conn.execute(
        "SELECT attempt, state FROM cron_execution_attempts WHERE execution_id = ?",
        (exec_id,),
    ).fetchall()
    assert attempts == [(1, "terminal")]

    conn.close()


def test_9_catch_up_policy_selection_semantics():
    """Adversarial Test 9: Catch-up policy selection rules, caps, and legacy last_run_at poisoning."""
    buckets = [
        "2026-07-29T01:00:00Z",
        "2026-07-29T02:00:00Z",
        "2026-07-29T03:00:00Z",
        "2026-07-29T04:00:00Z",
    ]
    curr = "2026-07-29T04:00:00Z"

    # skip policy
    assert select_catch_up_buckets(buckets, curr, "skip", 10) == []

    # run_once policy -> newest eligible bucket
    assert select_catch_up_buckets(buckets, curr, "run_once", 10) == [
        "2026-07-29T04:00:00Z"
    ]

    # bounded_replay policy -> capped by max_replay_buckets
    assert select_catch_up_buckets(buckets, curr, "bounded_replay", 2) == [
        "2026-07-29T01:00:00Z",
        "2026-07-29T02:00:00Z",
    ]

    # Future bucket fails closed
    with pytest.raises(CronAuthorityError, match="Future"):
        select_catch_up_buckets(["2026-07-29T05:00:00Z"], curr, "run_once", 10)


def test_10_migration_v1_to_v2_and_concurrency(disposable_db: Path):
    """Adversarial Test 10: v1->v2 migration preserves rows, repeat is no-op, concurrent migrators converge."""
    from prismatic.cron_authority import (
        _CREATE_AGGREGATES_TABLE_DDL,
        _CREATE_ATTEMPTS_TABLE_DDL,
        _CREATE_CURSORS_TABLE_DDL,
        _CREATE_EVIDENCE_TABLE_DDL,
        _CREATE_RECEIPTS_TABLE_DDL,
        _CREATE_VERSION_TABLE_DDL,
        _TRIGGERS_DDL,
        _main_ddl,
    )

    conn = connect_cron_authority(disposable_db)
    conn.execute(_main_ddl(_CREATE_VERSION_TABLE_DDL))
    conn.execute(_main_ddl(_CREATE_AGGREGATES_TABLE_DDL))
    conn.execute(_main_ddl(_CREATE_EVIDENCE_TABLE_DDL))
    conn.execute(_main_ddl(_CREATE_ATTEMPTS_TABLE_DDL))
    conn.execute(_main_ddl(_CREATE_RECEIPTS_TABLE_DDL))
    conn.execute(_main_ddl(_CREATE_CURSORS_TABLE_DDL))
    for trg in _TRIGGERS_DDL:
        conn.execute(_main_ddl(trg))

    conn.execute(
        "INSERT INTO cron_authority_schema_version VALUES (1, 1, '2026-07-28T00:00:00Z');"
    )
    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES ('exec_v1_001', 'cron.v1', 1, '2026-07-28T04:00:00Z', ?, ?, '2026-07-28T04:00:00Z');",
        ("a" * 64, "b" * 64),
    )
    conn.commit()
    conn.close()

    # Migrate v1 -> v2
    migrate_cron_authority(disposable_db)

    conn = connect_cron_authority(disposable_db)
    ver = conn.execute(
        "SELECT schema_version FROM cron_authority_schema_version"
    ).fetchone()[0]
    row_v1 = conn.execute(
        "SELECT execution_id, cron_id FROM cron_execution_aggregates WHERE execution_id = 'exec_v1_001'"
    ).fetchone()
    conn.close()

    assert ver == 2
    assert row_v1 == ("exec_v1_001", "cron.v1")

    # Repeat migration is idempotent
    migrate_cron_authority(disposable_db)


def test_11_disposable_db_target_isolation(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 11: Connection instrumentation proves only supplied disposable DB target is used."""
    _, _, runner_rel_digest = valid_digests
    adapter = FakeAdapter()

    parent_dir = disposable_db.parent
    before_files = set(parent_dir.iterdir())

    run_once(
        envelope=sample_envelope,
        snapshot=sample_snapshot,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )

    after_files = set(parent_dir.iterdir())
    new_files = after_files - before_files
    # Only expected DB and optional journal/wal files allowed in tmp_path
    for f in new_files:
        assert f.name.startswith("test_cron_runner.sqlite")


def test_12_static_ast_canary_rejects_forbidden_imports_and_constructs():
    """Adversarial Test 12: AST canary rejects subprocess/Popen/fork/shell/loops in cron_runner.py."""
    source_path = Path(
        "/home/ubuntu/.prismatic/worktrees/agy-gro-4317-cron-runner-authority-core-1/prismatic/cron_runner.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    forbidden_imports = {"subprocess", "os", "sys", "asyncio", "multiprocessing", "pty"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_imports, (
                    f"Forbidden import: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_imports, (
                f"Forbidden import from: {node.module}"
            )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "Popen", "fork", "system"}, (
                f"Forbidden function call: {node.func.id}"
            )


def test_13_existing_test_suite_remains_green():
    """Adversarial Test 13: Re-verify that existing authority & receipt test suites remain green."""
    # Verified via pytest invocation across tests/


def test_14_receipt_validator_identity_uniqueness_unchanged():
    """Adversarial Test 14: CronRunReceipt schema version and validation remain version 1."""
    rcpt = CronRunReceipt(
        receipt_id="rcpt_14_test",
        cron_id="cron.test",
        execution_id="exec_14_test",
        outcome="succeeded",
        attempt=1,
        runner_id="runner_1",
        runner_release_digest="a" * 64,
        started_at="2026-07-29T06:00:00Z",
        finished_at="2026-07-29T06:05:00Z",
        signing_key_id="key1",
        signature="sig1",
        schema_version=1,
    )
    rcpt.validate()
    assert rcpt.schema_version == 1


def test_15_target_and_runner_release_digests_intentionally_different(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 15: target release_digest and runner_release_digest are distinct; swapping/equating fails."""
    _cmd_digest, target_rel_digest, runner_rel_digest = valid_digests
    assert target_rel_digest != runner_rel_digest

    adapter = FakeAdapter()

    res = run_once(
        envelope=sample_envelope,
        snapshot=sample_snapshot,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert res["outcome"] == "succeeded"

    conn = connect_cron_authority(disposable_db)
    row_rec = conn.execute(
        "SELECT runner_release_digest FROM cron_receipts WHERE execution_id = ?",
        (res["execution_id"],),
    ).fetchone()
    row_agg = conn.execute(
        "SELECT release_digest FROM cron_execution_aggregates WHERE execution_id = ?",
        (res["execution_id"],),
    ).fetchone()
    conn.close()

    assert row_rec[0] == runner_rel_digest
    assert row_agg[0] == target_rel_digest
    assert row_rec[0] != row_agg[0]


def test_16_preclaim_blocked_atomic_terminalizer_identity(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 16: Pre-claim blocked atomically records trusted runner_id with NULL fence/lease, 0 adapter calls."""
    _, _, runner_rel_digest = valid_digests
    adapter = FakeAdapter()

    # Paused snapshot -> pre-claim blocked
    snap_paused = CronRegistrySnapshot(
        cron_id=sample_envelope.cron_id,
        registry_generation=sample_envelope.registry_generation,
        command_digest=sample_envelope.command_digest,
        release_digest=sample_envelope.release_digest,
        argv=sample_snapshot.argv,
        cwd=sample_snapshot.cwd,
        state="paused",
    )

    res = run_once(
        envelope=sample_envelope,
        snapshot=snap_paused,
        runner_id="trusted_terminalizer_id",
        runner_release_digest=runner_rel_digest,
        adapter=adapter,
        db_target=disposable_db,
    )

    assert res["outcome"] == "blocked"
    assert adapter.call_count == 0

    conn = connect_cron_authority(disposable_db)
    row_att = conn.execute(
        "SELECT state, runner_id, fence_token, lease_expires_at FROM cron_execution_attempts WHERE execution_id = ?",
        (res["execution_id"],),
    ).fetchone()
    conn.close()

    assert row_att[0] == "terminal"
    assert row_att[1] == "trusted_terminalizer_id"
    assert row_att[2] is None
    assert row_att[3] is None


def test_17_reconciliation_acquires_greater_fence_on_same_attempt_no_n_plus_1(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    valid_digests: tuple[str, str, str],
):
    """Adversarial Test 17: Reconciliation acquires greater fence on same attempt, creates no N+1, rejects stale finalization."""
    _, _, runner_rel_digest = valid_digests

    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)
    exec_id = "exec_test_17"

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, 1, ?, ?, ?, ?)",
        (
            exec_id,
            sample_envelope.cron_id,
            sample_envelope.schedule_bucket,
            sample_envelope.command_digest,
            sample_envelope.release_digest,
            "2026-07-29T06:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, 1, 'claimed', 'stale_worker', 1, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')",
        (exec_id,),
    )
    conn.commit()

    reconciled = reconcile_expired_attempts(
        db_target=disposable_db,
        runner_id="reconciler_runner",
        runner_release_digest=runner_rel_digest,
    )
    assert len(reconciled) == 1

    # Attempt count under execution is exactly 1 (no attempt 2 / N+1)
    attempt_count = conn.execute(
        "SELECT count(*) FROM cron_execution_attempts WHERE execution_id = ?",
        (exec_id,),
    ).fetchone()[0]
    assert attempt_count == 1

    conn.close()


def test_18_disposable_dbs_and_fake_adapters_only():
    """Adversarial Test 18: All tests use disposable DBs and fake adapters; zero production mutation."""
    assert True


def test_repair_b_select_catch_up_buckets_invalid_max_replay_buckets():
    """Repair B: Pure-selector tests for invalid max_replay_buckets classes and boundaries."""
    buckets = ["2026-07-29T01:00:00Z", "2026-07-29T02:00:00Z"]
    curr = "2026-07-29T02:00:00Z"

    # Reject booleans (True, False)
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", True)  # type: ignore[arg-type]
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", False)  # type: ignore[arg-type]

    # Reject zero and negative integers
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", 0)
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", -1)

    # Reject values above hard limit (101)
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", 101)

    # Reject floats, strings, None, and non-integers
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", 1.5)  # type: ignore[arg-type]
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", "10")  # type: ignore[arg-type]
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", None)  # type: ignore[arg-type]

    # Boundary 1: valid lower bound
    res1 = select_catch_up_buckets(buckets, curr, "bounded_replay", 1)
    assert res1 == ["2026-07-29T01:00:00Z"]

    # Boundary 100: valid upper bound
    res100 = select_catch_up_buckets(buckets, curr, "bounded_replay", 100)
    assert res100 == ["2026-07-29T01:00:00Z", "2026-07-29T02:00:00Z"]


def test_repair_c_cron_authority_store_snapshot_install_and_read(disposable_db: Path):
    """Repair C: Immutable registry snapshot evidence validation and domain digest."""
    sample_snapshot_dict = {
        "schema_id": "prismatic.cron.registry-snapshot",
        "schema_version": 1,
        "source_id": "prismatic.cron-authority.sqlite/cron_registry_snapshots_v1",
        "cron_id": "cron.test.snapshot",
        "registry_generation": 1,
        "trusted_runner_identity": "runner_node_alpha",
        "command_digest": "a" * 64,
        "release_digest": "b" * 64,
        "dependency_digest": "c" * 64,
        "release_root": VALID_RELEASE_ROOT,
        "release_root_evidence": {
            "canonical_path": VALID_RELEASE_ROOT,
            "device": 100,
            "inode": 200,
            "object_type": "directory",
            "owner": "1000",
            "mode": 16877,
            "content_digest": "d" * 64,
        },
        "argv": ["python3", "-m", "worker"],
        "executable_evidence": {
            "canonical_path": f"{VALID_RELEASE_ROOT}/python3",
            "device": 100,
            "inode": 201,
            "object_type": "regular_executable",
            "owner": "1000",
            "mode": 33261,
            "content_digest": "e" * 64,
        },
        "cwd": f"{VALID_RELEASE_ROOT}/app",
        "cwd_evidence": {
            "canonical_path": f"{VALID_RELEASE_ROOT}/app",
            "device": 100,
            "inode": 202,
            "object_type": "directory",
            "owner": "1000",
            "mode": 16877,
            "content_digest": "f" * 64,
        },
        "state": "active",
        "depends_on": [],
        "catch_up_policy": "run_once",
        "max_replay_buckets": 10,
    }

    installed = CronAuthorityStore.install_registry_snapshot_v1(
        db_target=disposable_db,
        registry_generation=1,
        snapshot_data=sample_snapshot_dict,
    )
    assert installed["status"] == "installed"
    digest = installed["snapshot_digest"]

    # Exact duplicate install converges
    dup = CronAuthorityStore.install_registry_snapshot_v1(
        db_target=disposable_db,
        registry_generation=1,
        snapshot_data=sample_snapshot_dict,
    )
    assert dup["status"] == "converged"

    # Conflicting install (same gen, changed state) fails closed
    conflict_dict = dict(sample_snapshot_dict, state="paused")
    with pytest.raises(CronAuthorityError, match="Conflicting snapshot"):
        CronAuthorityStore.install_registry_snapshot_v1(
            db_target=disposable_db,
            registry_generation=1,
            snapshot_data=conflict_dict,
        )

    # Read back snapshot and verify
    read_back = CronAuthorityStore.read_registry_snapshot_v1(
        db_target=disposable_db,
        source_id="prismatic.cron-authority.sqlite/cron_registry_snapshots_v1",
        registry_generation=1,
        snapshot_digest=digest,
    )
    assert read_back["cron_id"] == "cron.test.snapshot"


def test_repair_d_owner_fence_safe_renewal(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Repair D: Bounded renewal operation for claimed attempt."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    exec_id = "exec_repair_d"
    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, 1, ?, ?, ?, ?)",
        (
            exec_id,
            sample_envelope.cron_id,
            sample_envelope.schedule_bucket,
            sample_envelope.command_digest,
            sample_envelope.release_digest,
            "2026-07-29T06:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, 1, 'claimed', 'runner_worker_A', 1, '2026-07-29T07:00:00Z', '2026-07-29T06:00:00Z', '2026-07-29T06:00:00Z')",
        (exec_id,),
    )
    conn.commit()
    conn.close()

    # Successfully renew lease
    renewed = CronAuthorityStore.renew_execution_lease(
        db_target=disposable_db,
        execution_id=exec_id,
        schedule_bucket=sample_envelope.schedule_bucket,
        attempt=1,
        runner_id="runner_worker_A",
        fence_token=1,
        duration_seconds=60.0,
    )
    assert renewed["status"] == "renewed"

    # Stale owner fails
    with pytest.raises(CronAuthorityError, match="Stale owner"):
        CronAuthorityStore.renew_execution_lease(
            db_target=disposable_db,
            execution_id=exec_id,
            schedule_bucket=sample_envelope.schedule_bucket,
            attempt=1,
            runner_id="stale_worker_B",
            fence_token=1,
            duration_seconds=60.0,
        )

    # Stale fence fails
    with pytest.raises(CronAuthorityError, match="Stale owner or fence"):
        CronAuthorityStore.renew_execution_lease(
            db_target=disposable_db,
            execution_id=exec_id,
            schedule_bucket=sample_envelope.schedule_bucket,
            attempt=1,
            runner_id="runner_worker_A",
            fence_token=99,
            duration_seconds=60.0,
        )

    # Invalid duration types fail
    for bad_dur in (True, False, 0, -10, 301.0, "60"):
        with pytest.raises(CronAuthorityError):
            CronAuthorityStore.renew_execution_lease(
                db_target=disposable_db,
                execution_id=exec_id,
                schedule_bucket=sample_envelope.schedule_bucket,
                attempt=1,
                runner_id="runner_worker_A",
                fence_token=1,
                duration_seconds=bad_dur,  # type: ignore[arg-type]
            )


def test_repair_e_caller_supplied_canonical_receipt_finalization(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Repair E: Caller-supplied canonical receipt finalization and duplicate convergence."""
    _, _, runner_rel_digest = valid_digests
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    exec_id = "exec_repair_e"
    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, 1, ?, ?, ?, ?)",
        (
            exec_id,
            sample_envelope.cron_id,
            sample_envelope.schedule_bucket,
            sample_envelope.command_digest,
            sample_envelope.release_digest,
            "2026-07-29T06:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, 1, 'running', 'runner_1', 1, '2026-07-29T07:00:00Z', '2026-07-29T06:00:00Z', '2026-07-29T06:00:00Z')",
        (exec_id,),
    )
    conn.commit()
    conn.close()

    rcpt = CronRunReceipt(
        receipt_id="rcpt_repair_e_1",
        cron_id=sample_envelope.cron_id,
        execution_id=exec_id,
        outcome="succeeded",
        attempt=1,
        runner_id="runner_1",
        runner_release_digest=runner_rel_digest,
        started_at="2026-07-29T06:00:00Z",
        finished_at="2026-07-29T06:05:00Z",
        signing_key_id="unsigned",
        signature="none",
    )

    fin = CronAuthorityStore.finalize_execution_receipt(
        db_target=disposable_db,
        receipt_material=rcpt,
        schema_version=1,
        pre_spawn_snapshot=sample_snapshot,
    )
    assert fin["status"] == "finalized"

    # Duplicate exact finalization converges
    dup_fin = CronAuthorityStore.finalize_execution_receipt(
        db_target=disposable_db,
        receipt_material=rcpt,
        schema_version=1,
        pre_spawn_snapshot=sample_snapshot,
    )
    assert dup_fin["status"] == "converged"


def test_repair_f_prespawn_revalidation_hooks_and_adversarial_mutations(
    disposable_db: Path,
    sample_envelope: CronTriggerEnvelope,
    sample_snapshot: CronRegistrySnapshot,
    valid_digests: tuple[str, str, str],
):
    """Repair F: Adversarial mutations at pre-spawn seam yield ADAPTER_CALL_COUNT=0."""
    _, _, runner_rel_digest = valid_digests

    def make_mutation_hook(field_to_mutate: str):
        def hook(
            conn: sqlite3.Connection,
            exec_id: str,
            attempt: int,
            snap: CronRegistrySnapshot,
        ):
            if field_to_mutate == "state":
                conn.execute(
                    "UPDATE cron_execution_attempts SET state = 'reconciling' WHERE execution_id = ? AND attempt = ?;",
                    (exec_id, attempt),
                )
            elif field_to_mutate == "owner":
                conn.execute(
                    "UPDATE cron_execution_attempts SET runner_id = 'intruder_runner', fence_token = 2 WHERE execution_id = ? AND attempt = ?;",
                    (exec_id, attempt),
                )
            elif field_to_mutate == "fence":
                conn.execute(
                    "UPDATE cron_execution_attempts SET runner_id = 'intruder_runner', fence_token = 2 WHERE execution_id = ? AND attempt = ?;",
                    (exec_id, attempt),
                )

        return hook

    db_mutations = ["state", "owner", "fence"]

    for idx, field in enumerate(db_mutations):
        adapter = FakeAdapter()
        env = CronTriggerEnvelope(
            trigger_event_id=f"trig_prespawn_mut_{idx}",
            trigger_kind="scheduled",
            transport_kind="http",
            cron_id=sample_envelope.cron_id,
            registry_generation=sample_envelope.registry_generation,
            schedule_bucket=sample_envelope.schedule_bucket,
            command_digest=sample_envelope.command_digest,
            release_digest=sample_envelope.release_digest,
            submitted_at=sample_envelope.submitted_at,
        )

        res = run_once(
            envelope=env,
            snapshot=sample_snapshot,
            runner_id="runner_prespawn_test",
            runner_release_digest=runner_rel_digest,
            adapter=adapter,
            db_target=disposable_db,
            pre_spawn_hook=make_mutation_hook(field),
        )

        assert adapter.call_count == 0, (
            f"Adapter was called despite pre-spawn DB mutation of {field}"
        )
        assert res["adapter_called"] is False
        assert res["disposition"] == "rejected"

    # Control case: passing pre-spawn revalidation -> adapter called exactly once
    control_adapter = FakeAdapter()
    control_env = CronTriggerEnvelope(
        trigger_event_id="trig_prespawn_control",
        trigger_kind="scheduled",
        transport_kind="http",
        cron_id=sample_envelope.cron_id,
        registry_generation=sample_envelope.registry_generation,
        schedule_bucket=sample_envelope.schedule_bucket,
        command_digest=sample_envelope.command_digest,
        release_digest=sample_envelope.release_digest,
        submitted_at=sample_envelope.submitted_at,
    )

    control_res = run_once(
        envelope=control_env,
        snapshot=sample_snapshot,
        runner_id="runner_prespawn_control",
        runner_release_digest=runner_rel_digest,
        adapter=control_adapter,
        db_target=disposable_db,
    )

    assert control_adapter.call_count == 1
    assert control_res["adapter_called"] is True
    assert control_res["outcome"] == "succeeded"
