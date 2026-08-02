from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MERGE_TAB = ROOT / "prismatic/gateway/dashboard_src/tabs/merge.html"
DASHBOARD_JS = ROOT / "prismatic/gateway/dashboard_src/scripts/dashboard.js"
GENERATED = ROOT / "prismatic/gateway/templates/dashboard.html"


def test_merge_tab_marks_legacy_pipeline_quarantined_and_non_authoritative():
    source = MERGE_TAB.read_text(encoding="utf-8")

    assert "LEGACY_MERGE_PIPELINE_QUARANTINED" in source
    assert "Legacy Merge Pipeline — Quarantined" in source
    assert "not Review Factory merge authority" in source
    assert "cannot authorize or execute merges" in source
    assert "Historical Pending Sandboxes — Not RF Jobs" in source
    assert "Historical Merge Records — Automation Unverified" in source
    assert "Open canonical Review Factory" in source


def test_merge_tab_removes_false_live_watcher_claims():
    source = MERGE_TAB.read_text(encoding="utf-8")

    for forbidden in (
        "● Active",
        "Watcher Configurations",
        "300s / SIGUSR1",
        "Confidence Threshold",
        "Shadow Verification",
        "Slack & Telegram",
        "auto-merges to staging",
        "Recent Auto-Merges",
    ):
        assert forbidden not in source


def test_legacy_merge_rows_escape_untrusted_payload_values():
    script = DASHBOARD_JS.read_text(encoding="utf-8")

    assert "Snapshot scan: ${escapeHTML(snapshotScan)}" in script
    assert "data.last_scan ? formatDate(data.last_scan)" in script
    assert "Generated: ${escapeHTML(generated)}" not in script
    assert "pending.map(sb =>" in script
    assert "merged.map(m =>" in script
    assert "sb.contention.map(escapeHTML)" in script
    assert "${escapeHTML(sb.ticket)}" in script
    assert "${escapeHTML(sb.confidence)}" in script
    assert "${escapeHTML(sb.modified)}" in script
    assert "${escapeHTML(m.ticket)}" in script
    assert "${escapeHTML(commit.substring(0, 7)" in script
    assert "${sb.ticket}" not in script
    assert "${m.ticket}" not in script
    assert "${sb.contention.join" not in script


def test_generated_dashboard_contains_phase0_truth_marker():
    generated = GENERATED.read_text(encoding="utf-8")

    assert "LEGACY_MERGE_PIPELINE_QUARANTINED" in generated
    assert "Legacy Merge Pipeline — Quarantined" in generated
    assert "● Active" not in generated
