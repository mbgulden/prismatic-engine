from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from prismatic.agy_cli import (
    CANONICAL_AGY_WORKFLOW_VERSION,
    AgyWorkflowError,
    reconcile_terminal_run,
    record_review_decision,
)
from prismatic.harnesses.agy_cli import AGYCLIHarness


def _write(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _write_bound_terminal_run(
    run_dir: Path,
    record: dict[str, object],
    process: dict[str, object],
) -> None:
    run_id = run_dir.name
    bound_record = {
        "task_sha256": "a" * 64,
        "event_id": "task-admission:test",
        "attempt": 1,
        "activity_path": str(run_dir / "activity.json"),
        "session": f"prismatic-agy-{run_id}",
        **record,
    }
    _write(run_dir / "harness-run.json", json.dumps(bound_record))
    _write(
        run_dir / "manifest.json",
        json.dumps(
            {
                "identifier": run_id,
                "task_sha256": bound_record["task_sha256"],
                "process_result_path": str(run_dir / "process-result.json"),
                "activity_path": bound_record["activity_path"],
                "run_record_path": str(run_dir / "harness-run.json"),
                "active_slot_path": bound_record.get("active_slot_path"),
                "admission": {
                    "event_id": bound_record["event_id"],
                    "attempt": bound_record["attempt"],
                },
            }
        ),
    )
    _write(
        run_dir / "process-result.json",
        json.dumps(
            {
                "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
                "identifier": run_id,
                "activity_path": bound_record["activity_path"],
                **process,
            }
        ),
    )


def _review_worker(args: tuple[str, str, str]) -> str:
    run_dir, decision, reviewer = args
    try:
        record_review_decision(
            Path(run_dir),
            decision=decision,
            reviewer=reviewer,
            summary=f"Concurrent {decision} decision.",
        )
        return decision
    except AgyWorkflowError:
        return "blocked"


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
def test_harness_automatically_finalizes_without_status_poll(tmp_path: Path):
    harness, task = _harness_fixture(tmp_path)
    run_id = harness.dispatch({**task, "run_id": "agy-harness-test"})
    assert run_id == "agy-harness-test"
    run_dir = Path(task["runtime"]) / run_id
    record_path = run_dir / "harness-run.json"
    slot_path = Path(task["runtime"]) / "active-slots" / "slot-0.json"
    deadline = time.monotonic() + 5
    record = json.loads(record_path.read_text())
    while record["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
        record = json.loads(record_path.read_text())
    assert record["status"] == "completed"
    assert record["state"] == "review_pending"
    assert record["producer_completed"] is True
    assert record["error"] is None
    assert record["verification_status"] == "pending"
    assert record["completed_at"] is not None
    assert (run_dir / "process-result.json").is_file()
    assert not slot_path.exists()
    receipt = json.loads((run_dir / "launch-receipt.json").read_text())
    session_deadline = time.monotonic() + 2
    session_returncode = 0
    while time.monotonic() < session_deadline:
        session_returncode = subprocess.run(
            [harness._config["tmux"], "has-session", "-t", receipt["session"]],
            capture_output=True,
            check=False,
        ).returncode
        if session_returncode != 0:
            break
        time.sleep(0.05)
    assert session_returncode != 0

    status = harness.status(run_id)
    assert status["status"] == "completed"
    assert status["verification_status"] == "pending"
    assert status["producer_completed"] is True
    assert status["runtime_deadline"] is None
    assert status["activity"]["automatic_kill"] is False
    assert status["activity"]["classification"] == "terminal"
    reviewed = record_review_decision(
        run_dir,
        decision="accepted",
        reviewer="independent-test-reviewer",
        summary="Exact automatic terminalization verified.",
    )
    assert reviewed["state"] == "accepted"
    reviewed_status = harness.status(run_id)
    assert reviewed_status["state"] == "accepted"
    assert reviewed_status["verification_status"] == "reviewed"
    assert reviewed_status["review_status"] == "accepted"
    assert reviewed_status["producer_completed"] is True
    assert any("HARNESS_FAKE_DONE" in line for line in harness.logs(run_id))
    assert harness.cost(run_id)["available"] is False

    restarted = AGYCLIHarness(dict(harness._config))
    assert restarted.status(run_id)["status"] == "completed"
    assert record["event_id"] == "task-admission:harness-test"
    assert Path(record["result_path"]).is_relative_to(Path(task["spool"]))
    replay = restarted.dispatch({**task, "run_id": "agy-replay-ignored"})
    assert replay == run_id
    assert not (Path(task["spool"]) / "agy-replay-ignored").exists()


def test_dashqa2_stale_terminal_record_reconciles_and_releases_slot(tmp_path: Path):
    runtime = tmp_path / "runtime"
    run_id = "agy-admission-ef770cdc24f3ade3f791980b"
    run_dir = runtime / run_id
    slot_dir = runtime / "active-slots"
    spool = tmp_path / "spool"
    run_dir.mkdir(parents=True)
    slot_dir.mkdir()
    spool.mkdir()
    result = _write(spool / "RESULT.md", "PRISMATIC_AGY_RESULT_V1\n")
    plan = _write(spool / "PLAN.md", "PLAN\n")
    slot = _write(
        slot_dir / "slot-0.json",
        json.dumps({"run_id": run_id, "owner_pid": 999_999_999}),
    )
    _write_bound_terminal_run(
        run_dir,
        {
            "run_id": run_id,
            "status": "running",
            "state": "running",
            "result_path": str(result),
            "plan_path": str(plan),
            "active_slot_path": str(slot),
            "verification_status": "pending",
            "producer_identity": "agy-cli-producer",
        },
        {
            "exit_code": 0,
            "result_exists": True,
            "process_tree_cleanup_verified": True,
            "surviving_process_identities": [],
            "result_path": str(result),
            "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
            "finished_at_unix": 1234.5,
        },
    )

    finalized = reconcile_terminal_run(run_dir)
    assert finalized["status"] == "completed"
    assert finalized["state"] == "review_pending"
    assert finalized["producer_completed"] is True
    assert finalized["verification_status"] == "pending"
    assert finalized["completed_at"] == 1234.5
    assert not slot.exists()
    assert reconcile_terminal_run(run_dir) == finalized

    with pytest.raises(AgyWorkflowError, match="producer cannot independently review"):
        record_review_decision(
            run_dir,
            decision="accepted",
            reviewer="agy-cli-producer",
            summary="Self-review must fail closed.",
        )
    reviewed = record_review_decision(
        run_dir,
        decision="repair_required",
        reviewer="george-independent-review",
        summary="Required dashboard route returns 404 without a visible fallback.",
        reviewed_at=1235.0,
    )
    assert reviewed["state"] == "repair_required"
    assert reviewed["verification_status"] == "reviewed"
    assert reviewed["review_status"] == "repair_required"
    assert reviewed["reviewed_by"] == "george-independent-review"
    assert reconcile_terminal_run(run_dir) == reviewed
    assert (
        record_review_decision(
            run_dir,
            decision="repair_required",
            reviewer="george-independent-review",
            summary="Idempotent replay does not replace the durable decision.",
            reviewed_at=9999.0,
        )
        == reviewed
    )
    with pytest.raises(AgyWorkflowError, match="already recorded"):
        record_review_decision(
            run_dir,
            decision="accepted",
            reviewer="other-reviewer",
            summary="Conflicting decision.",
        )


@pytest.mark.parametrize(
    ("cleanup_verified", "result_hash_matches", "expected_state", "slot_released"),
    [
        (True, False, "review_pending", True),
        (False, True, "rejected", False),
    ],
)
def test_slot_release_requires_cleanup_not_producer_success(
    tmp_path: Path,
    cleanup_verified: bool,
    result_hash_matches: bool,
    expected_state: str,
    slot_released: bool,
):
    runtime = tmp_path / "runtime"
    run_id = "agy-terminal-safety"
    run_dir = runtime / run_id
    slots = runtime / "active-slots"
    spool = tmp_path / "spool"
    run_dir.mkdir(parents=True)
    slots.mkdir()
    spool.mkdir()
    result = _write(spool / "RESULT.md", "PRISMATIC_AGY_RESULT_V1\n")
    plan = _write(spool / "PLAN.md", "PLAN\n")
    slot = _write(slots / "slot-0.json", json.dumps({"run_id": run_id}))
    result_sha = hashlib.sha256(result.read_bytes()).hexdigest()
    _write_bound_terminal_run(
        run_dir,
        {
            "run_id": run_id,
            "state": "running",
            "status": "running",
            "result_path": str(result),
            "plan_path": str(plan),
            "active_slot_path": str(slot),
        },
        {
            "exit_code": 0,
            "result_exists": True,
            "result_path": str(result),
            "result_sha256": result_sha if result_hash_matches else "0" * 64,
            "process_tree_cleanup_verified": cleanup_verified,
            "surviving_process_identities": []
            if cleanup_verified
            else [{"pid": 999_999_999, "start_ticks": "1"}],
            "finished_at_unix": 1234.5,
        },
    )

    finalized = reconcile_terminal_run(run_dir)
    assert finalized["status"] == "failed"
    assert finalized["state"] == expected_state
    assert finalized["producer_completed"] is False
    assert slot.exists() is not slot_released


def test_terminal_receipts_fail_closed_when_symlinked_or_cancel_counterfeit(
    tmp_path: Path,
):
    runtime = tmp_path / "runtime"
    run_id = "agy-receipt-integrity"
    run_dir = runtime / run_id
    slots = runtime / "active-slots"
    spool = tmp_path / "spool"
    run_dir.mkdir(parents=True)
    slots.mkdir()
    spool.mkdir()
    result = _write(spool / "RESULT.md", "PRISMATIC_AGY_RESULT_V1\n")
    plan = _write(spool / "PLAN.md", "PLAN\n")
    slot = _write(slots / "slot-0.json", json.dumps({"run_id": run_id}))
    _write_bound_terminal_run(
        run_dir,
        {
            "run_id": run_id,
            "state": "running",
            "status": "running",
            "result_path": str(result),
            "plan_path": str(plan),
            "active_slot_path": str(slot),
        },
        {
            "exit_code": 0,
            "result_exists": True,
            "result_path": str(result),
            "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
            "process_tree_cleanup_verified": True,
            "surviving_process_identities": [],
            "finished_at_unix": 1234.5,
        },
    )
    process_path = run_dir / "process-result.json"
    external = tmp_path / "counterfeit-process.json"
    process_path.replace(external)
    process_path.symlink_to(external)
    with pytest.raises(AgyWorkflowError, match="regular non-symlink file"):
        reconcile_terminal_run(run_dir)
    assert slot.exists()

    process_path.unlink()
    external.replace(process_path)
    _write(run_dir / "cancel-receipt.json", "{}")
    with pytest.raises(AgyWorkflowError, match="cancellation receipt is invalid"):
        reconcile_terminal_run(run_dir)
    assert slot.exists()


def test_concurrent_review_decisions_are_non_overwritable(tmp_path: Path):
    run_dir = tmp_path / "agy-review-race"
    run_dir.mkdir()
    _write(
        run_dir / "harness-run.json",
        json.dumps(
            {
                "run_id": run_dir.name,
                "state": "review_pending",
                "status": "completed",
                "producer_identity": "agy-cli-producer",
            }
        ),
    )
    context = multiprocessing.get_context("spawn")
    with context.Pool(2) as pool:
        results = pool.map(
            _review_worker,
            [
                (str(run_dir), "accepted", "reviewer-a"),
                (str(run_dir), "rejected", "reviewer-b"),
            ],
        )
    assert sorted(results) in (["accepted", "blocked"], ["blocked", "rejected"])
    record = json.loads((run_dir / "harness-run.json").read_text())
    assert record["review_status"] in {"accepted", "rejected"}
    assert record["state"] == record["review_status"]


def test_live_identity_blocks_counterfeit_terminal_slot_reclaim(tmp_path: Path):
    harness, _task = _harness_fixture(tmp_path)
    slots = Path(harness._config["runtime_dir"]) / "active-slots"
    slots.mkdir()
    run_id = "live-counterfeit-run"
    run_dir = Path(harness._config["runtime_dir"]) / run_id
    run_dir.mkdir()
    raw = Path(f"/proc/{os.getpid()}/stat").read_text()
    start_ticks = raw[raw.rfind(")") + 2 :].split()[19]
    _write(
        run_dir / "harness-run.json",
        json.dumps(
            {
                "run_id": run_id,
                "pane_pid": os.getpid(),
                "pane_start_ticks": start_ticks,
            }
        ),
    )
    _write(run_dir / "process-result.json", "{}")
    _write(
        slots / "slot-0.json",
        json.dumps(
            {
                "run_id": run_id,
                "owner_pid": os.getpid(),
                "owner_start_ticks": start_ticks,
            }
        ),
    )
    with pytest.raises(AgyWorkflowError, match="concurrency cap is full"):
        harness._claim_slot("intruder")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_post_spawn_receipt_failure_contains_before_slot_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import prismatic.agy_cli as agy_cli_module

    harness, task = _harness_fixture(tmp_path)

    def fail_pane_lookup(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "tmux display-message")

    monkeypatch.setattr(agy_cli_module.subprocess, "check_output", fail_pane_lookup)
    with pytest.raises(subprocess.CalledProcessError):
        harness.dispatch({**task, "run_id": "agy-post-spawn-failure"})
    run_dir = Path(task["runtime"]) / "agy-post-spawn-failure"
    record = json.loads((run_dir / "harness-run.json").read_text())
    assert record["state"] == "rejected"
    assert record["producer_completed"] is False
    assert "post-spawn launch failure" in record["error"]
    assert Path(record["active_slot_path"]).exists()


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
