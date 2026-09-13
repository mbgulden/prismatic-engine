"""Unit tests for Prismatic Engine Agent Discovery & Signals Registry.

Verifies dynamic agent discovery, active model tracking, telemetry aggregation,
and nudge injection endpoints.
"""

from __future__ import annotations

import pytest
from prismatic.agents.registry import AgentRegistryManager, get_agent_telemetry_summary
from prismatic.agent_signal_stream import record_agent_signal, list_agent_signals


def test_agent_registry_discovery():
    agents = AgentRegistryManager.get_active_agents()
    assert isinstance(agents, list)
    assert len(agents) >= 6

    agent_ids = {a["agent_id"] for a in agents}
    assert "agy" in agent_ids
    assert "hermes" in agent_ids
    assert "kai" in agent_ids
    assert "fred" in agent_ids
    assert "george" in agent_ids
    assert "autobot" in agent_ids

    # Verify model attribution
    agy_agent = next(a for a in agents if a["agent_id"] == "agy")
    assert agy_agent["active_model"] in {"gemini-2.5-pro", "auto"}
    assert agy_agent["model_provider"] in {"Google DeepMind", "local"}


def test_agent_telemetry_summary():
    summary = get_agent_telemetry_summary()
    assert summary["status"] == "ok"
    assert summary["total_agents"] >= 6
    assert isinstance(summary["agents"], list)


def test_signal_emission_and_retrieval():
    item = record_agent_signal(
        agent="kai",
        event_type="ui_layout_verification",
        status="success",
        message="375px Playwright mobile audit clean.",
        source="unit_test",
        severity="info",
    )
    assert item["agent"] == "kai"
    assert item["event_type"] == "ui_layout_verification"

    signals = list_agent_signals(limit=10, agent="kai")
    assert signals["count"] >= 1
    assert any(s["event_type"] == "ui_layout_verification" for s in signals["items"])
