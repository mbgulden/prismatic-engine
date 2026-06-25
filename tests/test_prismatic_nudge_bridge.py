"""Tests for prismatic_nudge_bridge.py

Covers:
- forward_one() handles the engine's nudge JSON shape
- Maps to bot-delegation's request shape with correct fields
- Idempotency: doesn't overwrite recent forwards
- Corrupt nudges get moved to processed/ (no infinite loop)
- Missing dirs → graceful exit
- --once mode processes all pending then exits
- --watch mode requires explicit invocation (smoke test)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

# Make script importable from both PE repo and Hermes scripts dir.
# Paths are env-overridable so tests run on any host.
_HOME = os.environ.get("HOME", "")
_PE_ROOT = os.environ.get("PRISMATIC_HOME", os.path.join(_HOME, "work", "prismatic-engine")) if _HOME else ""
_BRIDGE_PATHS = [
    Path(os.path.join(_HOME, ".hermes", "profiles", "orchestrator", "scripts") if _HOME else "", "prismatic_nudge_bridge.py"),
    Path(_PE_ROOT, "scripts", "prismatic_nudge_bridge.py") if _PE_ROOT else None,
]
_BRIDGE_PATHS = [p for p in _BRIDGE_PATHS if p]
for p in _BRIDGE_PATHS:
    sys.path.insert(0, str(p.parent))

import prismatic_nudge_bridge as bridge  # noqa: E402


def test_forward_one_basic_shape(tmp_path, monkeypatch):
    """Forward a single nudge → bot-delegation request with correct mapping."""
    # Override paths to use tmp_path
    monkeypatch.setattr(bridge, "NUDGE_DIR", tmp_path / "prismatic")
    monkeypatch.setattr(bridge, "BOT_DELEGATION_DIR", tmp_path / "bot-delegation" / "requests")
    monkeypatch.setattr(bridge, "PROCESSED_DIR", tmp_path / "prismatic" / "nudges-processed")
    (tmp_path / "prismatic").mkdir()
    (tmp_path / "bot-delegation" / "requests").mkdir(parents=True)

    nudge = tmp_path / "prismatic" / "nudge-fred"
    payload = {
        "target": "fred",
        "action": "work",
        "issue_id": "GRO-9999",
        "title": "Test task",
        "priority": 3,
        "metadata": {},
        "signal_id": "abc123def456789",
        "created_at": 1234567890.0,
    }
    nudge.write_text(json.dumps(payload))

    result = bridge.forward_one(nudge)

    assert result is True
    # The forwarded file exists
    forwarded_files = list((tmp_path / "bot-delegation" / "requests").glob("*.json"))
    assert len(forwarded_files) == 1
    out = json.loads(forwarded_files[0].read_text())
    # Field mapping correct
    assert out["request_type"] == "agent_signal"
    assert out["agent"] == "fred"
    assert out["issue_id"] == "GRO-9999"
    assert out["title"] == "Test task"
    assert out["priority"] == 3
    assert out["source"] == "prismatic-engine"
    # The nudge was moved to processed/
    assert not nudge.exists()
    assert (tmp_path / "prismatic" / "nudges-processed" / "nudge-fred").exists()


def test_forward_one_corrupt_moved_to_processed(tmp_path, monkeypatch):
    """Corrupt JSON gets moved to processed/ to prevent infinite retries."""
    monkeypatch.setattr(bridge, "NUDGE_DIR", tmp_path / "prismatic")
    monkeypatch.setattr(bridge, "BOT_DELEGATION_DIR", tmp_path / "bot-delegation" / "requests")
    monkeypatch.setattr(bridge, "PROCESSED_DIR", tmp_path / "prismatic" / "nudges-processed")
    (tmp_path / "prismatic").mkdir()
    (tmp_path / "bot-delegation" / "requests").mkdir(parents=True)

    corrupt = tmp_path / "prismatic" / "nudge-fred"
    corrupt.write_text("not json {{{{")

    result = bridge.forward_one(corrupt)

    assert result is False
    assert not corrupt.exists()
    assert (tmp_path / "prismatic" / "nudges-processed" / "nudge-fred").exists()


def test_forward_one_idempotent(tmp_path, monkeypatch):
    """If a recent forward exists for the same signal_id, skip and clean up."""
    monkeypatch.setattr(bridge, "NUDGE_DIR", tmp_path / "prismatic")
    monkeypatch.setattr(bridge, "BOT_DELEGATION_DIR", tmp_path / "bot-delegation" / "requests")
    monkeypatch.setattr(bridge, "PROCESSED_DIR", tmp_path / "prismatic" / "nudges-processed")
    (tmp_path / "prismatic").mkdir()
    bot_dir = tmp_path / "bot-delegation" / "requests"
    bot_dir.mkdir(parents=True)

    # Pre-existing forward (recent)
    existing = bot_dir / "prismatic-abc123def456-fred.json"
    existing.write_text(json.dumps({"old": True}))
    mtime = existing.stat().st_mtime

    nudge = tmp_path / "prismatic" / "nudge-fred"
    nudge.write_text(json.dumps({
        "target": "fred", "issue_id": "X", "title": "t", "signal_id": "abc123def456",
    }))

    bridge.forward_one(nudge)

    # Nudge was still moved to processed (cleanup)
    assert not nudge.exists()
    # But the existing forward wasn't overwritten
    assert existing.stat().st_mtime == mtime
    assert json.loads(existing.read_text()) == {"old": True}


def test_process_pending_handles_missing_dir(monkeypatch):
    """When NUDGE_DIR doesn't exist, return 0 (no error)."""
    monkeypatch.setattr(bridge, "NUDGE_DIR", Path("/nonexistent/path/xyz123"))
    result = bridge.process_pending()
    assert result == 0


