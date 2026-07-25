from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

import pytest

from prismatic.agy_cli import AgyWorkflowError
from prismatic.harnesses.agy_cli import AGYCLIHarness


def _write(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _harness_fixture(tmp_path: Path) -> tuple[AGYCLIHarness, dict[str, str]]:
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    spool = tmp_path / "spool"
    home = tmp_path / "agy-home"
    tasks = tmp_path / "tasks"
    for path in (workspace, runtime, spool, home, tasks):
        path.mkdir(mode=0o700)
    task_file = _write(tasks / "TASK.md", "Create the required plan and result.\n")
    fake = _write(
        tmp_path / "agy-fake",
        """#!/usr/bin/env python3
import pathlib, re, sys
args = sys.argv[1:]
prompt = args[args.index('--print') + 1]
plan = re.search(r'plan to (.+?)\\. Then execute', prompt).group(1)
result = re.search(r'final result to (.+?) with marker', prompt).group(1)
pathlib.Path(plan).write_text('PLAN FIRST\\n')
pathlib.Path(result).write_text('PRISMATIC_AGY_RESULT_V1\\nPRODUCER ONLY\\n')
print('HARNESS_FAKE_DONE')
""",
        0o500,
    )
    digest = hashlib.sha256(fake.read_bytes()).hexdigest()
    admission = _write(
        tmp_path / "admission.json",
        json.dumps(
            {
                "marker": "PRISMATIC_AGY_ADMISSION_V1",
                "authorized": True,
                "event_id": "task-admission:harness-test",
                "attempt": 1,
                "attempt_token": "b" * 64,
                "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
                "agy_binary_sha256": digest,
            }
        ),
    )
    harness = AGYCLIHarness(
        {
            "agy_binary": str(fake),
            "agy_binary_sha256": digest,
            "agy_home": str(home),
            "runtime_dir": str(runtime),
            "spool_dir": str(spool),
            "tmux": shutil.which("tmux") or "/usr/bin/tmux",
            "models": ["gemini-3.6-flash-high"],
        }
    )
    return harness, {
        "workspace": str(workspace),
        "task_file": str(task_file),
        "admission_receipt": str(admission),
        "runtime": str(runtime),
        "spool": str(spool),
    }


def test_harness_contract_and_health(tmp_path: Path):
    harness, _task = _harness_fixture(tmp_path)
    assert harness.name == "agy-cli"
    assert harness.models == ["gemini-3.6-flash-high"]
    assert harness.health() == {"status": "ok", "harness": "agy-cli"}
    capabilities = harness.capabilities()
    assert capabilities.concurrent_runs == 1
    assert capabilities.extra["transport"] == "tmux-durable-anchor"
    assert capabilities.supports_timeout is False
    assert (
        capabilities.extra["runtime_policy"] == "no-wall-clock-cap-progress-supervised"
    )
    assert capabilities.extra["activity_receipts"] is True
    assert (
        "independent verification pending" in capabilities.extra["completion_authority"]
    )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_harness_dispatch_status_logs_and_durable_restart(tmp_path: Path):
    harness, task = _harness_fixture(tmp_path)
    run_id = harness.dispatch({**task, "run_id": "agy-harness-test"})
    assert run_id == "agy-harness-test"
    deadline = time.monotonic() + 5
    status = harness.status(run_id)
    while status["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
        status = harness.status(run_id)
    assert status["status"] == "completed"
    assert status["error"] is None
    assert status["verification_status"] == "pending"
    assert status["producer_completed"] is True
    assert status["runtime_deadline"] is None
    assert status["activity"]["automatic_kill"] is False
    assert status["activity"]["classification"] == "terminal"
    assert status["completed_at"] is not None
    assert any("HARNESS_FAKE_DONE" in line for line in harness.logs(run_id))
    assert harness.cost(run_id)["available"] is False

    restarted = AGYCLIHarness(dict(harness._config))
    assert restarted.status(run_id)["status"] == "completed"
    record = json.loads(
        (Path(task["runtime"]) / run_id / "harness-run.json").read_text()
    )
    assert record["verification_status"] == "pending"
    assert record["event_id"] == "task-admission:harness-test"
    assert Path(record["result_path"]).is_relative_to(Path(task["spool"]))
    replay = restarted.dispatch({**task, "run_id": "agy-replay-ignored"})
    assert replay == run_id
    assert not (Path(task["spool"]) / "agy-replay-ignored").exists()


def test_stale_slot_is_reclaimed_but_live_slot_blocks(tmp_path: Path):
    harness, _task = _harness_fixture(tmp_path)
    slot_dir = Path(harness._config["runtime_dir"]) / "active-slots"
    slot_dir.mkdir()
    slot = slot_dir / "slot-0.json"
    _write(
        slot,
        json.dumps(
            {
                "run_id": "dead-run",
                "claimed_at": 1,
                "owner_pid": 999_999_999,
                "owner_start_ticks": "1",
            }
        ),
    )
    claimed = harness._claim_slot("replacement-run")
    assert json.loads(claimed.read_text())["run_id"] == "replacement-run"
    with pytest.raises(AgyWorkflowError, match="concurrency cap is full"):
        harness._claim_slot("blocked-run")
    harness._release_slot(claimed, "replacement-run")


def test_consumed_admission_without_launch_requires_new_attempt(tmp_path: Path):
    harness, task = _harness_fixture(tmp_path)
    harness._config["tmux"] = str(tmp_path / "missing-tmux")
    requested = {**task, "run_id": "failed-before-launch"}
    with pytest.raises(AgyWorkflowError, match="tmux is unavailable"):
        harness.dispatch(requested)
    with pytest.raises(AgyWorkflowError, match="consumed without a durable launch"):
        harness.dispatch(requested)


def test_harness_rejects_unadmitted_or_hash_drifted_task(tmp_path: Path):
    harness, task = _harness_fixture(tmp_path)
    bad_admission = Path(task["admission_receipt"])
    payload = json.loads(bad_admission.read_text())
    payload["authorized"] = False
    bad_admission.write_text(json.dumps(payload))
    bad_admission.chmod(0o600)
    with pytest.raises(Exception, match="admission receipt mismatch"):
        harness.dispatch({**task, "run_id": "agy-rejected"})


def test_registry_module_is_real_and_importable():
    import prismatic.harnesses.agy_cli as module

    assert module.AGYCLIHarness().name == "agy-cli"
