from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from prismatic import telemetry as telemetry_module
from prismatic.telemetry import TelemetryCollector
from prismatic.vertex_telemetry import VertexBillingLedger


def _wait_for_count(db_path: str, expected: int, timeout_s: float = 3.0) -> int:
    deadline = time.monotonic() + timeout_s
    last = 0
    while time.monotonic() < deadline:
        with sqlite3.connect(db_path) as conn:
            last = conn.execute(
                "SELECT COUNT(*) FROM gcp_vertex_spend_events"
            ).fetchone()[0]
        if last >= expected:
            return last
        time.sleep(0.05)
    return last


def _quota_record(metric_type: str = "tpm", usage: int = 120, util: float = 25.0):
    return {
        "region": "us-central1",
        "model": "gemini-2.5-pro",
        "metric_type": metric_type,
        "metric_name": f"{metric_type}-quota",
        "usage": usage,
        "limit_value": 1000,
        "utilization_pct": util,
    }


def test_record_vertex_spend_creates_table_and_writes_row(tmp_path: Path):
    db_path = str(tmp_path / "telemetry.db")
    collector = TelemetryCollector(db_path=db_path)
    try:
        collector.record_vertex_spend(
            project_id="project-a",
            model="gemini-2.5-pro",
            region="us-central1",
            credits=3.5,
            operation="code_generation",
            tpm_used=42,
            context_pct=0.25,
        )

        assert _wait_for_count(db_path, 1) == 1
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """SELECT ledger.project, spend.model, spend.region,
                          ledger.credits, spend.operation, spend.tpm_used,
                          spend.rpm_used, spend.context_pct, spend.estimated_cost
                   FROM gcp_vertex_spend_events spend
                   JOIN gcp_vertex_billing_ledger ledger ON ledger.id = spend.ledger_id"""
            ).fetchone()

        assert row == (
            "project-a",
            "gemini-2.5-pro",
            "us-central1",
            3.5,
            "code_generation",
            42,
            0,
            0.25,
            3.5,
        )
    finally:
        collector._running = False


def test_record_vertex_spend_acceptance_query_returns_nonzero(tmp_path: Path):
    db_path = str(tmp_path / "telemetry.db")
    collector = TelemetryCollector(db_path=db_path)
    try:
        collector.record_vertex_spend(
            project_id="acceptance",
            model="gemini-2.5-flash",
            region="us-east4",
            credits=1.25,
            operation="summarization",
        )

        assert _wait_for_count(db_path, 1) == 1
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM gcp_vertex_spend_events "
                "WHERE recorded_at > datetime('now','-7 days')"
            ).fetchone()[0]
        assert count > 0
    finally:
        collector._running = False


def test_quota_snapshot_emits_vertex_spend_events_same_database(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "event_router.db")
    ledger = VertexBillingLedger(db_path=db_path)
    collector = TelemetryCollector(db_path=db_path)
    monkeypatch.setattr(telemetry_module, "_collector", collector)
    try:
        ledger.record_quota_snapshot(
            [_quota_record("tpm", 120, 25.0), _quota_record("rpm", 8, 50.0)],
            project_id="project-b",
        )

        assert _wait_for_count(db_path, 2) == 2
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """SELECT ledger.project, spend.operation, ledger.credits,
                          spend.tpm_used, spend.rpm_used, spend.context_pct
                   FROM gcp_vertex_spend_events spend
                   JOIN gcp_vertex_billing_ledger ledger ON ledger.id = spend.ledger_id
                   ORDER BY spend.operation"""
            ).fetchall()

        assert rows == [
            ("project-b", "quota_poll_rpm", 0.5, 0, 8, 0.5),
            ("project-b", "quota_poll_tpm", 0.25, 120, 0, 0.25),
        ]
    finally:
        collector._running = False
        monkeypatch.setattr(telemetry_module, "_collector", None)


def test_same_database_initialization_order_is_compatible(tmp_path: Path):
    first_db = str(tmp_path / "ledger_first.db")
    VertexBillingLedger(db_path=first_db)
    first_collector = TelemetryCollector(db_path=first_db)
    try:
        first_collector.record_vertex_spend(
            project_id="ledger-first",
            model="gemini-2.5-pro",
            region="us-central1",
            credits=0.75,
            operation="ledger_first",
        )
        assert _wait_for_count(first_db, 1) == 1
    finally:
        first_collector._running = False

    second_db = str(tmp_path / "collector_first.db")
    second_collector = TelemetryCollector(db_path=second_db)
    try:
        VertexBillingLedger(db_path=second_db)
        second_collector.record_vertex_spend(
            project_id="collector-first",
            model="gemini-2.5-flash",
            region="us-east4",
            credits=0.25,
            operation="collector_first",
        )
        assert _wait_for_count(second_db, 1) == 1
    finally:
        second_collector._running = False


def test_quota_snapshot_survives_telemetry_failure(tmp_path: Path, monkeypatch):
    class BrokenCollector:
        def record_vertex_spend(self, **_kwargs):
            raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(telemetry_module, "get_collector", lambda: BrokenCollector())
    ledger = VertexBillingLedger(db_path=str(tmp_path / "vertex.db"))

    ledger.record_quota_snapshot([_quota_record("tpm", 10, 10.0)], project_id="project-c")

    with sqlite3.connect(str(tmp_path / "vertex.db")) as conn:
        count = conn.execute("SELECT COUNT(*) FROM gcp_vertex_quota_snapshots").fetchone()[0]
    assert count == 1