def test_process_pending_handles_no_nudges(tmp_path, monkeypatch):
    """When NUDGE_DIR exists but has no nudge files, return 0."""
    monkeypatch.setattr(bridge, "NUDGE_DIR", tmp_path / "prismatic")
    monkeypatch.setattr(bridge, "BOT_DELEGATION_DIR", tmp_path / "bot-delegation" / "requests")
    (tmp_path / "prismatic").mkdir()
    result = bridge.process_pending()
    assert result == 0


def test_process_pending_multiple_nudges(tmp_path, monkeypatch):
    """Process multiple nudges in one call."""
    monkeypatch.setattr(bridge, "NUDGE_DIR", tmp_path / "prismatic")
    monkeypatch.setattr(bridge, "BOT_DELEGATION_DIR", tmp_path / "bot-delegation" / "requests")
    monkeypatch.setattr(bridge, "PROCESSED_DIR", tmp_path / "prismatic" / "nudges-processed")
    (tmp_path / "prismatic").mkdir()
    (tmp_path / "bot-delegation" / "requests").mkdir(parents=True)

    for agent in ["fred", "kai", "ned"]:
        nudge = tmp_path / "prismatic" / f"nudge-{agent}"
        nudge.write_text(json.dumps({
            "target": agent, "issue_id": f"GRO-{agent}",
            "title": f"Task for {agent}", "signal_id": f"sig-{agent}",
        }))

    result = bridge.process_pending()

    assert result == 3
    # All 3 in bot-delegation
    bot_files = list((tmp_path / "bot-delegation" / "requests").glob("*.json"))
    assert len(bot_files) == 3
    agents = {json.loads(f.read_text())["agent"] for f in bot_files}
    assert agents == {"fred", "kai", "ned"}
    # All 3 in processed
    processed = list((tmp_path / "prismatic" / "nudges-processed").glob("nudge-*"))
    assert len(processed) == 3


def test_main_once_mode(tmp_path, monkeypatch, capsys):
    """--once mode processes pending nudges and exits."""
    monkeypatch.setattr(bridge, "NUDGE_DIR", tmp_path / "prismatic")
    monkeypatch.setattr(bridge, "BOT_DELEGATION_DIR", tmp_path / "bot-delegation" / "requests")
    monkeypatch.setattr(bridge, "PROCESSED_DIR", tmp_path / "prismatic" / "nudges-processed")
    (tmp_path / "prismatic").mkdir()
    (tmp_path / "bot-delegation" / "requests").mkdir(parents=True)

    # Create one nudge
    nudge = tmp_path / "prismatic" / "nudge-fred"
    nudge.write_text(json.dumps({
        "target": "fred", "issue_id": "GRO-1", "title": "t", "signal_id": "sig1",
    }))

    # main() reads sys.argv; mock it to default to --once
    monkeypatch.setattr(sys, "argv", ["bridge"])
    rc = bridge.main()
    assert rc == 0

    captured = capsys.readouterr()
    assert "processed 1 nudge" in captured.out


def test_main_creates_bot_delegation_dir_if_missing(tmp_path, monkeypatch):
    """When /tmp/bot-delegation/requests/ doesn't exist, the bridge creates it."""
    monkeypatch.setattr(bridge, "NUDGE_DIR", tmp_path / "prismatic")
    monkeypatch.setattr(bridge, "BOT_DELEGATION_DIR", tmp_path / "missing-dir" / "requests")
    monkeypatch.setattr(bridge, "PROCESSED_DIR", tmp_path / "prismatic" / "nudges-processed")
    (tmp_path / "prismatic").mkdir()

    nudge = tmp_path / "prismatic" / "nudge-fred"
    nudge.write_text(json.dumps({
        "target": "fred", "issue_id": "GRO-1", "title": "t", "signal_id": "sig1",
    }))

    monkeypatch.setattr(sys, "argv", ["bridge"])
    rc = bridge.main()
    assert rc == 0
    # Directory was created
    assert (tmp_path / "missing-dir" / "requests").exists()
    # File was forwarded
    assert list((tmp_path / "missing-dir" / "requests").glob("*.json"))