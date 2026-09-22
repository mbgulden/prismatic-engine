"""Tests for the shadow failure-triage driver (scripts/failure_triage_shadow.py).

The driver must never crash the workflow: an unreadable jobs API (e.g. the
token lacking ``actions:read``) degrades to names-only triage and the
workflow still emits its one shadow audit signal. gh's stderr must reach
the logs so the next failure is diagnosable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DRIVER = REPO_ROOT / "scripts" / "failure_triage_shadow.py"

FAILING_GH = """#!/bin/sh
echo "gh: HTTP 403: Resource not accessible by integration (actions:read missing)" >&2
exit 1
"""


def _run_driver(tmp_path, monkeypatch, event):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAILING_GH)
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    audit = tmp_path / "audit.jsonl"
    proc = subprocess.run(
        [sys.executable, str(DRIVER), "--audit-log", str(audit)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    rows = (
        [json.loads(line) for line in audit.read_text().splitlines()]
        if audit.exists()
        else []
    )
    return proc, rows


def test_jobs_api_failure_degrades_to_names_only(tmp_path, monkeypatch):
    # The Sep 22 crash: jobs API 403 -> uncaught CalledProcessError.
    # Now: exit 0, one names-only shadow audit signal, no crash.
    proc, rows = _run_driver(
        tmp_path, monkeypatch, {"inputs": {"run_id": "35736882460"}}
    )
    assert proc.returncode == 0, proc.stderr
    assert len(rows) == 1
    row = rows[0]
    assert row["failure_id"] == "ci:35736882460:jobs-unavailable"
    assert row["test_name"] == "manual"  # workflow_dispatch, run by name only
    assert row["shadow"] is True


def test_gh_stderr_reaches_logs(tmp_path, monkeypatch):
    # The raised error must carry gh's stderr so the next failure is
    # diagnosable from workflow logs, not a bare CalledProcessError.
    proc, _ = _run_driver(tmp_path, monkeypatch, {"inputs": {"run_id": "1"}})
    assert proc.returncode == 0
    assert "403" in proc.stdout
    assert "actions:read" in proc.stdout
