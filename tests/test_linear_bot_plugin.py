from __future__ import annotations

import asyncio
import os
import sys
import yaml
import pytest
from pathlib import Path

# Ensure the plugins directory is in sys.path so we can import modules from it
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLUGINS_ROOT = _REPO_ROOT / "plugins"
if str(_PLUGINS_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_ROOT))

from prismatic.core.registry import PluginLoader
from prismatic.interface.plugin import PluginContext
from prismatic.gateway.event_bus import get_event_bus, SwarmEvent, set_event_bus, EventBus
from linear_bot.plugin import LinearBotPlugin

@pytest.fixture
def temp_bus_db(tmp_path, monkeypatch):
    """Set up a temporary SQLite event bus database path."""
    db_path = tmp_path / "test_bus.sqlite"
    monkeypatch.setattr("prismatic.gateway.event_bus._BUS_DB_PATH", db_path)
    return db_path

def test_linear_bot_manifest_and_metadata():
    """Verify that the manifest file for linear-bot contains required fields and correct schemas."""
    manifest_path = _PLUGINS_ROOT / "linear_bot" / "plugin-manifest.yaml"
    assert manifest_path.exists(), "Manifest file should exist"

    with open(manifest_path, "r") as f:
        manifest = yaml.safe_load(f)

    assert manifest["name"] == "linear-bot"
    assert manifest["modes"] == "both"
    assert "api.linear.app" in manifest["permissions"]["network"]
    assert "pwp.pipeline.failed" in manifest["events_subscribed"]
    assert "linear.issue.created" in manifest["events_published"]

def test_linear_bot_event_subscription_and_handling(temp_bus_db, monkeypatch):
    """Verify that LinearBotPlugin correctly registers to event bus and routes pipeline failures."""
    async def run_test():
        # Reset event bus
        bus = EventBus()
        set_event_bus(bus)

        # Initialize plugin
        context = PluginContext(
            config={},
            db_connection=None,
            state_dir=str(temp_bus_db.parent),
        )
        
        plugin = LinearBotPlugin()
        plugin.on_init(context)

        # Yield to allow scheduled tasks (the subscribe task) to run
        await asyncio.sleep(0)

        # Check that our handler is registered in the event bus
        assert plugin.handle_event in bus._handlers

        # Publish a simulated PWP pipeline failed event
        test_payload = {
            "pipeline_id": "pwp-run-123",
            "error": "CSS token mismatch on stage compile"
        }

        # Publish event
        published_event = await bus.publish(
            event_type="pwp.pipeline.failed",
            source="pwp-pipeline",
            payload=test_payload
        )

        # Check if a linear.issue.created event was published back onto the bus
        history = bus.get_history()
        
        issue_created_events = [e for e in history if e["type"] == "linear.issue.created"]
        assert len(issue_created_events) == 1, "Should have published exactly one issue creation event"

        created_event = issue_created_events[0]
        assert created_event["source"] == "linear-bot"
        payload = created_event["payload"]
        assert "PWP Pipeline Failure: pwp-run-123" in payload["title"]
        assert "CSS token mismatch on stage compile" in payload["error"]
        assert payload["severity"] == "high"

    asyncio.run(run_test())

def test_loader_loads_linear_bot(temp_bus_db):
    """Verify that the PluginLoader can scan and load the linear-bot plugin from plugins directory."""
    plugins_dir = _PLUGINS_ROOT
    loader = PluginLoader(core_version="0.2.0", plugins_dir=str(plugins_dir))
    
    context = PluginContext(
        config={"plugins_dir": str(plugins_dir)},
        db_connection=None,
        state_dir=str(temp_bus_db.parent),
    )
    
    loader.scan_and_load_plugins(context)
    
    assert "linear-bot" in loader.loaded_plugins
    assert isinstance(loader.loaded_plugins["linear-bot"], LinearBotPlugin)
