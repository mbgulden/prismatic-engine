from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MERGE_TAB = ROOT / "prismatic/gateway/dashboard_src/tabs/merge.html"
DASHBOARD_JS = ROOT / "prismatic/gateway/dashboard_src/scripts/dashboard.js"
GENERATED = ROOT / "prismatic/gateway/templates/dashboard.html"


def test_merge_tab_marks_legacy_pipeline_quarantined_and_non_authoritative():
    source = MERGE_TAB.read_text(encoding="utf-8")

    assert "LEGACY_MERGE_PIPELINE_QUARANTINED" in source
    assert "Legacy Merge Pipeline" in source
    assert "Review Factory" in source
    assert "Historical Pending" in source
    assert "Historical Merged" in source
    assert "Open Review Factory" in source


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
    assert "Legacy Merge Pipeline" in generated
    assert "● Active" not in generated


def test_review_factory_cta_switches_tab_and_supports_hash_navigation():
    merge_tab = MERGE_TAB.read_text(encoding="utf-8")
    script = DASHBOARD_JS.read_text(encoding="utf-8")

    assert "switchTab('review-factory')" in merge_tab or 'href="#review-factory"' in merge_tab
    assert '"review-factory"' in script.split("const dashboardTabIds", 1)[1]
    assert "dashboardTabFromURL" in script or "dashboardTabFromHash" in script
    assert 'popstate' in script or 'hashchange' in script
    assert "switchTab(" in script


def test_historical_cards_clear_and_flag_stale_on_failure_path():
    script = DASHBOARD_JS.read_text(encoding="utf-8")

    # Success path must clear any prior stale marker on the four Historical cards.
    assert 'node.removeAttribute("data-stale")' in script
    assert 'node.removeAttribute("title")' in script
    for stat_id in (
        "stat-merge-pending",
        "stat-merge-merged",
        "stat-merge-scan",
        "stat-merge-apply",
    ):
        assert f'getElementById("{stat_id}")' in script

    # Failure path must reset the four cards to an explicit unavailable state.
    failure_block_start = script.index("} catch (e) {")
    failure_block_end = script.index("}\n        }\n\n        function escapeHTML", failure_block_start)
    failure_block = script[failure_block_start:failure_block_end]
    for stat_id in (
        "stat-merge-pending",
        "stat-merge-merged",
        "stat-merge-scan",
        "stat-merge-apply",
    ):
        assert f'"{stat_id}"' in failure_block, stat_id
    assert 'node.setAttribute("data-stale", "true")' in failure_block
    assert 'node.textContent = "—"' in failure_block
    # The cards must clear their prior numeric or timestamp text — not just toggle an attribute.
    assert failure_block.count("—") >= 4
    # The change must live in the same function that fetches the merge snapshot.
    assert 'async function fetchMergeData' in script
