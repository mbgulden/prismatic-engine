#!/usr/bin/env python3
"""Verify the Prismatic governance dashboard stays wired to live adapters.

This is a focused regression contract for the protected governance dashboard.
It intentionally fails on mock/static tab regressions, 404 route gaps, or
compat-only placeholders replacing real adapters.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
HTML = REPO / "prismatic" / "gateway" / "templates" / "dashboard.html"
SERVER = REPO / "prismatic" / "gateway" / "server.py"
INGESTION_QUEUE = REPO / "prismatic" / "ingestion_queue.py"

FORBIDDEN_HTML_STRINGS = [
    "mockSkills",
    "mockSignals",
    "const mock",
    "Populate mock",
    "Simulated event",
    "simulated for ticket",
    "Completed UI mockup",
    "Creating rebase branches for GRO-671",
    "Watcher daily backup completed successfully",
    "gateway-compat",
    "dashboard-compat",
    "empty-fallback",
]

REQUIRED_HTML_STRINGS = [
    "/api/skills",
    "toggleSkillInstall",
    "/api/gateway/timeline?limit=80",
    "const API_PREFIX = \"/api/gateway\"",
    "`${API_PREFIX}/webhooks/queue`",
    "/api/gateway/agents/status",
    "Test Webhook Harness",
    "No synthetic fallback rendered",
]

ROUTES = [
    "/dashboard",
    "/api/skills",
    "/api/gateway/timeline?limit=20",
    "/api/gateway/agents/status",
    "/api/webhooks/stats",
    "/api/gateway/webhooks/stats",
    "/api/webhooks/queue",
    "/api/gateway/webhooks/queue",
    "/api/gateway/merge/status",
    "/api/foundation/peer_review",
    "/api/dispatcher/status",
    "/api/gateway/dispatcher/status",
    "/api/recovery/status",
    "/locks",
    "/locks/stale",
    "/native-crons?include_deleted=false",
    "/api/pwp/status",
    "/api/plugins/governance",
    "/api/quota",
    "/workspace-tree",
    "/workspace-tree/index.js",
    "/api/plugins/hermes-plugin-workspace-tree-navigator/health",
    "/api/plugins/hermes-plugin-workspace-tree-navigator/tree",
]


def fail(name: str, detail: Any, failures: list[dict[str, Any]]) -> None:
    failures.append({"check": name, "detail": detail})


def json_or_text(response) -> Any:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return response.json()
    return response.text


def main() -> int:
    failures: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    for path in [HTML, SERVER, INGESTION_QUEUE]:
        ok = path.exists()
        checks[f"exists:{path.relative_to(REPO)}"] = ok
        if not ok:
            fail("changed_path_exists", str(path), failures)

    py = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SERVER), str(INGESTION_QUEUE)],
        cwd=REPO,
        text=True,
        capture_output=True,
    )
    checks["py_compile:server"] = py.returncode == 0
    if py.returncode != 0:
        fail("py_compile:server", py.stderr[-1000:], failures)

    html = HTML.read_text(encoding="utf-8")
    for needle in FORBIDDEN_HTML_STRINGS:
        ok = needle not in html
        checks[f"forbidden_absent:{needle}"] = ok
        if not ok:
            fail("forbidden_html_string", needle, failures)
    for needle in REQUIRED_HTML_STRINGS:
        ok = needle in html
        checks[f"required_present:{needle}"] = ok
        if not ok:
            fail("required_html_string", needle, failures)

    server_text = SERVER.read_text(encoding="utf-8")
    queue_noop_needles = [
        "Retry request recorded by gateway compatibility layer",
        "Purge request accepted by gateway compatibility layer",
    ]
    for needle in queue_noop_needles:
        ok = needle not in server_text
        checks[f"queue_noop_removed:{needle[:24]}"] = ok
        if not ok:
            fail("queue_noop_removed", needle, failures)

    script = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    script_tmp = Path("/tmp/hermes-dashboard-contract-node-check.js")
    script_tmp.write_text(script, encoding="utf-8")
    node = subprocess.run(
        ["node", "--check", str(script_tmp)],
        cwd=REPO,
        text=True,
        capture_output=True,
    )
    script_tmp.unlink(missing_ok=True)
    checks["node_check:inline_dashboard_script"] = node.returncode == 0
    if node.returncode != 0:
        fail("node_check:inline_dashboard_script", node.stderr[-1000:], failures)

    from prismatic.gateway.server import app

    client = TestClient(app)
    responses: dict[str, Any] = {}
    status: dict[str, int] = {}
    for route in ROUTES:
        response = client.get(route)
        status[route] = response.status_code
        responses[route] = json_or_text(response)
    checks["routes_200"] = status
    if any(code != 200 for code in status.values()):
        fail("routes_200", status, failures)

    def body(route: str) -> Any:
        return responses.get(route) if status.get(route) == 200 else {}

    skills = body("/api/skills")
    ok = isinstance(skills, dict) and skills.get("source") == "prismatic.skills" and len(skills.get("skills") or []) > 0
    checks["skills_live_source"] = {"ok": ok, "source": skills.get("source") if isinstance(skills, dict) else None, "count": len(skills.get("skills") or []) if isinstance(skills, dict) else 0}
    if not ok:
        fail("skills_live_source", checks["skills_live_source"], failures)

    timeline = body("/api/gateway/timeline?limit=20")
    ok = isinstance(timeline, dict) and timeline.get("source") == "prismatic.timeline"
    checks["signals_live_timeline"] = {"ok": ok, "source": timeline.get("source") if isinstance(timeline, dict) else None}
    if not ok:
        fail("signals_live_timeline", checks["signals_live_timeline"], failures)

    expectations = {
        "/api/gateway/agents/status": ("source", "run_records+agent_registry+queue_state+timeline+health_context"),
        "/api/webhooks/stats": ("source", "linear_webhook_queue.db"),
        "/api/gateway/webhooks/stats": ("source", "linear_webhook_queue.db"),
        "/api/webhooks/queue": ("source", "linear_webhook_queue.db"),
        "/api/gateway/webhooks/queue": ("source", "linear_webhook_queue.db"),
        "/api/gateway/merge/status": ("source", "merge_state+governance_triage+merge_control_state"),
        "/api/foundation/peer_review": ("source", "run_records+foundation_control_state"),
        "/api/dispatcher/status": ("source", "dashboard_dispatcher_state+run_records"),
        "/api/gateway/dispatcher/status": ("source", "dashboard_dispatcher_state+run_records"),
        "/api/recovery/status": ("source", "dashboard_recovery_controls+run_records"),
        "/api/quota": ("source", "quota_state.db"),
    }
    for route, (key, expected) in expectations.items():
        payload = body(route)
        ok = isinstance(payload, dict) and payload.get(key) == expected
        checks[f"live_source:{route}"] = {"ok": ok, "actual": payload.get(key) if isinstance(payload, dict) else None}
        if not ok:
            fail(f"live_source:{route}", checks[f"live_source:{route}"], failures)

    pwp = body("/api/pwp/status")
    ok = isinstance(pwp, dict) and pwp.get("connected") is True
    checks["pwp_connected"] = {"ok": ok, "state": pwp.get("state") if isinstance(pwp, dict) else None}
    if not ok:
        fail("pwp_connected", checks["pwp_connected"], failures)

    crons = body("/native-crons?include_deleted=false")
    ok = isinstance(crons, list) and len(crons) > 0
    checks["native_crons_populated"] = {"ok": ok, "count": len(crons) if isinstance(crons, list) else 0}
    if not ok:
        fail("native_crons_populated", checks["native_crons_populated"], failures)

    plugins = body("/api/plugins/governance")
    ok = isinstance(plugins, dict) and len(plugins.get("plugins") or []) > 0
    checks["plugins_populated"] = {"ok": ok, "count": len(plugins.get("plugins") or []) if isinstance(plugins, dict) else 0}
    if not ok:
        fail("plugins_populated", checks["plugins_populated"], failures)

    workspace_tree = body("/api/plugins/hermes-plugin-workspace-tree-navigator/tree")
    ok = isinstance(workspace_tree, dict) and workspace_tree.get("ok") is True and workspace_tree.get("workspace_count", 0) > 0
    checks["workspace_tree_populated"] = {"ok": ok, "count": workspace_tree.get("workspace_count") if isinstance(workspace_tree, dict) else None}
    if not ok:
        fail("workspace_tree_populated", checks["workspace_tree_populated"], failures)

    result = {
        "AD_HOC_VERIFICATION": "PASS" if not failures else "FAIL",
        "scope": "Governance dashboard regression contract: all tabs use live adapters, no mock/static regressions",
        "checks": checks,
        "failures": failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
