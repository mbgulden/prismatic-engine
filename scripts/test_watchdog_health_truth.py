from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_watchdog_uses_live_health_as_source_of_truth_when_service_and_heartbeat_disagree(
    tmp_path: Path,
) -> None:
    """A healthy /health endpoint must not be reported red because local diagnostics disagree."""
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "watchdog.sh", script_dir / "watchdog.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "heartbeat.sh", script_dir / "heartbeat.sh")
    (script_dir / "watchdog.sh").chmod(0o755)
    (script_dir / "heartbeat.sh").chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "curl",
        "#!/usr/bin/env bash\nprintf '200'\n",
    )
    _write_executable(
        fake_bin / "systemctl",
        "#!/usr/bin/env bash\nexit 3\n",
    )

    home = tmp_path / "home"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "PRISMATIC_HOME": str(home),
            "PRISMATIC_PORT": "19000",
        }
    )

    result = subprocess.run(
        ["bash", str(script_dir / "watchdog.sh")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log = (home / ".prismatic" / "run" / "watchdog.log").read_text(encoding="utf-8")
    assert (
        "live gateway health endpoint http://localhost:19000/health → 200 — PASS" in log
    )
    assert (
        "systemd service prismatic-dispatcher.service inactive/unavailable — DIAGNOSTIC ONLY"
        in log
    )
    assert "heartbeat file check failed: MISSING: heartbeat.pid" in log
    assert "DIAGNOSTIC ONLY (live gateway healthy)" in log
    assert "✅ Healthy — no failures recorded" in log
    assert "failure 1/3" not in log


def test_python_gateway_watchdog_reports_live_gateway_source_of_truth(
    monkeypatch,
) -> None:
    import json
    import sys
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("PRISMATIC_TESTING", "1")
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    for module_name in list(sys.modules):
        if module_name == "prismatic" or module_name.startswith("prismatic."):
            sys.modules.pop(module_name, None)

    from prismatic.gateway.watchdog import check_gateway_health

    assert str(REPO_ROOT) in check_gateway_health.__globals__["__file__"]

    now = datetime(2026, 7, 3, 6, 15, tzinfo=timezone.utc)
    started_at = datetime(2026, 7, 3, 4, 0, tzinfo=timezone.utc).timestamp()
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = json.dumps(
        {"status": "ok", "started_at": started_at}
    ).encode("utf-8")
    router = MagicMock()

    with patch("urllib.request.urlopen", return_value=response):
        result = check_gateway_health(
            gateway_url="http://localhost:9000/health", now=now, router=router
        )

    assert result["status"] == "ok"
    assert result["source_of_truth"] == "live_gateway_health_endpoint"
    router.route.assert_not_called()
