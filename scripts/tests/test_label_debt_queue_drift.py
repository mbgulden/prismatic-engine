#!/usr/bin/env python3
"""Tests for scripts/ops/label_debt_queue_drift.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "label_debt_queue_drift.py"
spec = importlib.util.spec_from_file_location("label_debt_queue_drift", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def issue(identifier, state_name, state_type, updated_at, labels, title="Test issue"):
    return mod.IssueSummary.from_linear(
        {
            "identifier": identifier,
            "title": title,
            "updatedAt": updated_at,
            "state": {"name": state_name, "type": state_type},
            "labels": {"nodes": [{"name": label} for label in labels]},
        }
    )


def test_summarize_counts_label_debt_and_stale_drift_categories():
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    issues = [
        issue(
            "GRO-1",
            "Backlog",
            "backlog",
            "2026-06-20T00:00:00Z",
            ["agent:ned", "agent:fred", "dispatch:ready"],
        ),
        issue("GRO-2", "Todo", "unstarted", "2026-07-05T00:00:00Z", ["dispatch:ready"]),
        issue("GRO-3", "Done", "completed", "2026-07-01T00:00:00Z", ["agent:ned"]),
        issue(
            "GRO-4", "In Progress", "started", "2026-07-04T00:00:00Z", ["agent:done"]
        ),
        issue("GRO-5", "Backlog", "backlog", "2026-06-01T00:00:00Z", []),
        issue(
            "GRO-6",
            "In Progress",
            "started",
            "2026-07-05T00:00:00Z",
            ["agent:needs-human-review"],
        ),
    ]

    summary = mod.summarize(issues, now=now, stale_days=7)

    assert summary["totals"]["open_issues_scanned"] == 6
    assert summary["totals"]["dispatchable_issues"] == 5
    assert summary["totals"]["stale_dispatchable_issues"] == 2
    assert summary["totals"]["blocked_from_dispatch"] == 1
    assert summary["label_debt_by_reason"]["multiple_agent_labels"] == 1
    assert summary["label_debt_by_reason"]["dispatch_ready_without_agent"] == 1
    assert summary["label_debt_by_reason"]["agent_label_on_terminal_state"] == 1
    assert summary["label_debt_by_reason"]["agent_done_on_non_done_state"] == 1
    assert summary["label_debt_by_reason"]["dispatchable_without_agent_or_ready"] == 2


def test_add_drift_compares_with_previous_snapshot():
    current = {
        "generated_at": "2026-07-06T00:00:00+00:00",
        "totals": {
            "open_issues_scanned": 10,
            "issues_with_label_debt": 4,
            "stale_dispatchable_issues": 3,
        },
    }
    previous = {
        "generated_at": "2026-07-05T00:00:00+00:00",
        "totals": {
            "open_issues_scanned": 8,
            "issues_with_label_debt": 5,
            "stale_dispatchable_issues": 1,
        },
    }

    result = mod.add_drift(current, previous)

    assert result["drift"]["baseline"] == "2026-07-05T00:00:00+00:00"
    assert result["drift"]["deltas"]["open_issues_scanned"] == 2
    assert result["drift"]["deltas"]["issues_with_label_debt"] == -1
    assert result["drift"]["deltas"]["stale_dispatchable_issues"] == 2


def test_cli_fixture_appends_history_and_outputs_json(tmp_path, capsys):
    fixture = tmp_path / "issues.json"
    history = tmp_path / "history.jsonl"
    fixture.write_text(
        json.dumps(
            [
                {
                    "identifier": "GRO-7",
                    "title": "Old queue item",
                    "updatedAt": "2026-06-01T00:00:00Z",
                    "state": {"name": "Backlog", "type": "backlog"},
                    "labels": {"nodes": [{"name": "agent:ned"}]},
                }
            ]
        )
    )

    rc = mod.main(
        [
            "--fixture",
            str(fixture),
            "--history-path",
            str(history),
            "--append-history",
            "--json",
            "--stale-days",
            "7",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert output["totals"]["stale_dispatchable_issues"] == 1
    assert history.exists()
    assert len(history.read_text().splitlines()) == 1
