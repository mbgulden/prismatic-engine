#!/usr/bin/env python3
"""One-command public launch smoke test for Prismatic Engine.

This script is intentionally local-only. It does not require credentials,
webhooks, systemd, or hosted infrastructure.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def step(name: str, fn) -> Any:
    print(f"[public-smoke] {name} ...", flush=True)
    try:
        result = fn()
    except Exception as exc:  # pragma: no cover - CLI diagnostic path
        print(f"[public-smoke] FAILED: {name}: {exc}", file=sys.stderr)
        raise
    print(f"[public-smoke] ok: {name}", flush=True)
    return result


def run(cmd: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    tmp = Path(tempfile.mkdtemp(prefix="prismatic-public-smoke-"))
    state = tmp / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PRISMATIC_STATE_DIR", str(state))
    os.environ.setdefault(
        "PRISMATIC_PLUGIN_JOBS_STATE", str(state / "plugin_jobs.json")
    )
    os.environ.setdefault(
        "PRISMATIC_PLUGIN_ARTIFACTS_STATE", str(state / "plugin_artifacts.json")
    )
    control_token = secrets.token_urlsafe(32)
    control_auth_file = state / "control-auth.json"
    control_auth_file.write_text(
        json.dumps(
            {
                "version": 1,
                "credentials": [
                    {
                        "actor": "public-launch-smoke",
                        "token_sha256": hashlib.sha256(
                            control_token.encode("utf-8")
                        ).hexdigest(),
                        "roles": ["operator"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    control_auth_file.chmod(0o600)
    os.environ["PRISMATIC_CONTROL_AUTH_FILE"] = str(control_auth_file)
    control_headers = {"Authorization": f"Bearer {control_token}"}

    def cleanup_control_auth() -> None:
        control_auth_file.unlink(missing_ok=True)
        if os.environ.get("PRISMATIC_CONTROL_AUTH_FILE") == str(control_auth_file):
            os.environ.pop("PRISMATIC_CONTROL_AUTH_FILE", None)

    atexit.register(cleanup_control_auth)

    def import_core() -> None:
        import prismatic  # noqa: F401
        from prismatic.plugin_policy import decision_payload

        policy = decision_payload(decision="allow", reason="public smoke")
        assert policy["decision"] == "allow"

    def cli_help() -> None:
        from prismatic.cli import run as cli_run

        assert cli_run([]) == 0

    def catalog() -> dict[str, Any]:
        out = run([sys.executable, "scripts/plugin_architecture", "catalog"], cwd=repo)
        payload = json.loads(out)
        assert payload["count"] >= 1
        assert payload["ready_count"] >= 1
        return payload

    def plugin_load_gate() -> None:
        from prismatic.quality.plugin_load import verify_shipped_plugins_load

        result = verify_shipped_plugins_load(
            plugins_dir=repo / "plugins", core_version="0.2.0"
        )
        if not result.passed:
            raise RuntimeError(result.reason)

    def gateway_smoke() -> None:
        from fastapi.testclient import TestClient
        from prismatic.gateway import server

        client = TestClient(server.app)
        for path in [
            "/api/plugins/catalog",
            "/api/plugins/governance",
            "/api/plugins/jobs",
            "/api/plugins/artifacts",
            "/api/plugins/audit-events",
        ]:
            response = client.get(path)
            assert response.status_code == 200, (
                path,
                response.status_code,
                response.text,
            )
        policy = client.post(
            "/api/plugins/policy/preview",
            headers=control_headers,
            json={
                "kind": "job_request",
                "plugin_name": "example-plugin",
                "action": "smoke_validate",
            },
        )
        assert policy.status_code == 200
        assert policy.json()["decision"] == "allow"

    def public_docs() -> None:
        required_docs = [
            "README.md",
            ".env.example",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "docs/north-star.md",
            "docs/dashboard-primary-touchpoint.md",
            "docs/okf-evidence-map.md",
            "docs/public-launch.md",
            "docs/public-onboarding.md",
            "docs/plugin-developer-quickstart.md",
            "docs/plugin-developer-guide.md",
            "docs/security.md",
            "docs/contributing.md",
            "docs/public-security-readiness.md",
            "docs/pwp-reference-lifecycle.md",
            "docs/troubleshooting.md",
        ]
        missing = [rel for rel in required_docs if not (repo / rel).exists()]
        if missing:
            raise RuntimeError(f"missing public docs: {missing}")
        launch = (repo / "docs/public-launch.md").read_text(encoding="utf-8")
        north_star = (repo / "docs/north-star.md").read_text(encoding="utf-8")
        dashboard_primary = (repo / "docs/dashboard-primary-touchpoint.md").read_text(
            encoding="utf-8"
        )
        okf_map = (repo / "docs/okf-evidence-map.md").read_text(encoding="utf-8")
        for marker in [
            "PUBLIC_LAUNCH_SMOKE_OK",
            "PWP reference lifecycle",
            "/api/plugins/audit-events",
            "public_security_readiness_audit.py",
        ]:
            if marker not in launch:
                raise RuntimeError(f"public launch guide missing marker: {marker}")
        for marker in [
            "One-sentence North Star",
            "Plugin ecosystem maturity ladder",
            "Media plugin readiness checklist",
            "Business plugin readiness checklist",
            "Golden Flow status",
            "Dashboard as the intended primary touchpoint",
        ]:
            if marker not in north_star:
                raise RuntimeError(f"north star guide missing marker: {marker}")
        for marker in [
            "Dashboard first for normal users",
            "Telegram/headless for notifications",
            "OKF documentation gap closure",
            "Dashboard-first OKF map",
            "Acceptance criteria for dashboard-primary maturity",
        ]:
            if marker not in dashboard_primary:
                raise RuntimeError(f"dashboard-primary guide missing marker: {marker}")
        for marker in [
            "Objective → Key Result → Function → Evidence",
            "Public-launch OKF map",
            "Plugin-governance OKF map",
            "Media plugin OKF map",
            "Business plugin OKF map",
        ]:
            if marker not in okf_map:
                raise RuntimeError(f"OKF evidence map missing marker: {marker}")

    def dashboard_markers() -> None:
        html = (repo / "prismatic/gateway/templates/dashboard.html").read_text(
            encoding="utf-8"
        )
        markers = [
            "plugin-policy-summary",
            "plugin-policy-decision",
            "renderPluginPolicy",
            "Policy Enforcement",
            "plugin-first-run-empty-state",
            "plugin-onboarding-hints",
            "copyDashboardCommand",
            "plugin-detail-drawer",
            "plugin-job-timeline",
            "plugin-artifact-inventory",
            "plugin-audit-events",
            "renderPluginAuditEvents",
            "/api/plugins/audit-events",
            "plugin-approval-controls",
            "plugin-dashboard-health-cards",
            "pwp-lifecycle-history",
            "renderPWPLifecycleHistory",
            "pwpAction('lifecycle-demo')",
        ]
        missing = [marker for marker in markers if marker not in html]
        if missing:
            raise RuntimeError(f"missing dashboard markers: {missing}")

    step("core imports", import_core)
    step("CLI help", cli_help)
    catalog_payload = step("plugin catalog", catalog)
    step("plugin load gate", plugin_load_gate)
    step("Gateway API smoke", gateway_smoke)
    cleanup_control_auth()
    step("public docs", public_docs)
    step("dashboard markers", dashboard_markers)

    print(
        json.dumps(
            {
                "plugins": catalog_payload["count"],
                "ready": catalog_payload["ready_count"],
            },
            sort_keys=True,
        )
    )
    print("PUBLIC_LAUNCH_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
