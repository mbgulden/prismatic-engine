from __future__ import annotations

import json
import subprocess
from pathlib import Path

from prismatic.harnesses.base import HarnessStatus
from prismatic.harnesses.hermes.adapter import HermesHarness


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if args[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="ActiveState=active\nSubState=running\nResult=success\nMainPID=123\nExecMainStatus=0\n",
                stderr="",
            )
        if args and args[0] == "journalctl":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="2026-07-06T00:00:00Z hermes-fred started\n2026-07-06T00:00:01Z task accepted\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def test_dispatch_writes_spool_file_and_starts_systemd_service(tmp_path: Path):
    runner = FakeRunner()
    harness = HermesHarness(
        {
            "target": "fred",
            "spool_dir": str(tmp_path),
            "service_template": "hermes-{target}.service",
        },
        runner=runner,
    )

    run_id = harness.dispatch(
        {"issue_id": "GRO-3361", "title": "Implement Hermes adapter"}
    )

    assert run_id.startswith("hermes-fred-")
    assert runner.calls[0] == ["systemctl", "start", "hermes-fred.service"]
    task_file = tmp_path / f"{run_id}.json"
    payload = json.loads(task_file.read_text())
    assert payload["run_id"] == run_id
    assert payload["service"] == "hermes-fred.service"
    assert payload["task"]["issue_id"] == "GRO-3361"


def test_status_reads_systemctl_show_and_normalizes_running(tmp_path: Path):
    runner = FakeRunner()
    harness = HermesHarness(
        {"target": "kai", "spool_dir": str(tmp_path)}, runner=runner
    )
    run_id = harness.dispatch({"issue_id": "GRO-3361"})

    status = harness.status(run_id)

    assert status["status"] == HarnessStatus.RUNNING.value
    assert status["service"] == "hermes-kai.service"
    assert status["systemd"]["MainPID"] == "123"
    assert [
        "systemctl",
        "show",
        "hermes-kai.service",
        "--no-page",
        "--property=ActiveState,SubState,Result,MainPID,ExecMainStatus",
    ] in runner.calls


def test_logs_read_from_journalctl(tmp_path: Path):
    runner = FakeRunner()
    harness = HermesHarness(
        {"target": "ned", "spool_dir": str(tmp_path)}, runner=runner
    )
    run_id = harness.dispatch({"issue_id": "GRO-3361"})

    logs = harness.logs(run_id, tail=2)

    assert logs == [
        "2026-07-06T00:00:00Z hermes-fred started",
        "2026-07-06T00:00:01Z task accepted",
    ]
    assert [
        "journalctl",
        "-u",
        "hermes-ned.service",
        "-n",
        "2",
        "--no-pager",
        "--output=short-iso",
    ] in runner.calls


def test_cancel_stops_systemd_service(tmp_path: Path):
    runner = FakeRunner()
    harness = HermesHarness(
        {"target": "fred", "spool_dir": str(tmp_path)}, runner=runner
    )
    run_id = harness.dispatch({"issue_id": "GRO-3361"})

    assert harness.cancel(run_id) is True
    assert (
        harness.status(run_id)["status"] == HarnessStatus.RUNNING.value
    )  # live systemd state wins on next read
    assert ["systemctl", "stop", "hermes-fred.service"] in runner.calls


def test_failed_systemctl_start_is_visible_in_status(tmp_path: Path):
    def runner(args, **kwargs):
        if args[:2] == ["systemctl", "start"]:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="unit missing"
            )
        if args[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="unit missing"
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    harness = HermesHarness(
        {"target": "sam", "spool_dir": str(tmp_path)}, runner=runner
    )
    run_id = harness.dispatch({"issue_id": "GRO-3361"})

    status = harness.status(run_id)

    assert status["status"] == HarnessStatus.UNKNOWN.value
    assert "unit missing" in status["error"]
