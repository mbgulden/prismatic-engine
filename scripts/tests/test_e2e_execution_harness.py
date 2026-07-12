from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "scripts" / "e2e_execution_harness.py"


def run_harness(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_e2e_harness_success_is_repeatable(tmp_path: Path) -> None:
    first = run_harness("--workspace", str(tmp_path / "run-one"), "--json")
    second = run_harness("--workspace", str(tmp_path / "run-two"), "--json")

    assert first.returncode == 0, first.stderr + first.stdout
    assert second.returncode == 0, second.stderr + second.stdout

    first_report = json.loads(first.stdout)
    second_report = json.loads(second.stdout)
    for report in (first_report, second_report):
        assert report["ok"] is True
        assert report["issue_id"] == "GRO-3493-E2E"
        assert report["linear_state"] == "In Review"
        assert report["linear_comments"] == 1
        assert report["artifact_sha256"]
        assert Path(report["artifact_path"]).exists()
        assert [stage["stage"] for stage in report["stages"]] == [
            "issue",
            "dispatch",
            "execution",
            "artifact",
            "linear-update",
        ]


def test_e2e_harness_isolates_broken_artifact_link(tmp_path: Path) -> None:
    proc = run_harness(
        "--workspace",
        str(tmp_path / "broken-artifact"),
        "--break-stage",
        "artifact",
        "--json",
    )

    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["failed_stage"] == "artifact"
    assert "expected artifact missing" in report["failure"]
    assert report["linear_state"] == "Todo"


def test_e2e_harness_isolates_broken_linear_update(tmp_path: Path) -> None:
    proc = run_harness(
        "--workspace",
        str(tmp_path / "broken-linear"),
        "--break-stage",
        "linear-update",
        "--json",
    )

    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["failed_stage"] == "linear-update"
    assert report["linear_state"] == "Todo"
    assert report["artifact_sha256"]
    assert Path(report["artifact_path"]).exists()
