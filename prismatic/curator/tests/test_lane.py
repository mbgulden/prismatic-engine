"""
Tests for prismatic.curator.lane.

Covers:
- Tag rules for each event type (per SPEC.md §4)
- Persistence (idempotent on re-tag)
- Digest rendering (counts, escalations, lane stats)
- Curator stream dispatch state
- SLO-relevant behavior

Run: pytest prismatic/curator/tests/test_lane.py -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure parent dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prismatic.curator.lane import (  # noqa: E402
    BusEvent, TagResult, tag_event, init_curator_db, persist_tag,
    already_tagged, get_last_processed_rowid, update_lane_stats,
    fetch_bus_events_after, render_digest, write_digest, record_digest_run,
    CURATOR_DB, CuratorLane,
)


# === Tag rule tests ===
def make_event(source: str, topic: str, payload: dict | None = None) -> BusEvent:
    return BusEvent(rowid=0, topic=topic, payload=payload or {}, ts=0.0, source=source)


def test_linear_create_is_delegate():
    ev = make_event("linear", "Issue.create",
                    {"action": "create", "type": "Issue",
                     "data": {"identifier": "GRO-X"}})
    result = tag_event(ev)
    assert result.tag == "delegate"
    assert result.lane_hint == "triage"


def test_linear_update_with_dispatch_ready_is_delegate():
    ev = make_event("linear", "Issue.update",
                    {"action": "update", "type": "Issue",
                     "data": {"labels": [{"name": "dispatch:ready"}]}})
    result = tag_event(ev)
    assert result.tag == "delegate"


def test_linear_update_routine_is_auto_pick():
    ev = make_event("linear", "Issue.update",
                    {"action": "update", "type": "Issue",
                     "data": {"labels": [{"name": "agent:fred"}]}})
    result = tag_event(ev)
    assert result.tag == "auto-pick"


def test_linear_comment_is_drop():
    ev = make_event("linear", "Comment.create",
                    {"action": "create", "type": "Comment"})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_github_ping_is_drop():
    ev = make_event("github", "ping", {"zen": "test"})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_github_pr_opened_is_delegate_to_jules():
    ev = make_event("github", "pull_request", {"action": "opened"})
    result = tag_event(ev)
    assert result.tag == "delegate"
    assert result.lane_hint == "jules"


def test_wrapped_github_pr_opened_is_delegate_to_jules():
    ev = make_event(
        "github",
        "pull_request",
        {
            "type": "pull_request",
            "source": "github",
            "payload": {
                "action": "opened",
                "number": 99,
                "repository": {"full_name": "org/repo"},
                "pull_request": {"html_url": "https://github.com/org/repo/pull/99"},
            },
        },
    )
    result = tag_event(ev)
    assert result.tag == "delegate"
    assert result.lane_hint == "jules"


def test_wrapped_github_ping_is_drop():
    ev = make_event("github", "ping", {"type": "ping", "source": "github", "payload": {"zen": "test"}})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_webhook_auth_failed_is_escalate_before_source_rules():
    ev = make_event("linear", "webhook.auth_failed", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_webhook_ping_is_drop_before_source_rules():
    ev = make_event("github", "webhook.ping", {})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_dispatcher_agent_launched_is_auto_pick():
    ev = make_event("dispatcher:codex", "agent_launched", {})
    result = tag_event(ev)
    assert result.tag == "auto-pick"
    assert result.lane_hint == "codex"


def test_agent_failed_is_escalate():
    ev = make_event("fred", "agent_failed", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_agent_failed_preserves_lane_hint():
    ev = make_event("dispatcher:agy", "agent_failed", {"lane": "jules"})
    result = tag_event(ev)
    assert result.tag == "escalate"
    assert result.lane_hint == "agy"


def test_circuit_breaker_trip_is_escalate():
    ev = make_event("distributed_watchdog:timeout", "circuit_breaker_trip", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_budget_exceeded_is_escalate():
    ev = make_event("governance", "bus.budget.exceeded", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_webhook_generic_is_auto_pick():
    ev = make_event("webhook", "delivery", {})
    result = tag_event(ev)
    assert result.tag == "auto-pick"


def test_self_monitoring_is_drop():
    ev = make_event("internal", "agent.heartbeat", {})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_unmatched_source_is_escalate():
    ev = make_event("totally-unknown-source", "mystery.topic", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_lifecycle_close_is_auto_pick():
    ev = make_event("linear", "Issue.close",
                    {"action": "close", "type": "Issue"})
    result = tag_event(ev)
    assert result.tag == "auto-pick"


def test_nested_payload_extraction():
    """Linear payloads sometimes nest under .payload."""
    ev = make_event("linear", "Issue.update",
                    {"payload": {"action": "update", "type": "Issue",
                                 "data": {"labels": [{"name": "dispatch:ready"}]}}})
    result = tag_event(ev)
    assert result.tag == "delegate"


def test_schema_migration_and_dispatched_state(tmp_path: Path, monkeypatch):
    import prismatic.curator.lane as lane

    db_path = tmp_path / "curator.sqlite"
    monkeypatch.setattr(lane, "CURATOR_DB", db_path)
    lane.init_curator_db()

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tagged_events)")}
        assert "dispatched" in cols
        assert "dispatched_at" in cols

    lane.persist_tag(1, "delegate", "triage", "test")
    curator = lane.CuratorLane(enable_dispatch=False)
    curator._mark_dispatched(1)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT dispatched, dispatched_at FROM tagged_events WHERE rowid = 1").fetchone()
        assert row[0] == 1
        assert row[1] is not None


def test_tick_tagging_does_not_dispatch_until_dispatch_stream(tmp_path: Path, monkeypatch):
    import prismatic.curator.lane as lane

    curator_db = tmp_path / "curator.sqlite"
    bus_db = tmp_path / "bus.sqlite"
    monkeypatch.setattr(lane, "CURATOR_DB", curator_db)
    monkeypatch.setattr(lane, "BUS_DB", bus_db)

    with sqlite3.connect(bus_db) as conn:
        conn.execute("CREATE TABLE events (topic TEXT, payload_json TEXT, ts REAL)")
        conn.execute(
            "INSERT INTO events (topic, payload_json, ts) VALUES (?, ?, ?)",
            (
                "Issue.create",
                json.dumps({"source": "linear", "action": "create", "type": "Issue", "data": {"identifier": "GRO-1"}}),
                time.time(),
            ),
        )
        conn.commit()

    class Pool:
        def _reap_zombies(self):
            pass

    calls = []
    monkeypatch.setattr(lane, "get_pool", lambda: Pool())
    monkeypatch.setattr(lane, "dispatch_to_supervisor_bounded", lambda issue_id, cmd: calls.append((issue_id, cmd)) or {"status": "queued"})

    curator = lane.CuratorLane(enable_dispatch=True)
    assert curator.tick_tagging() == 1
    assert calls == []

    assert curator.tick_dispatch() == 1
    assert calls and calls[0][0] == "GRO-1"
    with sqlite3.connect(curator_db) as conn:
        assert conn.execute("SELECT dispatched FROM tagged_events").fetchone()[0] == 1


def test_failed_dispatch_remains_pending(tmp_path: Path, monkeypatch):
    import prismatic.curator.lane as lane

    curator_db = tmp_path / "curator.sqlite"
    bus_db = tmp_path / "bus.sqlite"
    monkeypatch.setattr(lane, "CURATOR_DB", curator_db)
    monkeypatch.setattr(lane, "BUS_DB", bus_db)

    with sqlite3.connect(bus_db) as conn:
        conn.execute("CREATE TABLE events (topic TEXT, payload_json TEXT, ts REAL)")
        conn.execute(
            "INSERT INTO events (topic, payload_json, ts) VALUES (?, ?, ?)",
            ("Issue.create", json.dumps({"source": "linear", "data": {"identifier": "GRO-2"}}), time.time()),
        )
        conn.commit()

    class Pool:
        def _reap_zombies(self):
            pass

    monkeypatch.setattr(lane, "get_pool", lambda: Pool())
    monkeypatch.setattr(lane, "dispatch_to_supervisor_bounded", lambda issue_id, cmd: {"status": "failed", "reason": "boom"})

    curator = lane.CuratorLane(enable_dispatch=True)
    assert curator.tick_tagging() == 1
    assert curator.tick_dispatch() == 0
    with sqlite3.connect(curator_db) as conn:
        assert conn.execute("SELECT dispatched FROM tagged_events").fetchone()[0] == 0


def test_tick_dispatch_uses_github_pr_handoff_identifier(tmp_path: Path, monkeypatch):
    import prismatic.curator.lane as lane

    curator_db = tmp_path / "curator.sqlite"
    bus_db = tmp_path / "bus.sqlite"
    monkeypatch.setattr(lane, "CURATOR_DB", curator_db)
    monkeypatch.setattr(lane, "BUS_DB", bus_db)

    with sqlite3.connect(bus_db) as conn:
        conn.execute("CREATE TABLE events (topic TEXT, payload_json TEXT, ts REAL)")
        conn.execute(
            "INSERT INTO events (topic, payload_json, ts) VALUES (?, ?, ?)",
            (
                "pull_request",
                json.dumps({
                    "type": "pull_request",
                    "source": "github",
                    "payload": {
                        "action": "opened",
                        "number": 99,
                        "repository": {"full_name": "org/repo"},
                        "pull_request": {"html_url": "https://github.com/org/repo/pull/99"},
                    },
                }),
                time.time(),
            ),
        )
        conn.commit()

    class Pool:
        def _reap_zombies(self):
            pass

    calls = []
    monkeypatch.setattr(lane, "get_pool", lambda: Pool())
    monkeypatch.setattr(lane, "dispatch_to_supervisor_bounded", lambda issue_id, cmd: calls.append((issue_id, cmd)) or {"status": "queued"})

    curator = lane.CuratorLane(enable_dispatch=True)
    assert curator.tick_tagging() == 1
    assert curator.tick_dispatch() == 1
    assert calls[0][0] == "https://github.com/org/repo/pull/99"


def test_github_webhook_publishes_header_event_type(monkeypatch):
    import asyncio
    import json as json_module
    import prismatic.gateway.event_bus as event_bus
    from prismatic.gateway.server import github_webhook

    published = []

    class Bus:
        async def publish(self, **kwargs):
            published.append(kwargs)

    class Request:
        headers = {"X-GitHub-Event": "pull_request"}

        async def body(self):
            return json_module.dumps({"action": "opened"}).encode()

    import prismatic.gateway.server as gateway_server
    monkeypatch.setattr(gateway_server, "get_github_secrets", lambda: [])
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: Bus())
    asyncio.run(github_webhook(Request()))  # type: ignore[arg-type]

    assert published == [
        {"event_type": "pull_request", "source": "github", "payload": {"action": "opened"}}
    ]


def test_ipc_bridge_accepts_webhook_control_events():
    from prismatic.gateway.ipc_bridge import validate_event

    assert validate_event({"type": "webhook.auth_failed", "source": "github"}) == (True, "")
    assert validate_event({"type": "webhook.ping", "source": "linear"}) == (True, "")


def test_gateway_auth_failed_publish_is_redacted(monkeypatch):
    import asyncio
    import prismatic.gateway.event_bus as event_bus
    from prismatic.gateway.server import _publish_webhook_auth_failed

    published = []

    class Bus:
        async def publish(self, **kwargs):
            published.append(kwargs)

    monkeypatch.setattr(event_bus, "get_event_bus", lambda: Bus())
    asyncio.run(_publish_webhook_auth_failed("github"))

    assert published == [
        {
            "event_type": "webhook.auth_failed",
            "source": "github",
            "payload": {"status": "auth-failed"},
        }
    ]
    assert "signature" not in published[0]["payload"]
