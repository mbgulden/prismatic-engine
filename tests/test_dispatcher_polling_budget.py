from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from prismatic import dispatcher
from prismatic.gateway import server
from prismatic.linear_rate_limit import LinearRateLimitState


def future_reset() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()


def reset_dispatcher_budget_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRISMATIC_DISPATCHER_POLLING_BUDGET_STATE", str(tmp_path / "poll_budget.json"))
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(tmp_path / "linear_rate.json"))
    monkeypatch.setenv("PRISMATIC_POLL_MAX_LINEAR_CALLS_PER_CYCLE", "6")
    monkeypatch.setenv("PRISMATIC_POLL_LABEL_SCAN_TTL_SECONDS", "300")
    monkeypatch.setenv("PRISMATIC_POLL_PIPELINE_SCAN_CADENCE", "99")
    monkeypatch.setenv("PRISMATIC_POLL_ROUTE_SCAN_CADENCE", "99")
    monkeypatch.setenv("PRISMATIC_POLL_AGENT_SCAN_CADENCE", "1")
    monkeypatch.setenv("PRISMATIC_POLL_RECOVERY_SCAN_CADENCE", "99")
    monkeypatch.setenv("PRISMATIC_POLL_ORIGIN_SCAN_CADENCE", "99")
    dispatcher._POLL_CYCLE_NUMBER = 0
    dispatcher._CURRENT_POLL_BUDGET = None
    dispatcher.TEAM_ID = "team-test"
    dispatcher._LABEL_SCAN_CACHE.clear()
    dispatcher._LAST_POLL_BUDGET_STATUS.clear()


def fake_team_issues() -> dict:
    return {"team": {"issues": {"nodes": []}}}


def budgeted_fake_gql(_query, _variables=None, *, source="test.fake"):
    if dispatcher._CURRENT_POLL_BUDGET is not None:
        dispatcher._CURRENT_POLL_BUDGET.consume(source)
    return fake_team_issues()


def test_idle_fallback_cycle_stays_under_configured_budget(monkeypatch, tmp_path: Path):
    reset_dispatcher_budget_state(monkeypatch, tmp_path)
    monkeypatch.setattr(dispatcher, "gql", budgeted_fake_gql)
    monkeypatch.setattr(dispatcher, "dispatch_local_tasks", MagicMock(return_value=0))
    monkeypatch.setattr(dispatcher, "load_pipeline_templates", MagicMock(return_value={"pipelines": {}}))
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", MagicMock(return_value=0))
    monkeypatch.setattr(dispatcher, "AGENT_CONFIG", {name: {} for name in ["fred", "kai", "agy", "jules", "codex"]})

    counts = dispatcher.dispatch_once(MagicMock(), pipelines={"pipelines": {}})
    status = dispatcher.get_dispatcher_polling_budget_snapshot()

    assert counts["linear_calls_used"] == 5
    assert status["marker"] == dispatcher.DISPATCHER_POLLING_BUDGET_MARKER
    assert status["max_calls_per_cycle"] == 6
    assert status["last_cycle_calls"] == 5
    assert status["cache_misses"] == 5
    assert "pipeline_scan" in status["skipped_sections"]
    assert "route_scan" in status["skipped_sections"]
    assert "recovery_scan" in status["skipped_sections"]
    assert "origin_scan" in status["skipped_sections"]


def test_budget_exhaustion_skips_remaining_broad_scans_without_crashing(monkeypatch, tmp_path: Path):
    reset_dispatcher_budget_state(monkeypatch, tmp_path)
    monkeypatch.setenv("PRISMATIC_POLL_MAX_LINEAR_CALLS_PER_CYCLE", "2")
    monkeypatch.setattr(dispatcher, "gql", budgeted_fake_gql)
    monkeypatch.setattr(dispatcher, "dispatch_local_tasks", MagicMock(return_value=0))
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", MagicMock(return_value=0))
    monkeypatch.setattr(dispatcher, "AGENT_CONFIG", {name: {} for name in ["fred", "kai", "agy", "jules", "codex"]})

    counts = dispatcher.dispatch_once(MagicMock(), pipelines={"pipelines": {}})
    status = dispatcher.get_dispatcher_polling_budget_snapshot()

    assert counts["linear_call_budget_exhausted"] == 1
    assert counts["broad_poll_skipped"] == 1
    assert status["last_cycle_calls"] == 2
    assert status["last_skip_reason"].startswith("linear call budget exhausted")


