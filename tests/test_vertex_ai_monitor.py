"""Tests for Vertex AI billing / quota telemetry monitor."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from prismatic.providers.billing.vertex_ai_monitor import VertexAIMonitor


@pytest.fixture
def db_path():
    """Create a temp SQLite DB for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def monitor(db_path):
    """VertexAIMonitor pointed at a temp DB."""
    return VertexAIMonitor(
        project="test-project",
        billing_account="test-billing",
        db_path=db_path,
    )


class TestVertexAIMonitor:
    """Unit tests for VertexAIMonitor."""

    def test_table_created(self, monitor):
        """The gcp_vertex_billing_ledger table should exist after init."""
        conn = sqlite3.connect(monitor._db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        names = [t[0] for t in tables]
        assert "gcp_vertex_billing_ledger" in names

    def test_ensure_table_idempotent(self, monitor):
        """Calling _ensure_table twice should not raise."""
        monitor._ensure_table()  # second call
        monitor._ensure_table()  # third call — should be idempotent
        conn = sqlite3.connect(monitor._db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = [t[0] for t in tables]
        assert "gcp_vertex_billing_ledger" in table_names

    def test_persist_empty_snapshot(self, monitor):
        """Persist a snapshot with no billing/quota data."""
        snapshot = {
            "timestamp": "2026-06-14T00:00:00+00:00",
            "project": "test-project",
            "billing": {},
            "quotas": {},
        }
        monitor._persist(snapshot)
        conn = sqlite3.connect(monitor._db_path)
        row = conn.execute(
            "SELECT * FROM gcp_vertex_billing_ledger"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[1] == "2026-06-14T00:00:00+00:00"  # recorded_at
        assert row[2] == "test-project"  # project

    def test_persist_full_snapshot(self, monitor):
        """Persist a snapshot with full billing and quota data."""
        snapshot = {
            "timestamp": "2026-06-14T01:00:00+00:00",
            "project": "test-project",
            "billing": {
                "total_cost": 42.50,
                "credits": 10.00,
                "currency": "USD",
                "services": {"aiplatform": 42.50},
                "error": None,
            },
            "quotas": {
                "quotas": {
                    "PredictRequest": {"display_name": "Predict Requests"},
                },
                "error": None,
            },
        }
        monitor._persist(snapshot)
        conn = sqlite3.connect(monitor._db_path)
        row = conn.execute(
            "SELECT total_cost, credits, currency FROM gcp_vertex_billing_ledger"
        ).fetchone()
        conn.close()
        assert row[0] == 42.50
        assert row[1] == 10.00
        assert row[2] == "USD"

    def test_persist_with_error(self, monitor):
        """Persist a snapshot with billing error info."""
        snapshot = {
            "timestamp": "2026-06-14T02:00:00+00:00",
            "project": "test-project",
            "billing": {"error": "API quota exceeded", "total_cost": 0.0, "credits": 0.0, "currency": "USD", "services": {}},
            "quotas": {"quotas": {}, "error": "Rate limited"},
        }
        monitor._persist(snapshot)
        conn = sqlite3.connect(monitor._db_path)
        row = conn.execute(
            "SELECT error_info FROM gcp_vertex_billing_ledger"
        ).fetchone()
        conn.close()
        # Should capture billing error (takes priority in _persist)
        assert row[0] is not None

    def test_get_recent_snapshots_empty(self, monitor):
        """get_recent_snapshots returns empty list when no data."""
        results = monitor.get_recent_snapshots(hours=1)
        assert results == []

    def test_get_recent_snapshots_with_data(self, monitor):
        """get_recent_snapshots returns persisted snapshots."""
        snapshot = {
            "timestamp": "2026-06-14T01:00:00+00:00",
            "project": "test-project",
            "billing": {"total_cost": 5.0, "credits": 1.0, "currency": "USD", "services": {}, "error": None},
            "quotas": {"quotas": {}, "error": None},
        }
        monitor._persist(snapshot)
        results = monitor.get_recent_snapshots(hours=24)
        assert len(results) >= 1
        assert results[0]["project"] == "test-project"
        assert results[0]["total_cost"] == 5.0
