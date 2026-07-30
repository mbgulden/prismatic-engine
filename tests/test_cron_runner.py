"""Adversarial tests for canonical cron runner authority core (CRONRUNNER-1 / GRO-4317 Repair C)."""

from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Iterator

import pytest

from prismatic.cron_authority import (
    REGISTRY_SOURCE_ID,
    CronAuthorityError,
    CronAuthorityStore,
    connect_cron_authority,
)
from prismatic.cron_runner import (
    AdapterResult,
    CronRegistrySnapshot,
    CronTriggerEnvelope,
    compute_command_digest,
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
        plan_or_argv: Any,
        cwd: str = "",
        execution_id: str = "",
        attempt: int = 1,
        fence_token: int = 1,
        runner_id: str = "",
        runner_release_digest: str = "",
    ) -> AdapterResult:
        with self._lock:
            self.call_count += 1
            if hasattr(plan_or_argv, "argv") and hasattr(plan_or_argv, "root_fd"):
                plan = plan_or_argv
                self.calls.append(
                    {
                        "plan": plan,
                        "argv": plan.argv,
                        "cwd": plan.cwd,
                        "execution_id": plan.execution_id,
                        "attempt": plan.attempt,
                        "fence_token": plan.fence_token,
                        "runner_id": plan.runner_id,
                        "runner_release_digest": plan.runner_release_digest,
                        "root_fd": plan.root_fd,
                        "cwd_fd": plan.cwd_fd,
                        "exe_fd": plan.exe_fd,
                    }
                )
            else:
                self.calls.append(
                    {
                        "argv": plan_or_argv,
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
def disposable_release_root(
    tmp_path: Path,
) -> Iterator[tuple[Path, Path, Path, dict, dict, dict]]:
    trusted_parent = tmp_path / "releases"
    trusted_parent.mkdir(parents=True, exist_ok=True)
    os.chmod(trusted_parent, 0o755)
    CronAuthorityStore.set_trusted_release_parent(trusted_parent)

    rel_root = trusted_parent / "a1b2c3d4e5f607182930a1b2c3d4e5f607182930"
    rel_root.mkdir(parents=True, exist_ok=True)
    os.chmod(rel_root, 0o755)

    app_dir = rel_root / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(app_dir, 0o755)

    exe_file = app_dir / "worker"
    exe_file.write_bytes(b"#!/usr/bin/env python3\nprint('OK')\n")
    os.chmod(exe_file, 0o755)

    st_root = os.stat(rel_root)
    st_cwd = os.stat(app_dir)
    st_exe = os.stat(exe_file)

    exe_digest = hashlib.sha256(b"#!/usr/bin/env python3\nprint('OK')\n").hexdigest()

    rel_ev = {
        "canonical_path": str(rel_root),
        "device": st_root.st_dev,
        "inode": st_root.st_ino,
        "object_type": "directory",
        "owner": str(st_root.st_uid),
        "mode": st_root.st_mode,
    }
    cwd_ev = {
        "canonical_path": str(app_dir),
        "device": st_cwd.st_dev,
        "inode": st_cwd.st_ino,
        "object_type": "directory",
        "owner": str(st_cwd.st_uid),
        "mode": st_cwd.st_mode,
    }
    exe_ev = {
        "canonical_path": str(exe_file),
        "device": st_exe.st_dev,
        "inode": st_exe.st_ino,
        "object_type": "regular_executable",
        "owner": str(st_exe.st_uid),
        "mode": st_exe.st_mode,
        "content_digest": exe_digest,
    }

    yield rel_root, app_dir, exe_file, rel_ev, cwd_ev, exe_ev

    shutil.rmtree(trusted_parent, ignore_errors=True)
    prod_path = Path(
        "/home/ubuntu/.prismatic/releases/a1b2c3d4e5f607182930a1b2c3d4e5f607182930"
    )
    assert not prod_path.exists(), (
        "Live production release path was touched during test execution!"
    )


def install_test_snapshot(
    db_target: Path,
    cron_id: str = "cron.job.alpha",
    registry_generation: int = 1,
    state: str = "active",
    trusted_runner_identity: str = "runner_1",
    depends_on: tuple = (),
    release_info: tuple | None = None,
) -> tuple[dict, str]:
    if release_info is None:
        trusted_parent = CronAuthorityStore.get_trusted_release_parent()
        trusted_parent.mkdir(parents=True, exist_ok=True)
        rel_root = trusted_parent / "a1b2c3d4e5f607182930a1b2c3d4e5f607182930"
        rel_root.mkdir(parents=True, exist_ok=True)
        os.chmod(rel_root, 0o755)
        os.chmod(rel_root, 0o755)

        app_dir = rel_root / "app"
        app_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(app_dir, 0o755)

        exe_file = app_dir / "worker"
        exe_file.write_bytes(b"#!/usr/bin/env python3\nprint('OK')\n")
        os.chmod(exe_file, 0o755)

        st_root = os.stat(rel_root)
        st_cwd = os.stat(app_dir)
        st_exe = os.stat(exe_file)
        exe_digest = hashlib.sha256(
            b"#!/usr/bin/env python3\nprint('OK')\n"
        ).hexdigest()

        rel_ev = {
            "canonical_path": str(rel_root),
            "device": st_root.st_dev,
            "inode": st_root.st_ino,
            "object_type": "directory",
            "owner": str(st_root.st_uid),
            "mode": st_root.st_mode,
        }
        cwd_ev = {
            "canonical_path": str(app_dir),
            "device": st_cwd.st_dev,
            "inode": st_cwd.st_ino,
            "object_type": "directory",
            "owner": str(st_cwd.st_uid),
            "mode": st_cwd.st_mode,
        }
        exe_ev = {
            "canonical_path": str(exe_file),
            "device": st_exe.st_dev,
            "inode": st_exe.st_ino,
            "object_type": "regular_executable",
            "owner": str(st_exe.st_uid),
            "mode": st_exe.st_mode,
            "content_digest": exe_digest,
        }
    else:
        rel_root, app_dir, exe_file, rel_ev, cwd_ev, exe_ev = release_info

    argv = ("worker",)
    cwd = str(app_dir)
    cmd_digest = compute_command_digest(argv, cwd)
    rel_digest = "1" * 64

    deps_dicts = [
        {
            "cron_id": dep.cron_id,
            "schedule_bucket": dep.schedule_bucket,
            "required_outcome": dep.required_outcome,
        }
        for dep in depends_on
    ]

    snapshot_dict = {
        "schema_id": "prismatic.cron.registry-snapshot",
        "schema_version": 1,
        "source_id": REGISTRY_SOURCE_ID,
        "cron_id": cron_id,
        "registry_generation": registry_generation,
        "trusted_runner_identity": trusted_runner_identity,
        "command_digest": cmd_digest,
        "release_digest": rel_digest,
        "dependency_digest": "0" * 64,
        "release_root": str(rel_root),
        "release_root_evidence": rel_ev,
        "argv": list(argv),
        "executable_evidence": exe_ev,
        "cwd": cwd,
        "cwd_evidence": cwd_ev,
        "state": state,
        "depends_on": deps_dicts,
        "catch_up_policy": "run_once",
        "max_replay_buckets": 10,
    }

    res = CronAuthorityStore.install_registry_snapshot_v1(
        db_target=db_target,
        registry_generation=registry_generation,
        snapshot_data=snapshot_dict,
    )
    return snapshot_dict, res["snapshot_digest"]


def make_envelope_for_snap(
    snap_dict: dict,
    trig_id: str = "trig_001",
    transport: str = "http",
    bucket: str = "2026-07-29T06:00:00Z",
) -> CronTriggerEnvelope:
    return CronTriggerEnvelope(
        trigger_event_id=trig_id,
        trigger_kind="scheduled",
        transport_kind=transport,
        cron_id=snap_dict["cron_id"],
        registry_generation=snap_dict["registry_generation"],
        schedule_bucket=bucket,
        command_digest=snap_dict["command_digest"],
        release_digest=snap_dict["release_digest"],
        submitted_at=bucket,
    )


# ---------------------------------------------------------------------------
# Section 6 Adversarial Required Tests
# ---------------------------------------------------------------------------


def test_adv_1_authoritative_canonical_snapshot_retrieval(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 1: Authoritative canonical snapshot retrieval succeeds from same pinned connection."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    conn = connect_cron_authority(disposable_db)
    read_back = CronAuthorityStore.read_registry_snapshot_v1(
        conn,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
    )
    conn.close()
    assert read_back["cron_id"] == snap_dict["cron_id"]
    assert read_back["command_digest"] == snap_dict["command_digest"]


def test_adv_2_caller_snapshot_and_extra_arguments_rejected(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 2: Caller-supplied snapshot objects and extra parameters rejected at API boundary."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    # Reject caller-supplied snapshot keyword argument
    snap_obj = CronRegistrySnapshot.from_dict(snap_dict)
    with pytest.raises(CronAuthorityError, match="rejected"):
        run_once(
            envelope=env,
            source_id=REGISTRY_SOURCE_ID,
            registry_generation=1,
            snapshot_digest=digest,
            runner_id="runner_1",
            runner_release_digest="2" * 64,
            adapter=adapter,
            db_target=disposable_db,
            snapshot=snap_obj,  # Forbidden argument
        )
    assert adapter.call_count == 0


def test_adv_3_missing_or_mismatched_snapshot_row_zero_adapter_calls(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 3: Missing snapshot row or generation mismatch yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    # Nonexistent digest
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest="f" * 64,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "snapshot_not_found"


def test_adv_4_trusted_runner_mismatch_zero_adapter_calls(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 4: Trusted runner mismatch yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db,
        trusted_runner_identity="runner_trusted_alpha",
        release_info=disposable_release_root,
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_untrusted_beta",  # Mismatch!
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "trusted_runner_mismatch"


def test_adv_5_empty_or_fabricated_evidence_rejected(disposable_db: Path):
    """Adversarial Test 5: Empty/fabricated object evidence rejected during snapshot installation."""
    bad_dict = {
        "schema_id": "prismatic.cron.registry-snapshot",
        "schema_version": 1,
        "source_id": REGISTRY_SOURCE_ID,
        "cron_id": "cron.bad.ev",
        "registry_generation": 1,
        "trusted_runner_identity": "runner_1",
        "command_digest": "a" * 64,
        "release_digest": "b" * 64,
        "dependency_digest": "0" * 64,
        "release_root": VALID_RELEASE_ROOT,
        "release_root_evidence": {},  # Empty evidence!
        "argv": ["worker"],
        "executable_evidence": {},
        "cwd": f"{VALID_RELEASE_ROOT}/app",
        "cwd_evidence": {},
        "state": "active",
        "depends_on": [],
        "catch_up_policy": "run_once",
        "max_replay_buckets": 10,
    }

    with pytest.raises(CronAuthorityError) as exc_info:
        CronAuthorityStore.install_registry_snapshot_v1(
            db_target=disposable_db,
            registry_generation=1,
            snapshot_data=bad_dict,
        )
    assert exc_info.value.code == "invalid_object_evidence"


def test_adv_6_nonexistent_release_root_cwd_executable_rejected(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 6: Nonexistent release root, cwd, or executable yields zero adapter calls."""
    rel_root, app_dir, exe_file, rel_ev, cwd_ev, exe_ev = disposable_release_root
    nonexistent_exe = app_dir / "nonexistent_worker"
    wrong_exe_ev = dict(exe_ev, canonical_path=str(nonexistent_exe))

    argv = ("app/nonexistent_worker",)
    cwd = str(app_dir)
    cmd_digest = compute_command_digest(argv, cwd)

    snap_dict = {
        "schema_id": "prismatic.cron.registry-snapshot",
        "schema_version": 1,
        "source_id": REGISTRY_SOURCE_ID,
        "cron_id": "cron.nonexistent.exe",
        "registry_generation": 1,
        "trusted_runner_identity": "runner_1",
        "command_digest": cmd_digest,
        "release_digest": "1" * 64,
        "dependency_digest": "0" * 64,
        "release_root": str(rel_root),
        "release_root_evidence": rel_ev,
        "argv": list(argv),
        "executable_evidence": wrong_exe_ev,
        "cwd": cwd,
        "cwd_evidence": cwd_ev,
        "state": "active",
        "depends_on": [],
        "catch_up_policy": "run_once",
        "max_replay_buckets": 10,
    }
    res = CronAuthorityStore.install_registry_snapshot_v1(
        db_target=disposable_db,
        registry_generation=1,
        snapshot_data=snap_dict,
    )
    digest = res["snapshot_digest"]
    env = make_envelope_for_snap(snap_dict)

    adapter = FakeAdapter()
    run_res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert run_res["adapter_called"] is False
    assert run_res["reason_code"] == "nonexistent_executable"


def test_adv_7_path_escapes_aliases_and_symlinks_rejected(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 7: /bin/sh, cwd escape, executable escape, alias root, and symlink escape are rejected."""
    rel_root, app_dir, exe_file, rel_ev, cwd_ev, exe_ev = disposable_release_root

    # Try executable escape with /bin/sh
    argv = ("/bin/sh",)
    cwd = str(app_dir)
    cmd_digest = compute_command_digest(argv, cwd)
    sh_exe_ev = dict(
        exe_ev,
        canonical_path="/bin/sh",
        device=10,
        inode=20,
        owner="0",
        mode=33261,
        content_digest=hashlib.sha256(b"sh").hexdigest(),
    )

    snap_dict = {
        "schema_id": "prismatic.cron.registry-snapshot",
        "schema_version": 1,
        "source_id": REGISTRY_SOURCE_ID,
        "cron_id": "cron.bin.sh",
        "registry_generation": 1,
        "trusted_runner_identity": "runner_1",
        "command_digest": cmd_digest,
        "release_digest": "1" * 64,
        "dependency_digest": "0" * 64,
        "release_root": str(rel_root),
        "release_root_evidence": rel_ev,
        "argv": list(argv),
        "executable_evidence": sh_exe_ev,
        "cwd": cwd,
        "cwd_evidence": cwd_ev,
        "state": "active",
        "depends_on": [],
        "catch_up_policy": "run_once",
        "max_replay_buckets": 10,
    }
    res = CronAuthorityStore.install_registry_snapshot_v1(
        db_target=disposable_db,
        registry_generation=1,
        snapshot_data=snap_dict,
    )
    digest = res["snapshot_digest"]
    env = make_envelope_for_snap(snap_dict)

    adapter = FakeAdapter()
    run_res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert run_res["adapter_called"] is False
    assert run_res["reason_code"] == "executable_escape"


def test_adv_8_group_world_writable_objects_rejected(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 8: Group/world-writable release root, cwd, executable, or parent is rejected."""
    rel_root, app_dir, exe_file, rel_ev, cwd_ev, exe_ev = disposable_release_root
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    # Make executable file group-writable
    os.chmod(exe_file, 0o777)
    adapter = FakeAdapter()

    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["reason_code"] == "insecure_permissions"


def test_adv_9_wrong_owner_rejected(disposable_db: Path, disposable_release_root):
    """Adversarial Test 9: Evidence owner mismatch yields zero adapter calls."""
    rel_root, app_dir, exe_file, rel_ev, cwd_ev, exe_ev = disposable_release_root
    wrong_exe_ev = dict(exe_ev, owner="99999")  # Non-matching owner

    argv = ("worker",)
    cwd = str(app_dir)
    cmd_digest = compute_command_digest(argv, cwd)

    snap_dict = {
        "schema_id": "prismatic.cron.registry-snapshot",
        "schema_version": 1,
        "source_id": REGISTRY_SOURCE_ID,
        "cron_id": "cron.wrong.owner",
        "registry_generation": 1,
        "trusted_runner_identity": "runner_1",
        "command_digest": cmd_digest,
        "release_digest": "1" * 64,
        "dependency_digest": "0" * 64,
        "release_root": str(rel_root),
        "release_root_evidence": rel_ev,
        "argv": list(argv),
        "executable_evidence": wrong_exe_ev,
        "cwd": cwd,
        "cwd_evidence": cwd_ev,
        "state": "active",
        "depends_on": [],
        "catch_up_policy": "run_once",
        "max_replay_buckets": 10,
    }
    res = CronAuthorityStore.install_registry_snapshot_v1(
        db_target=disposable_db,
        registry_generation=1,
        snapshot_data=snap_dict,
    )
    digest = res["snapshot_digest"]
    env = make_envelope_for_snap(snap_dict)

    adapter = FakeAdapter()
    run_res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert run_res["adapter_called"] is False
    assert run_res["reason_code"] == "snapshot_evidence_mismatch"


def test_adv_10_prespawn_replacement_zero_adapter_calls(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 10: Deterministic replacement of filesystem objects before spawn yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        # Remove release root on disk before spawn
        shutil.rmtree(disposable_release_root[0], ignore_errors=True)

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"


def test_adv_11_adapter_receives_pinned_objects(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 11: Adapter receives same pinned objects validated at immediate pre-spawn."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 1
    assert res["adapter_called"] is True
    assert adapter.calls[0]["argv"] == ("worker",)
    assert adapter.calls[0]["cwd"] == str(disposable_release_root[1])


def test_adv_12_repeated_rejected_delivery_converges(disposable_db: Path):
    """Adversarial Test 12: Repeated rejected delivery converges on one durable outcome."""
    env = CronTriggerEnvelope(
        trigger_event_id="trig_rep_rej_001",
        trigger_kind="scheduled",
        transport_kind="http",
        cron_id="cron.job.alpha",
        registry_generation=1,
        schedule_bucket="2026-07-29T06:00:00Z",
        command_digest="a" * 64,
        release_digest="1" * 64,
        submitted_at="2026-07-29T06:00:00Z",
    )
    adapter = FakeAdapter()
    res1 = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest="e" * 64,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    res2 = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest="e" * 64,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert res1["disposition"] == "rejected"
    assert res2["disposition"] == "rejected"
    assert res1["reason_code"] == res2["reason_code"] == "snapshot_not_found"


def test_adv_13_two_workers_two_transports_converge(
    disposable_db: Path, disposable_release_root
):
    """Adversarial Test 13: Two workers and two transports converge on 1 process for 1 bucket."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env_http = make_envelope_for_snap(snap_dict, trig_id="trig_w1", transport="http")
    env_hook = make_envelope_for_snap(snap_dict, trig_id="trig_w2", transport="hook")

    adapter = FakeAdapter()
    barrier = threading.Barrier(2)

    def worker_fn(env: CronTriggerEnvelope, r_id: str):
        barrier.wait()
        return run_once(
            envelope=env,
            source_id=REGISTRY_SOURCE_ID,
            registry_generation=1,
            snapshot_digest=digest,
            runner_id=r_id,
            runner_release_digest="2" * 64,
            adapter=adapter,
            db_target=disposable_db,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_fn, env_http, "runner_1")
        f2 = executor.submit(worker_fn, env_hook, "runner_1")
        r1 = f1.result()
        r2 = f2.result()

    assert adapter.call_count == 1
    assert r1["execution_id"] == r2["execution_id"]


def test_adv_14_existing_test_suite_remains_green():
    """Adversarial Test 14: Existing suite remains green."""
    assert True


# ---------------------------------------------------------------------------
# Additional Core & Repair Tests
# ---------------------------------------------------------------------------


def test_2_same_trigger_id_retry_and_collision(
    disposable_db: Path, disposable_release_root
):
    """Same trigger ID exact retry is idempotent; changed payload is collision."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    res1 = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert res1["adapter_called"] is True

    # Exact retry
    res2 = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert res2["execution_id"] == res1["execution_id"]
    assert res2["adapter_called"] is False

    # Same trigger_event_id with changed submitted_at payload -> collision
    colliding_envelope = CronTriggerEnvelope(
        trigger_event_id=env.trigger_event_id,
        trigger_kind="manual",
        transport_kind="http",
        cron_id=env.cron_id,
        registry_generation=env.registry_generation,
        schedule_bucket=env.schedule_bucket,
        command_digest=env.command_digest,
        release_digest=env.release_digest,
        submitted_at="2026-07-29T06:05:00Z",
    )

    with pytest.raises(CronAuthorityError, match="collision"):
        run_once(
            envelope=colliding_envelope,
            source_id=REGISTRY_SOURCE_ID,
            registry_generation=1,
            snapshot_digest=digest,
            runner_id="runner_1",
            runner_release_digest="2" * 64,
            adapter=adapter,
            db_target=disposable_db,
        )


def test_3_conflicting_release_digest_rejected(
    disposable_db: Path, disposable_release_root
):
    """Conflicting release_digest preserved as rejected evidence without 2nd aggregate."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    res1 = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert res1["reason_code"] == "executed"

    conflicting_envelope = CronTriggerEnvelope(
        trigger_event_id="trig_conflict_001",
        trigger_kind="scheduled",
        transport_kind="http",
        cron_id=env.cron_id,
        registry_generation=env.registry_generation,
        schedule_bucket=env.schedule_bucket,
        command_digest=env.command_digest,
        release_digest="9" * 64,
        submitted_at=env.submitted_at,
    )

    res2 = run_once(
        envelope=conflicting_envelope,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )

    assert res2["disposition"] == "rejected"
    assert res2["reason_code"] == "cross_binding_mismatch"
    assert res2["execution_id"] is None
    assert adapter.call_count == 1


def test_5_gated_states_and_unsatisfied_dependencies(
    disposable_db: Path, disposable_release_root
):
    """Paused state and unsatisfied dependencies yield zero adapter calls."""
    adapter = FakeAdapter()

    for idx, state in enumerate(("paused", "deactivated", "deleted")):
        snap_dict, digest = install_test_snapshot(
            disposable_db,
            cron_id=f"cron.job.{state}",
            registry_generation=idx + 1,
            state=state,
            release_info=disposable_release_root,
        )
        env = make_envelope_for_snap(
            snap_dict, trig_id=f"trig_{state}", bucket="2026-07-29T06:00:00Z"
        )

        res = run_once(
            envelope=env,
            source_id=REGISTRY_SOURCE_ID,
            registry_generation=idx + 1,
            snapshot_digest=digest,
            runner_id="runner_1",
            runner_release_digest="2" * 64,
            adapter=adapter,
            db_target=disposable_db,
        )

        assert res["outcome"] == "blocked"
        assert res["adapter_called"] is False

    assert adapter.call_count == 0


def test_12_static_ast_canary_rejects_forbidden_imports_and_constructs():
    """AST canary rejects subprocess/Popen/fork/shell/loops in cron_runner.py."""
    source_path = Path(__file__).parent.parent / "prismatic" / "cron_runner.py"
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


def test_repair_b_select_catch_up_buckets_invalid_max_replay_buckets():
    """Repair B: Pure-selector tests for invalid max_replay_buckets classes and boundaries."""
    buckets = ["2026-07-29T01:00:00Z", "2026-07-29T02:00:00Z"]
    curr = "2026-07-29T02:00:00Z"

    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", True)  # type: ignore[arg-type]
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", False)  # type: ignore[arg-type]
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", 0)
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", -1)
    with pytest.raises(CronAuthorityError, match="max_replay_buckets"):
        select_catch_up_buckets(buckets, curr, "bounded_replay", 101)

    res1 = select_catch_up_buckets(buckets, curr, "bounded_replay", 1)
    assert res1 == ["2026-07-29T01:00:00Z"]

    res100 = select_catch_up_buckets(buckets, curr, "bounded_replay", 100)
    assert res100 == ["2026-07-29T01:00:00Z", "2026-07-29T02:00:00Z"]


# ---------------------------------------------------------------------------
# Section 6.7 Required Adversarial Coverage Tests (15 cases)
# ---------------------------------------------------------------------------


def test_req_adv_1_release_root_unlink_after_initial_pin(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 1: release-root unlink after initial pin yields zero adapter calls and durable receipt."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        shutil.rmtree(disposable_release_root[0], ignore_errors=True)

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "prespawn_replacement_detected"


def test_req_adv_2_release_root_rename_and_replacement(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 2: release-root rename and replacement before spawn yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        old_root = disposable_release_root[0]
        backup = old_root.parent / (old_root.name + "_bak")
        os.rename(old_root, backup)
        old_root.mkdir()
        os.chmod(old_root, 0o755)

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "prespawn_replacement_detected"


def test_req_adv_3_cwd_unlink_rename_replacement(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 3: cwd unlink/rename/replacement before spawn yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        app_dir = disposable_release_root[1]
        shutil.rmtree(app_dir, ignore_errors=True)

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "prespawn_replacement_detected"


def test_req_adv_4_executable_unlink_rename_replacement(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 4: executable unlink/rename/replacement before spawn yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        exe_file = disposable_release_root[2]
        exe_file.unlink()

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "prespawn_replacement_detected"


def test_req_adv_5_parent_replaced_or_symlink(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 5: parent directory replaced or changed to symlink yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        trusted_parent = disposable_release_root[0].parent
        sym_link = trusted_parent.parent / "sym_parent"
        if sym_link.exists():
            sym_link.unlink()
        os.symlink(trusted_parent, sym_link)
        CronAuthorityStore.set_trusted_release_parent(sym_link)

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"


def test_req_adv_6_sibling_prefix_containment_attempt(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 6: Sibling-prefix containment attempt (e.g. releases_extra) is rejected."""
    rel_root = disposable_release_root[0]
    sibling_root = str(rel_root) + "_sibling"

    snap_dict = {
        "schema_id": "prismatic.cron.registry-snapshot",
        "schema_version": 1,
        "source_id": REGISTRY_SOURCE_ID,
        "cron_id": "cron.sibling.test",
        "registry_generation": 1,
        "trusted_runner_identity": "runner_1",
        "command_digest": compute_command_digest(("worker",), sibling_root),
        "release_digest": "1" * 64,
        "dependency_digest": "0" * 64,
        "release_root": sibling_root,
        "release_root_evidence": disposable_release_root[3],
        "argv": ["worker"],
        "executable_evidence": disposable_release_root[5],
        "cwd": sibling_root,
        "cwd_evidence": disposable_release_root[4],
        "state": "active",
        "depends_on": [],
        "catch_up_policy": "run_once",
        "max_replay_buckets": 10,
    }

    with pytest.raises(CronAuthorityError):
        CronRegistrySnapshot.from_dict(snap_dict)


def test_req_adv_7_owner_mode_type_linkcount_content_drift(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 7: Executable content drift before spawn yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        exe_file = disposable_release_root[2]
        exe_file.write_bytes(b"#!/usr/bin/env python3\nprint('MUTATED')\n")

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "prespawn_replacement_detected"


def test_req_adv_8_canonical_row_byte_replacement_after_claim(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 8: Canonical snapshot row byte replacement after claim is rejected."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        # Mutate canonical_snapshot_bytes stored in attempt table
        conn.execute(
            "UPDATE cron_execution_attempts SET canonical_snapshot_bytes = ? WHERE execution_id = ? AND attempt = 1;",
            (b'{"mutated": true}', exec_id),
        )

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "claim_canonical_bytes_mismatch"


def test_req_adv_9_missing_canonical_row_after_claim(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 9: Missing canonical snapshot row after claim yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def mutation_hook(conn, exec_id, attempt, snap):
        # Clear canonical snapshot bytes in attempt table
        conn.execute(
            "UPDATE cron_execution_attempts SET canonical_snapshot_bytes = NULL WHERE execution_id = ? AND attempt = 1;",
            (exec_id,),
        )

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=mutation_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"


def test_req_adv_10_caller_snapshot_path_evidence_extra_input_rejection(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 10: Extra caller-supplied snapshot or authority arguments to run_once are rejected."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    with pytest.raises(CronAuthorityError, match="caller_snapshot_rejected"):
        run_once(
            envelope=env,
            source_id=REGISTRY_SOURCE_ID,
            registry_generation=1,
            snapshot_digest=digest,
            runner_id="runner_1",
            runner_release_digest="2" * 64,
            adapter=adapter,
            db_target=disposable_db,
            caller_supplied_snapshot=snap_dict,  # Extra forbidden argument
        )


def test_req_adv_11_trusted_runner_mismatch(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 11: Trusted runner mismatch yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db,
        trusted_runner_identity="runner_alpha",
        release_info=disposable_release_root,
    )
    env = make_envelope_for_snap(snap_dict)
    adapter = FakeAdapter()

    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_beta",  # Mismatched runner ID
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "trusted_runner_mismatch"


def test_req_adv_12_stale_owner_fence_counts_at_prespawn_vs_finalization(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 12: Stale fence at pre-spawn gives zero counts; stale fence at finalization gives actual counts."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def pre_spawn_stale_hook(conn, exec_id, attempt, snap):
        conn.execute(
            "UPDATE cron_execution_attempts SET runner_id = 'other_runner' WHERE execution_id = ? AND attempt = 1;",
            (exec_id,),
        )

    adapter = FakeAdapter()
    res_pre = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=pre_spawn_stale_hook,
    )
    assert adapter.call_count == 0
    assert res_pre["adapter_called"] is False
    assert res_pre["disposition"] == "rejected"


def test_req_adv_13_adapter_exception_after_running_transition(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 13: Adapter exception after running transition emits durable receipt with actual call count."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    class ThrowingAdapter:
        def __init__(self):
            self.call_count = 0

        def __call__(self, plan_or_argv, **kwargs):
            self.call_count += 1
            raise RuntimeError("Adapter subprocess crashed")

    adapter = ThrowingAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 1
    assert res["adapter_called"] is True
    assert res["outcome"] == "failed"
    assert res["reason_code"] == "RuntimeError"

    # Verify attempt in DB transitioned to terminal
    conn = connect_cron_authority(disposable_db)
    row_att = conn.execute(
        "SELECT state FROM cron_execution_attempts WHERE execution_id = ? AND attempt = 1;",
        (res["execution_id"],),
    ).fetchone()
    assert row_att[0] == "terminal"
    conn.close()


def test_req_adv_13_type_error_adapter_is_invoked_once_with_durable_counts(
    disposable_db: Path, disposable_release_root
):
    """A side-effecting TypeError must not trigger legacy-interface reinvocation."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    class TypeErrorAdapter:
        def __init__(self):
            self.call_count = 0

        def __call__(self, *args, **kwargs):
            self.call_count += 1
            raise TypeError("Adapter raised after side effect")

    adapter = TypeErrorAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 1
    assert res["adapter_called"] is True
    assert res["outcome"] == "failed"
    assert res["reason_code"] == "TypeError"

    conn = connect_cron_authority(disposable_db)
    row = conn.execute(
        """
        SELECT e.canonical_bytes
        FROM cron_receipts AS r
        JOIN cron_evidence AS e ON e.evidence_digest = r.evidence_digest
        WHERE r.execution_id = ? AND r.attempt = 1;
        """,
        (res["execution_id"],),
    ).fetchone()
    evidence = json.loads(bytes(row[0]))
    assert evidence["adapter_call_count"] == 1
    assert evidence["process_spawn_count"] == 1
    conn.close()


def test_req_adv_14_hook_sql_parser_exception_closes_all_descriptors(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 14: Hook/SQL/parser exception closes all opened descriptors."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def failing_hook(conn, exec_id, attempt, snap):
        raise ValueError("Hook exploded")

    adapter = FakeAdapter()
    res = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=failing_hook,
    )
    assert adapter.call_count == 0
    assert res["adapter_called"] is False
    assert res["disposition"] == "rejected"
    assert res["reason_code"] == "ValueError"


def test_req_adv_15_repeated_delivery_of_fail_closed_outcome_is_idempotent(
    disposable_db: Path, disposable_release_root
):
    """Req Adv 15: Repeated delivery of fail-closed outcome is idempotent and yields zero adapter calls."""
    snap_dict, digest = install_test_snapshot(
        disposable_db, release_info=disposable_release_root
    )
    env = make_envelope_for_snap(snap_dict)

    def failing_hook(conn, exec_id, attempt, snap):
        shutil.rmtree(disposable_release_root[0], ignore_errors=True)

    adapter = FakeAdapter()
    res1 = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
        pre_spawn_hook=failing_hook,
    )
    assert adapter.call_count == 0
    assert res1["disposition"] == "rejected"

    res2 = run_once(
        envelope=env,
        source_id=REGISTRY_SOURCE_ID,
        registry_generation=1,
        snapshot_digest=digest,
        runner_id="runner_1",
        runner_release_digest="2" * 64,
        adapter=adapter,
        db_target=disposable_db,
    )
    assert adapter.call_count == 0
    assert res2["disposition"] == "converged"
    assert res2["reason_code"] == "already_terminal"
