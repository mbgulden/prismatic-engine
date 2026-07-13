from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = REPO_ROOT / "prismatic/gateway/templates/dashboard.html"


def test_plugin_dashboard_public_ux_markers_present() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    markers = [
        "plugin-first-run-empty-state",
        "plugin-onboarding-hints",
        "plugin-error-explanation",
        "copyDashboardCommand",
        "copy-cli-command",
        "dashboard-doc-link",
        "plugin-detail-drawer",
        "renderPluginDetailDrawer",
        "plugin-job-timeline",
        "renderPluginJobTimeline",
        "plugin-artifact-inventory",
        "renderPluginArtifactInventory",
        "plugin-audit-events",
        "renderPluginAuditEvents",
        "/api/plugins/audit-events",
        "plugin-approval-controls",
        "renderPluginApprovalControls",
        "plugin-dashboard-health-cards",
        "pluginDashboardCounts",
        "pwp-lifecycle-history",
        "renderPWPLifecycleHistory",
        "pwpAction('lifecycle-demo')",
        "docs/public-onboarding.md",
        "docs/release-process.md",
        "docs/prismatic-plugin-architecture.md",
    ]
    missing = [marker for marker in markers if marker not in html]
    assert not missing


def test_plugin_dashboard_mobile_responsive_markers_present() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    for marker in [
        "grid-cols-1",
        "sm:grid-cols-2",
        "2xl:grid-cols-[minmax(0,1fr)_380px]",
        "overflow-x-auto",
        "min-w-0",
    ]:
        assert marker in html


def test_dashboard_visual_qa_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/dashboard_visual_qa.py"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DASHBOARD_VISUAL_QA_OK" in result.stdout
