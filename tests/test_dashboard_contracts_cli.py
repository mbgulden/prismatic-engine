import json
import subprocess
import sys
from pathlib import Path


def _run_cli(*args: str):
    return subprocess.run(
        [sys.executable, "-m", "prismatic.dashboard_contracts", *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=120,
    )


def test_dashboard_contracts_cli_source_json_passes() -> None:
    proc = _run_cli("--json", "--section", "agents,queue")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["AD_HOC_VERIFICATION"] == "PASS"
    assert payload["source_contract"]["ok"] is True
    assert payload["public_smoke"] == "skipped"


def test_dashboard_contracts_cli_local_smoke_isolated_state() -> None:
    proc = _run_cli("--json", "--section", "agents,queue", "--isolated-state", "--start-local-gateway")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["AD_HOC_VERIFICATION"] == "PASS"
    local = payload["local_live_smoke"]
    assert local["ok"] is True
    assert local["gateway"] == "killed"
    assert local["temp_state"] == "removed"
    assert not local["failures"]
    assert local["endpoint_results"]["/api/gateway/webhooks/stats"] == "passed"
