from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from prismatic.agy_cli import CANONICAL_AGY_WORKFLOW_VERSION, CANONICAL_TRANSPORT
from prismatic.task_admission_agy_launcher import (
    TaskAdmissionAgyLauncherError,
    launch_request,
    load_config,
    parse_request,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"],
        check=True,
    )
    (workspace / ".gitignore").write_text(".prismatic-task/\n")
    (workspace / "README.md").write_text("fixture\n")
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-qm", "fixture"], check=True
    )
    task_file = workspace / ".prismatic-task" / "TST-1.md"
    task_file.parent.mkdir(mode=0o700)
    task_file.write_text("# frozen task\n")

    runtime = tmp_path / "runtime"
    spool = tmp_path / "spool"
    agy_home = tmp_path / "agy-home"
    for directory in (runtime, spool, agy_home):
        directory.mkdir(mode=0o700)
    binary = tmp_path / "agy"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o500)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    task_sha = hashlib.sha256(task_file.read_bytes()).hexdigest()
    event = "task-admission:" + "a" * 64
    request = {
        "event_id": event,
        "idempotency_key": event,
        "claim_id": "b" * 32,
        "attempt": 1,
        "task_id": "TST-1",
        "producer_identity": "agy-gemini-3.6-flash-high",
        "base_commit": _git(workspace, "rev-parse", "HEAD"),
        "base_tree": _git(workspace, "rev-parse", "HEAD^{tree}"),
        "task_file_sha256": task_sha,
        "writer_cap": 1,
        "worktree": str(workspace),
        "task_file": ".prismatic-task/TST-1.md",
        "actor": "operator-test",
    }
    harness = {
        "runtime_dir": str(runtime),
        "spool_dir": str(spool),
        "agy_binary": str(binary),
        "agy_binary_sha256": binary_sha,
        "agy_home": str(agy_home),
        "models": ["gemini-3.6-flash-high"],
        "concurrent_runs": 1,
        "tmux": "/usr/bin/true",
    }
    config = {
        "version": 1,
        "harness": harness,
        "tasks": {
            "TST-1": {
                **{
                    key: request[key]
                    for key in (
                        "producer_identity",
                        "base_commit",
                        "base_tree",
                        "task_file_sha256",
                        "worktree",
                        "task_file",
                    )
                },
                "model": "gemini-3.6-flash-high",
                "sandbox": True,
            }
        },
    }
    return request, config, runtime


class _FakeHarness:
    dispatches: list[dict] = []
    runs_by_token: dict[str, str] = {}

    def __init__(self, config):
        self.runtime_dir = Path(config["runtime_dir"])

    def dispatch(self, task):
        type(self).dispatches.append(task)
        admission = json.loads(Path(task["admission_receipt"]).read_text())
        token = admission["attempt_token"]
        run_id = task["run_id"]
        type(self).runs_by_token.setdefault(token, run_id)
        launch_dir = self.runtime_dir / run_id
        launch_dir.mkdir(mode=0o700)
        receipt = {
            "accepted": True,
            "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
            "transport": CANONICAL_TRANSPORT,
            "identifier": run_id,
            "event_id": admission["event_id"],
            "attempt": admission["attempt"],
            "attempt_token": token,
            "task_sha256": admission["task_sha256"],
            "agy_binary_sha256": admission["agy_binary_sha256"],
            "runtime_deadline": None,
        }
        receipt_path = launch_dir / "launch-receipt.json"
        receipt_path.write_text(json.dumps(receipt))
        receipt_path.chmod(0o600)
        return run_id


def test_request_requires_claim_attempt_cap_and_no_duplicate_keys(tmp_path):
    request, _config, _runtime = _fixture(tmp_path)
    assert parse_request(json.dumps(request).encode())["attempt"] == 1

    duplicate = json.dumps(request)[:-1] + ',"attempt":1}'
    with pytest.raises(TaskAdmissionAgyLauncherError, match="duplicate_json_key"):
        parse_request(duplicate.encode())

    request["writer_cap"] = 2
    with pytest.raises(TaskAdmissionAgyLauncherError, match="writer_cap_invalid"):
        parse_request(json.dumps(request).encode())


