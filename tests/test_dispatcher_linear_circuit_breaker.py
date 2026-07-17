from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from prismatic import dispatcher
from prismatic.linear_rate_limit import LinearRateLimitState


def future_reset() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()


def trip_state(path: Path) -> None:
    LinearRateLimitState(path).trip(reason="test cooldown", reset_at=future_reset(), source="test")


def test_dispatch_once_skips_broad_linear_pollers_when_circuit_open(monkeypatch, tmp_path: Path):
    state = tmp_path / "linear_state.json"
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(state))
    trip_state(state)

    monkeypatch.setattr(dispatcher, "dispatch_local_tasks", MagicMock(return_value=0))
    monkeypatch.setattr(dispatcher, "load_pipeline_templates", MagicMock(return_value={"pipelines": {}}))
    setup = MagicMock(return_value=[])
    route = MagicMock(return_value=0)
    get_issues = MagicMock(return_value=[])
    recover = MagicMock()
    detect = MagicMock(return_value=0)
    cleanup = MagicMock(return_value=0)
    monkeypatch.setattr(dispatcher, "setup_pipeline_issues", setup)
    monkeypatch.setattr(dispatcher, "route_dispatch_ready_issues", route)
    monkeypatch.setattr(dispatcher, "get_issues_with_label", get_issues)
    monkeypatch.setattr(dispatcher, "recover_stalled_agy", recover)
    monkeypatch.setattr(dispatcher, "detect_origin_completions", detect)
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", cleanup)

    counts = dispatcher.dispatch_once(MagicMock(), pipelines={"pipelines": {}})

    assert counts["linear_circuit_open"] == 1
    assert counts["broad_poll_skipped"] == 1
    setup.assert_not_called()
    route.assert_not_called()
    get_issues.assert_not_called()
    recover.assert_not_called()
    detect.assert_not_called()
    cleanup.assert_called_once()


def test_dispatch_once_stops_broad_polling_after_midcycle_budget_error(monkeypatch, tmp_path: Path):
    state = tmp_path / "linear_state.json"
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(state))
    monkeypatch.setattr(dispatcher, "dispatch_local_tasks", MagicMock(return_value=0))
    route = MagicMock(return_value=0)
    cleanup = MagicMock(return_value=0)
    recover = MagicMock()
    detect = MagicMock(return_value=0)
    setup = MagicMock(side_effect=dispatcher.LinearBudgetExhaustedError("cooldown"))
    monkeypatch.setattr(dispatcher, "route_dispatch_ready_issues", route)
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", cleanup)
    monkeypatch.setattr(dispatcher, "recover_stalled_agy", recover)
    monkeypatch.setattr(dispatcher, "detect_origin_completions", detect)
    monkeypatch.setattr(dispatcher, "setup_pipeline_issues", setup)
    get_issues = MagicMock(return_value=[])
    monkeypatch.setattr(dispatcher, "get_issues_with_label", get_issues)

    counts = dispatcher.dispatch_once(MagicMock(), pipelines={"pipelines": {}})

    assert counts["linear_circuit_open"] == 1
    assert counts["broad_poll_skipped"] == 1
    route.assert_not_called()
    get_issues.assert_not_called()
    recover.assert_not_called()
    detect.assert_not_called()


def test_gql_raises_budget_exhausted_before_urlopen_when_circuit_open(monkeypatch, tmp_path: Path):
    state = tmp_path / "linear_state.json"
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(state))
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test_key")
    trip_state(state)

    def fail_urlopen(*_args, **_kwargs):  # pragma: no cover - should never run
        raise AssertionError("urlopen should not be called while circuit is open")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(dispatcher.LinearBudgetExhaustedError):
        dispatcher.gql("query { viewer { id } }", source="test.no_network")