def test_ttl_cache_prevents_repeated_same_label_scan_calls(monkeypatch, tmp_path: Path):
    reset_dispatcher_budget_state(monkeypatch, tmp_path)
    calls = {"count": 0}

    def fake_gql(_query, _variables=None, *, source="test.fake"):
        calls["count"] += 1
        if dispatcher._CURRENT_POLL_BUDGET is not None:
            dispatcher._CURRENT_POLL_BUDGET.consume(source)
        return fake_team_issues()

    monkeypatch.setattr(dispatcher, "gql", fake_gql)
    budget = dispatcher.LinearCycleBudget(max_calls=6, cycle_number=1, poll_fallback_enabled=True)
    dispatcher._CURRENT_POLL_BUDGET = budget
    try:
        first = dispatcher.get_issues_with_label("agent::fred")
        second = dispatcher.get_issues_with_label("agent::fred")
    finally:
        dispatcher._CURRENT_POLL_BUDGET = None

    assert first == []
    assert second == []
    assert calls["count"] == 1
    assert budget.cache_misses == 1
    assert budget.cache_hits == 1
    assert budget.calls_used == 1


def test_cooldown_active_prevents_broad_polling(monkeypatch, tmp_path: Path):
    reset_dispatcher_budget_state(monkeypatch, tmp_path)
    LinearRateLimitState(tmp_path / "linear_rate.json").trip(reason="fixture cooldown", reset_at=future_reset(), source="test")
    get_issues = MagicMock(return_value=[])
    monkeypatch.setattr(dispatcher, "get_issues_with_label", get_issues)
    monkeypatch.setattr(dispatcher, "dispatch_local_tasks", MagicMock(return_value=0))
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", MagicMock(return_value=0))

    counts = dispatcher.dispatch_once(MagicMock(), pipelines={"pipelines": {}})
    status = dispatcher.get_dispatcher_polling_budget_snapshot()

    assert counts["linear_circuit_open"] == 1
    assert counts["broad_poll_skipped"] == 1
    get_issues.assert_not_called()
    assert status["rate_limit_cooldown_active"] is True
    assert "rate_limit_cooldown" in status["skipped_sections"]


def test_dashboard_api_exposes_polling_budget_state(monkeypatch, tmp_path: Path):
    reset_dispatcher_budget_state(monkeypatch, tmp_path)
    dispatcher._persist_polling_budget_status(
        {
            "marker": dispatcher.DISPATCHER_POLLING_BUDGET_MARKER,
            "poll_fallback_enabled": True,
            "max_calls_per_cycle": 6,
            "calls_used_this_cycle": 5,
            "last_cycle_calls": 5,
            "cache_hits": 2,
            "cache_misses": 5,
            "last_skip_reason": "cadence skip",
            "rate_limit_cooldown_active": False,
        }
    )
    client = TestClient(server.app)

    rate_limit = client.get("/api/gateway/linear/rate-limit").json()
    dispatcher_status = client.get("/api/gateway/dispatcher/status").json()

    assert rate_limit["polling_budget"]["marker"] == dispatcher.DISPATCHER_POLLING_BUDGET_MARKER
    assert rate_limit["polling_budget"]["last_cycle_calls"] == 5
    assert dispatcher_status["polling_budget"]["max_calls_per_cycle"] == 6
    assert dispatcher_status["polling_budget"]["cache_hits"] == 2
