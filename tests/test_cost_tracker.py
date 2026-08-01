# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.cost import tracker


def test_cost_tracker_records_and_summarizes(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "cost.db"
    monkeypatch.setattr(tracker, "COST_DB", db)

    cost = tracker.record_cost(
        run_id="run-001",
        issue_id="GRO-3366",
        agent="agy",
        model="gemini-2.5-pro",
        tokens_in=100_000,
        tokens_out=50_000,
    )
    assert cost == 0.375

    fallback = tracker.record_cost(
        run_id="run-002",
        issue_id="GRO-3366",
        agent="fred",
        model="unknown-model",
        tokens_in=100_000,
        tokens_out=50_000,
    )
    assert fallback == 0.35

    assert db.exists()
    assert tracker.daily_spend() == 0.725
    assert tracker.agent_spend("agy", days=1) == 0.375
    assert tracker.model_spend(days=1)["gemini-2.5-pro"] == 0.375
    assert len(tracker.last_7_days()) == 7


def test_cost_summary_trend_uses_dates(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "cost.db"
    monkeypatch.setattr(tracker, "COST_DB", db)
    tracker.record_cost(
        "run-003", "GRO-3366", "jules", "gemini-3.5-flash-high", 1_000_000, 0
    )

    summary = tracker.cost_summary()

    assert summary["today"] == 0.075
    assert summary["per_agent"]["jules"] == 0.075
    assert summary["per_model"]["gemini-3.5-flash-high"] == 0.075
    assert len(summary["trend_7d"]) == 7
    assert {"date", "spend"} <= set(summary["trend_7d"][-1])


def test_api_cost_endpoint_returns_summary(tmp_path: Path, monkeypatch) -> None:
    import sys
    import types

    db = tmp_path / "cost.db"
    monkeypatch.setattr(tracker, "COST_DB", db)
    tracker.record_cost(
        "run-api", "GRO-3366", "codex", "gemini-2.5-pro", 10_000, 10_000
    )

    # Existing gateway module imports an optional escalations module that is not
    # present in this branch; stub it so this focused test can exercise /api/cost.
    fake_escalations = types.ModuleType("prismatic.escalations")
    fake_escalations.EscalationStore = object
    fake_escalations.get_telegram_deeplink = lambda escalation_id: f"telegram://{escalation_id}"
    monkeypatch.setitem(sys.modules, "prismatic.escalations", fake_escalations)

    from prismatic.gateway.server import app

    client = TestClient(app)
    response = client.get("/api/cost")

    assert response.status_code == 200
    data = response.json()
    assert data["today"] == 0.0625
    assert data["per_agent"]["codex"] == 0.0625
    assert data["per_model"]["gemini-2.5-pro"] == 0.0625
    assert len(data["trend_7d"]) == 7
