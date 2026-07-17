from __future__ import annotations

import importlib
from pathlib import Path


def setup_runtime(tmp_path: Path, monkeypatch, *, disabled: str = ""):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(tmp_path / "linear_rate_limit_state.json"))
    monkeypatch.setenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", "1")
    monkeypatch.setenv("PRISMATIC_ENABLED_AGENTS", "kai,fred,agy")
    monkeypatch.setenv("PRISMATIC_DISABLED_AGENTS", disabled)
    import prismatic.ingestion_queue as q
    import prismatic.dispatcher as dispatcher

    q = importlib.reload(q)
    dispatcher = importlib.reload(dispatcher)
    q.ensure_queue_db()
    return q, dispatcher


def payload(identifier: str, *agents: str, event_id: str | None = None) -> dict:
    labels = [{"name": f"agent:{agent}"} for agent in agents]
    return {
        "id": event_id or f"evt-{identifier.lower()}",
        "action": "update",
        "type": "Issue",
        "data": {
            "id": f"issue-{identifier.lower()}",
            "identifier": identifier,
            "labels": {"nodes": labels},
        },
    }


def enqueue(q, item: dict) -> dict:
    result = q.enqueue_linear_event(item)
    assert result["inserted"] is True
    return result["item"]


def latest(q) -> dict:
    return q.queue_payload(limit=1)["items"][0]


def dispatch_identifier(dispatcher, identifier: str) -> dict:
    result = dispatcher.dispatch_issue_by_identifier(identifier, dry_run=True)
    assert result is not None
    return result


def test_agent_kai_resolves_and_wakes_only_kai(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-KAI", "kai"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-KAI")
    row = latest(q)

    assert result["marker"] == "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
    assert result["wakes"] == ["kai"]
    assert row["target_agent"] == "kai"
    assert row["resolver_status"] == "resolved"
    assert row["preflight_status"] == "passed"
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "kai"


def test_agent_fred_resolves_and_wakes_only_fred(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-FRED", "fred"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-FRED")
    row = latest(q)

    assert result["wakes"] == ["fred"]
    assert row["target_agent"] == "fred"
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "fred"


def test_agent_agy_resolves_and_wakes_only_agy(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-AGY", "agy"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-AGY")
    row = latest(q)

    assert result["wakes"] == ["agy"]
    assert row["target_agent"] == "agy"
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "agy"


def test_missing_agent_metadata_fails_closed_and_wakes_nobody(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-MISSING"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-MISSING")
    row = latest(q)

    assert result["status"] == "needs_manual_review"
    assert result["wakes"] == []
    assert row["dispatch_status"] == "needs_manual_review"
    assert row["resolver_status"] == "needs_manual_review"


def test_conflicting_agent_metadata_fails_closed_and_wakes_nobody(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-CONFLICT", "kai", "agy"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-CONFLICT")
    row = latest(q)

    assert result["status"] == "needs_manual_review"
    assert result["wakes"] == []
    assert "conflicting" in row["last_error"]
    assert row["dispatch_status"] == "needs_manual_review"


def test_disabled_agent_fails_preflight_and_wakes_nobody(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch, disabled="kai")
    enqueue(q, payload("GRO-TEST-DISABLED", "kai"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-DISABLED")
    row = latest(q)

    assert result["status"] == "blocked_preflight"
    assert result["wakes"] == []
    assert row["target_agent"] == "kai"
    assert row["preflight_status"] == "blocked_preflight"
    assert row["dispatch_status"] == "blocked_preflight"


def test_unknown_agent_fails_closed_and_wakes_nobody(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-UNKNOWN", "ned"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-UNKNOWN")
    row = latest(q)

    assert result["status"] == "needs_manual_review"
    assert result["wakes"] == []
    assert row["dispatch_status"] == "needs_manual_review"


def test_already_claimed_running_completed_rows_are_not_double_dispatched(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    for status in ["claimed", "processing", "dispatched"]:
        event_id = f"evt-{status}"
        item = payload(f"GRO-TEST-{status.upper()}", "fred", event_id=event_id)
        enqueue(q, item)
        q.update_assigned_dispatch_state(event_id, dispatch_status=status, target_agent="fred", claim_owner="fred")
        row = latest(q)
        result = dispatcher.dispatch_assigned_agent_event(row, dry_run=True)
        assert result["wakes"] == []
        assert result["status"] == "blocked_preflight"


def test_rate_limit_cooldown_blocks_wake_and_records_deferred(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-COOLDOWN", "agy"))
    from prismatic.linear_rate_limit import LinearRateLimitState

    LinearRateLimitState(tmp_path / "linear_rate_limit_state.json").trip(
        reason="fixture cooldown", source="test.assigned_agent", remaining=0, limit=100
    )

    result = dispatch_identifier(dispatcher, "GRO-TEST-COOLDOWN")
    row = latest(q)

    assert result["status"] == "deferred_rate_limit"
    assert result["wakes"] == []
    assert row["preflight_status"] == "deferred_rate_limit"
    assert row["dispatch_status"] == "deferred_rate_limit"


def test_queue_status_api_fields_expose_assigned_agent_state(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-API", "kai"))
    dispatch_identifier(dispatcher, "GRO-TEST-API")

    status = q.queue_status_payload()
    latest_event = status["latest_event"]

    assert status["assigned_agent_marker"] == "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
    assert latest_event["target_agent"] == "kai"
    assert latest_event["routing_source"] == "label"
    assert latest_event["resolver_status"] == "resolved"
    assert latest_event["preflight_status"] == "passed"
    assert latest_event["dispatch_status"] == "dispatched"


def test_old_poller_gate_remains_absent_and_disabled():
    assert not Path("/home/ubuntu/.prismatic/allow-poll-dispatcher").exists()