def test_private_config_requires_cap_one_and_rejects_symlink(tmp_path):
    _request, config, _runtime = _fixture(tmp_path)
    path = tmp_path / "launcher.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    assert load_config(path)["harness"]["concurrent_runs"] == 1

    config["harness"]["concurrent_runs"] = 2
    path.write_text(json.dumps(config))
    with pytest.raises(TaskAdmissionAgyLauncherError, match="config_shape_invalid"):
        load_config(path)

    link = tmp_path / "launcher-link.json"
    link.symlink_to(path)
    with pytest.raises(TaskAdmissionAgyLauncherError, match="config_"):
        load_config(link)


def test_adapter_delegates_replay_and_cap_durability_to_canonical_harness(tmp_path):
    request, config, runtime = _fixture(tmp_path)
    _FakeHarness.dispatches.clear()
    _FakeHarness.runs_by_token.clear()

    first = launch_request(request, config, harness_factory=_FakeHarness)
    recovered_request = dict(request, claim_id="c" * 32, attempt=2)
    second = launch_request(recovered_request, config, harness_factory=_FakeHarness)

    assert (
        first
        == second
        == {
            "accepted": True,
            "idempotency_key": request["event_id"],
            "launch_id": "agy-admission-" + "a" * 24,
        }
    )
    assert len(_FakeHarness.dispatches) == 1
    assert len(_FakeHarness.runs_by_token) == 1
    task = _FakeHarness.dispatches[0]
    assert task["workspace"] == request["worktree"]
    assert task["task_ref"] == request["task_id"]
    assert task["run_id"] == first["launch_id"]

    receipts = list((runtime / "admission-receipts").glob("*.json"))
    assert len(receipts) == 1
    admission = json.loads(receipts[0].read_text())
    assert admission["event_id"] == request["event_id"]
    assert admission["claim_id"] == request["claim_id"]
    assert admission["attempt"] == request["attempt"]
    assert admission["task_sha256"] == request["task_file_sha256"]


def test_incomplete_event_run_fails_closed_without_second_dispatch(tmp_path):
    request, config, runtime = _fixture(tmp_path)
    _FakeHarness.dispatches.clear()
    run_dir = runtime / ("agy-admission-" + "a" * 24)
    run_dir.mkdir(mode=0o700)

    with pytest.raises(
        TaskAdmissionAgyLauncherError, match="launch_receipt_unavailable"
    ):
        launch_request(request, config, harness_factory=_FakeHarness)

    assert _FakeHarness.dispatches == []


def test_task_binding_conflict_fails_before_harness_or_receipt(tmp_path):
    request, config, runtime = _fixture(tmp_path)
    _FakeHarness.dispatches.clear()
    request["base_commit"] = "c" * 40

    with pytest.raises(TaskAdmissionAgyLauncherError, match="task_binding_mismatch"):
        launch_request(request, config, harness_factory=_FakeHarness)

    assert _FakeHarness.dispatches == []
    assert list(runtime.iterdir()) == []


def test_source_uses_harness_without_detached_process_or_wall_clock_policy():
    source = Path("prismatic/task_admission_agy_launcher.py").read_text()
    assert "AGYCLIHarness" in source
    assert "subprocess.Popen" not in source
    assert "print-timeout" not in source
    assert "sqlite3" not in source
    assert "_read_policy_bytes" in source


def test_executable_wrapper_failure_is_sanitized_and_has_no_stdout(tmp_path):
    request, _config, _runtime = _fixture(tmp_path)
    completed = subprocess.run(
        [sys.executable, "scripts/task_admission_agy_launcher.py"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "launcher_error:TaskAdmissionAgyLauncherError\n"
    assert request["event_id"] not in completed.stderr
    assert request["claim_id"] not in completed.stderr
