from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from pathlib import Path

import pytest

from prismatic.agy_cli import (
    AgyLaunchSpec,
    CANONICAL_ADMISSION_MARKER,
    launch_tmux,
    wait_tmux,
)
from prismatic.harnesses.agy_cli import AGYCLIHarness


def _write_detaching_binary(path: Path, *, parent_lingers: bool) -> str:
    body = f"""#!/usr/bin/env python3
import os, pathlib, signal, sys, time
args = sys.argv[1:]
add_dirs = [pathlib.Path(args[i + 1]) for i, arg in enumerate(args) if arg == '--add-dir']
workspace, artifacts = add_dirs[0], add_dirs[-1]
pid = os.fork()
if pid == 0:
    os.setsid()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pidfile = artifacts / 'detached.pid'
    pidfile.write_text(str(os.getpid()))
    while True:
        time.sleep(1)
if {parent_lingers!r}:
    while True:
        time.sleep(1)
(workspace / 'PLAN.md').write_text('containment plan\\n')
(artifacts / 'RESULT.md').write_text('PRISMATIC_AGY_RESULT_V1\\ncontained\\n')
"""
    path.write_text(body)
    path.chmod(0o700)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity_active(pid: int, start_ticks: str) -> bool:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    fields = raw[raw.rfind(")") + 2 :].split()
    return fields[19] == start_ticks and fields[0] != "Z"


def _identity(pidfile: Path) -> tuple[int, str]:
    for _ in range(100):
        if pidfile.is_file():
            pid = int(pidfile.read_text())
            raw = Path(f"/proc/{pid}/stat").read_text()
            return pid, raw[raw.rfind(")") + 2 :].split()[19]
        time.sleep(0.05)
    pytest.fail("detached descendant did not publish its identity")


def _fixture(
    tmp_path: Path, *, parent_lingers: bool
) -> tuple[AgyLaunchSpec, Path, Path]:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    runtime = tmp_path / "runtime"
    agy_home = tmp_path / "agy-home"
    for directory in (workspace, artifacts, runtime, agy_home):
        directory.mkdir()
    task = tmp_path / "TASK.md"
    task.write_text("contain the exact process tree\n")
    task.chmod(0o600)
    binary = tmp_path / "fake-agy"
    digest = _write_detaching_binary(binary, parent_lingers=parent_lingers)
    spec = AgyLaunchSpec(
        identifier=f"containment-{'cancel' if parent_lingers else 'complete'}-{tmp_path.name}",
        task_file=str(task),
        workspace=str(workspace),
        artifact_root=str(artifacts),
        result_path=str(artifacts / "RESULT.md"),
        plan_path=str(workspace / "PLAN.md"),
        stdout_path=str(artifacts / "stdout.log"),
        stderr_path=str(artifacts / "stderr.log"),
        diagnostics_path=str(artifacts / "diagnostics.log"),
        agy_binary=str(binary),
        agy_binary_sha256=digest,
        agy_home=str(agy_home),
    )
    admission = tmp_path / "admission.json"
    admission.write_text(
        json.dumps(
            {
                "marker": CANONICAL_ADMISSION_MARKER,
                "authorized": True,
                "task_sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                "agy_binary_sha256": digest,
                "event_id": f"test:{spec.identifier}",
                "attempt_token": "c" * 64,
                "attempt": 1,
            }
        )
    )
    admission.chmod(0o600)
    return spec, admission, runtime


def test_normal_completion_contains_detached_descendant(tmp_path: Path):
    spec, admission, runtime = _fixture(tmp_path, parent_lingers=False)
    receipt = launch_tmux(spec, runtime, admission_receipt=admission)
    pid, start_ticks = _identity(Path(spec.artifact_root) / "detached.pid")
    try:
        result = wait_tmux(
            Path(receipt["manifest_path"]).parent / "launch-receipt.json"
        )
        assert result["exit_code"] == 0
        assert result["process_tree_cleanup_verified"] is True
        assert result["surviving_process_identities"] == []
        assert result["observed_process_count"] >= 2
        assert not _identity_active(pid, start_ticks)
    finally:
        if _identity_active(pid, start_ticks):
            os.kill(pid, signal.SIGKILL)


def test_explicit_cancel_contains_detached_descendant_before_slot_release(
    tmp_path: Path,
):
    spec, admission, runtime = _fixture(tmp_path, parent_lingers=True)
    harness = AGYCLIHarness(
        {
            "agy_binary": spec.agy_binary,
            "agy_binary_sha256": spec.agy_binary_sha256,
            "agy_home": spec.agy_home,
            "runtime_dir": str(runtime),
            "spool_dir": spec.artifact_root,
            "concurrent_runs": 1,
        }
    )
    run_id = harness.dispatch(
        {
            "run_id": spec.identifier,
            "workspace": spec.workspace,
            "task_file": spec.task_file,
            "admission_receipt": str(admission),
        }
    )
    pid, start_ticks = _identity(Path(spec.artifact_root) / run_id / "detached.pid")
    try:
        assert harness.cancel(run_id) is True
        receipt = json.loads((runtime / run_id / "cancel-receipt.json").read_text())
        assert receipt["exact_process_tree_cleanup"] is True
        assert receipt["observed_process_count"] >= 2
        assert not _identity_active(pid, start_ticks)
        record = harness._record(run_id)
        assert record["status"] == "cancelled"
        assert record["state"] == "rejected"
        assert record["producer_completed"] is False
        assert not Path(record["active_slot_path"]).exists()
    finally:
        if _identity_active(pid, start_ticks):
            os.kill(pid, signal.SIGKILL)
