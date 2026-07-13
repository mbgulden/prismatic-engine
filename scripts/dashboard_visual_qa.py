#!/usr/bin/env python3
"""Static dashboard visual QA for public plugin dashboard UX.

This is a lightweight, credential-free check for the HTML dashboard surface. It does
not replace browser screenshots, but it prevents the public-polish markers from
regressing in CI and local release checks.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = REPO_ROOT / "prismatic/gateway/templates/dashboard.html"

REQUIRED_MARKERS = {
    "first-run empty state": ["plugin-first-run-empty-state", "First-run plugin setup"],
    "onboarding hints": ["plugin-onboarding-hints", "Copy CLI", "dashboard-doc-link"],
    "error explanations": ["plugin-error-explanation", "Dashboard refresh error"],
    "copyable commands": ["copyDashboardCommand", "copy-cli-command"],
    "doc links": [
        "docs/public-onboarding.md",
        "docs/release-process.md",
        "docs/prismatic-plugin-architecture.md",
    ],
    "detail drawer": ["plugin-detail-drawer", "renderPluginDetailDrawer"],
    "job timeline": ["plugin-job-timeline", "renderPluginJobTimeline"],
    "artifact inventory": [
        "plugin-artifact-inventory",
        "renderPluginArtifactInventory",
    ],
    "audit events": [
        "plugin-audit-events",
        "renderPluginAuditEvents",
        "/api/plugins/audit-events",
    ],
    "approval controls": [
        "plugin-approval-controls",
        "renderPluginApprovalControls",
        "/approve",
        "/reject",
    ],
    "health cards": ["plugin-dashboard-health-cards", "pluginDashboardCounts"],
    "pwp reference lifecycle": [
        "pwp-lifecycle-history",
        "renderPWPLifecycleHistory",
        "pwpAction('lifecycle-demo')",
        "approval before publish",
    ],
    "responsive layout": [
        "grid-cols-1",
        "sm:grid-cols-2",
        "2xl:grid-cols-[minmax(0,1fr)_380px]",
        "overflow-x-auto",
    ],
}


def main() -> int:
    html = DASHBOARD.read_text(encoding="utf-8")
    failures: list[str] = []
    for label, markers in REQUIRED_MARKERS.items():
        missing = [marker for marker in markers if marker not in html]
        if missing:
            failures.append(f"{label}: missing {missing}")

    if html.count('id="plugin-detail-drawer"') != 1:
        failures.append("plugin detail drawer must have exactly one DOM container")
    if "renderPluginDashboardChrome(null);" not in html:
        failures.append("dashboard chrome renderer is not called on successful refresh")
    if "renderPluginDashboardChrome(err);" not in html:
        failures.append("dashboard chrome renderer is not called on refresh error")

    payload = {
        "ok": not failures,
        "dashboard": str(DASHBOARD.relative_to(REPO_ROOT)),
        "checks": sorted(REQUIRED_MARKERS),
        "failures": failures,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if failures:
        return 1
    print("DASHBOARD_VISUAL_QA_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
