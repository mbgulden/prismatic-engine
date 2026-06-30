"""GRO-2995 verification tests for `gcp_vertex_spend_events` INSERT writer.

GRO-2980.6 child task — the `gcp_vertex_spend_events` table was created at
`prismatic/vertex_telemetry.py:125` but no INSERT statement existed in the
engine. Result: 0 rows.

GRO-2995 added a writer `TelemetryCollector.record_vertex_spend(...)` and
wired it into the `_drain` queue. This test file verifies:

  1. `record_vertex_spend()` is callable with the documented signature
  2. The `gcp_vertex_spend_events` table is auto-created on TelemetryCollector init
  3. After `record_vertex_spend()` + drain, the table has exactly one row
     with the right column values
  4. The acceptance-criteria query:
       SELECT COUNT(*) FROM gcp_vertex_spend_events
       WHERE recorded_at > datetime('now','-7 days')
     returns > 0 once spend is recorded
  5. Multiple records accumulate correctly
  6. Default values (tpm_used, rpm_used, context_pct, estimated_cost) work
  7. The schema in telemetry.py mirrors vertex_telemetry.py:125 (same columns
     but engine version drops the ledger_id FK to keep the credit-ledger and
     Vertex spend tables independent)

Restored 2026-06-30 alongside the existing GRO-2992 test file pattern.
"""

from __future__ import annotations

import sqlite3

import pytest

from prismatic.telemetry import TelemetryCollector


@pytest.fixture()
def telemetry_db(tmp_path):
    """TelemetryCollector backed by a temp DB. Each test owns its own collector;
    the daemon writer thread is GC'd when the fixture scope ends."""
    import time
    db_path = str(tmp_path / "test_gro_2995.db")
    collector = TelemetryCollector(db_path=db_path)
    # Give the daemon writer a moment to start before tests push events
    time.sleep(0.05)
    yield collector, db_path


def test_table_auto_created_on_init(tmp_path):
    """`gcp_vertex_spend_events` table must exist after TelemetryCollector init."""
    db_path = str(tmp_path / "init_test.db")
    TelemetryCollector(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='gcp_vertex_spend_events'"
        ).fetchone()
        assert row is not None, "gcp_vertex_spend_events table not created"
    finally:
        conn.close()


def test_record_vertex_spend_writes_one_row(telemetry_db):
    """Calling record_vertex_spend then draining yields exactly one row."""
    import time
    collector, db_path = telemetry_db
    collector.record_vertex_spend(
        project_id="my-gcp-project",
        model="gemini-2.5-pro",
        region="us-central1",
        credits=12.5,
        operation="code_generation",
    )
    # Daemon writer polls queue with 1.0s timeout — wait for it to drain
    time.sleep(1.5)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT project_id, model, region, credits, operation "
            "FROM gcp_vertex_spend_events"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    proj, model, region, credits, op = rows[0]
    assert proj == "my-gcp-project"
    assert model == "gemini-2.5-pro"
    assert region == "us-central1"
    assert abs(credits - 12.5) < 1e-9
    assert op == "code_generation"


def test_acceptance_query_returns_nonzero(telemetry_db):
    """Acceptance: COUNT(*) WHERE recorded_at > now()-7 days > 0 after a record."""
    import time
    collector, db_path = telemetry_db
    collector.record_vertex_spend(
        project_id="acc-proj",
        model="gemini-2.5-flash",
        region="us-east4",
        credits=3.14,
        operation="summarization",
    )
    time.sleep(1.5)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM gcp_vertex_spend_events "
            "WHERE recorded_at > datetime('now','-7 days')"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count > 0, (
        "Acceptance criterion failed: gcp_vertex_spend_events has 0 rows in "
        "the last 7 days after record_vertex_spend() was called."
    )


def test_multiple_records_accumulate(telemetry_db):
    """Three calls to record_vertex_spend should produce three rows."""
    import time
    collector, db_path = telemetry_db
    for i in range(3):
        collector.record_vertex_spend(
            project_id=f"proj-{i}",
            model="gemini-2.5-pro",
            region="us-central1",
            credits=float(i + 1),
            operation=f"op-{i}",
        )
    time.sleep(2.0)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM gcp_vertex_spend_events"
        ).fetchone()[0]
        credits = [r[0] for r in conn.execute(
            "SELECT credits FROM gcp_vertex_spend_events ORDER BY credits"
        ).fetchall()]
    finally:
        conn.close()
    assert count == 3
    assert credits == [1.0, 2.0, 3.0]


def test_default_values_for_optional_fields(telemetry_db):
    """When tpm_used / rpm_used / context_pct / estimated_cost are omitted,
    the schema defaults (0, 0, 0.0, 0.0) should apply, and estimated_cost
    should default to the `credits` value per the writer docstring."""
    import time
    collector, db_path = telemetry_db
    collector.record_vertex_spend(
        project_id="defaults",
        model="gemini-2.5-pro",
        region="us-central1",
        credits=7.0,
        operation="defaults_test",
    )
    time.sleep(1.5)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        row = conn.execute(
            "SELECT tpm_used, rpm_used, context_pct, estimated_cost "
            "FROM gcp_vertex_spend_events"
        ).fetchone()
    finally:
        conn.close()
    assert row == (0, 0, 0.0, 7.0), (
        f"Expected defaults (tpm=0, rpm=0, context=0.0, cost=credits=7.0), got {row}"
    )


def test_engine_table_matches_vertex_telemetry_schema():
    """The engine's gcp_vertex_spend_events schema must mirror
    prismatic/vertex_telemetry.py:125 (same logical columns), but the
    engine drops the ledger_id FK since the engine has its own credit ledger.

    This test guards against drift between the two schemas.
    """
    expected_columns = {
        "id",
        "project_id",
        "model",
        "region",
        "credits",
        "operation",
        "tpm_used",
        "rpm_used",
        "context_pct",
        "estimated_cost",
        "recorded_at",
    }
    # Compare against the CREATE TABLE statement embedded in vertex_telemetry.py
    from prismatic import vertex_telemetry

    schema_text = vertex_telemetry.LEDGER_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS gcp_vertex_spend_events" in schema_text
    # Each expected column appears in the vertex_telemetry schema
    for col in ("model", "region", "tpm_used", "rpm_used",
                "context_pct", "estimated_cost", "operation", "recorded_at"):
        assert col in schema_text, f"vertex_telemetry schema missing column {col!r}"
    # The engine schema should mention every expected column too
    # (we test this by initializing a collector and inspecting sqlite)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = f.name
        TelemetryCollector(db_path=db_path)
        conn = sqlite3.connect(db_path)
        try:
            actual_cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(gcp_vertex_spend_events)"
            ).fetchall()}
        finally:
            conn.close()
    assert expected_columns.issubset(actual_cols), (
        f"Engine table missing columns: {expected_columns - actual_cols}"
    )


def test_recorded_at_defaults_to_now_utc(telemetry_db):
    """recorded_at must auto-populate with an ISO-8601 UTC timestamp
    when not provided.

    Note: we do NOT call collector._drain() directly here — that would
    start a SECOND drainer thread and deadlock on the queue (the daemon
    writer is already draining). Instead, we wait for the existing
    daemon thread to pick up the event (same pattern as the other tests
    in this file).
    """
    import time
    from datetime import datetime, timezone

    collector, db_path = telemetry_db
    before = datetime.now(timezone.utc).isoformat()
    collector.record_vertex_spend(
        project_id="ts",
        model="gemini-2.5-pro",
        region="us-central1",
        credits=1.0,
        operation="ts_test",
    )
    # Daemon writer polls queue with 1.0s timeout — wait for drain.
    time.sleep(1.5)
    after = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        ts = conn.execute(
            "SELECT recorded_at FROM gcp_vertex_spend_events"
        ).fetchone()[0]
    finally:
        conn.close()
    assert before <= ts <= after, (
        f"recorded_at={ts!r} not in expected range [{before}, {after}]"
    )


# ── GRO-2995 Phase B: Vertex quota-snapshot → record_vertex_spend wiring ──
#
# The GRO-2995 wiring lives at `prismatic/vertex_telemetry.py:VertexBillingLedger
# .record_quota_snapshot`. Each quota record fires a `record_vertex_spend()`
# call so the acceptance-criteria query
#   SELECT COUNT(*) FROM gcp_vertex_spend_events
#   WHERE recorded_at > datetime('now','-7 days')
# returns > 0 once a real quota poll fires. These tests verify (a) the
# happy path emits one vertex_spend per quota record and (b) a telemetry
# outage does NOT break the quota-snapshot write path (which is the
# load-bearing data path).


def test_quota_snapshot_emits_vertex_spend_per_record(tmp_path):
    """`record_quota_snapshot()` writes quota data AND emits a
    corresponding `record_vertex_spend()` for each input record.

    Lifecycle: a real ``TelemetryCollector`` is mounted on the same DB
    the ledger will use; after `record_quota_snapshot` completes + the
    collector drains, `gcp_vertex_spend_events` should hold exactly one
    row per quota record.
    """
    db_path = str(tmp_path / "wiring_test.db")
    collector = TelemetryCollector(db_path=db_path)

    # Mount the collector so `get_collector()` returns it (Pass-N+44
    # Pitfall: use monkeypatch-safe singleton reset via _collector attr,
    # NOT direct module-attr assignment, because pytest may wipe it).
    import prismatic.telemetry as _tel_mod
    original = getattr(_tel_mod, "_collector", None)
    _tel_mod._collector = collector
    try:
        from prismatic.vertex_telemetry import VertexBillingLedger

        ledger = VertexBillingLedger(db_path=db_path)
        records = [
            {
                "region": "us-central1",
                "model": "gemini-2.5-pro",
                "metric_type": "tpm",
                "metric_name": "aiplatform.googleapis.com/test",
                "usage": 5000.0,
                "limit_value": 10000.0,
                "utilization_pct": 50.0,
            },
            {
                "region": "us-east4",
                "model": "gemini-2.5-flash",
                "metric_type": "rpm",
                "metric_name": "aiplatform.googleapis.com/test",
                "usage": 1200.0,
                "limit_value": 2000.0,
                "utilization_pct": 60.0,
            },
        ]
        ledger.record_quota_snapshot(records, project_id="wiring-test-pid")

        # Drain collector to flush the queued vertex_spend events.
        collector._drain()  # noqa: SLF001
    finally:
        _tel_mod._collector = original
        # Daemon writer thread will be GC'd when pytest process exits; no
        # explicit close() needed (TelemetryCollector has no close()).

    # Verify quota data is written (load-bearing path must succeed).
    conn = sqlite3.connect(db_path)
    try:
        quota_count = conn.execute(
            "SELECT COUNT(*) FROM gcp_vertex_quota_snapshots"
        ).fetchone()[0]
        assert quota_count == 2, f"expected 2 quota rows, got {quota_count}"

        # Verify spend events were emitted (1 per quota record).
        spend_rows = conn.execute(
            "SELECT project_id, model, region, credits, operation, "
            "tpm_used, rpm_used, context_pct "
            "FROM gcp_vertex_spend_events ORDER BY id"
        ).fetchall()
        assert len(spend_rows) == 2, (
            f"expected 2 spend rows, got {len(spend_rows)}"
        )

        # Row 1: tpm row from us-central1, util_pct=50 → credits=0.5
        r1 = spend_rows[0]
        assert r1[0] == "wiring-test-pid"
        assert r1[1] == "gemini-2.5-pro"
        assert r1[2] == "us-central1"
        assert abs(r1[3] - 0.5) < 1e-6, f"credits={r1[3]} expected 0.5"
        assert r1[4] == "quota_poll_tpm"
        assert r1[5] == 5000, f"tpm_used={r1[5]} expected 5000"
        assert r1[6] == 0, f"rpm_used={r1[6]} expected 0"

        # Row 2: rpm row from us-east4, util_pct=60 → credits=0.6
        r2 = spend_rows[1]
        assert r2[0] == "wiring-test-pid"
        assert r2[1] == "gemini-2.5-flash"
        assert r2[2] == "us-east4"
        assert abs(r2[3] - 0.6) < 1e-6, f"credits={r2[3]} expected 0.6"
        assert r2[4] == "quota_poll_rpm"
        assert r2[5] == 0, f"tpm_used={r2[5]} expected 0"
        assert r2[6] == 1200, f"rpm_used={r2[6]} expected 1200"
    finally:
        conn.close()


def test_quota_snapshot_succeeds_when_telemetry_is_down(tmp_path):
    """Telemetry outage MUST NOT break the quota-snapshot write.

    Per the Pass-N+44 wiring pattern, `record_vertex_spend()` calls are
    best-effort fire-and-forget. If `get_collector()` raises (e.g.,
    collector not initialized), the snapshot data MUST still land in
    `gcp_vertex_quota_snapshots`.
    """
    db_path = str(tmp_path / "outage_test.db")

    # Monkeypatch get_collector to a stub that raises — simulating a
    # telemetry outage. The quota snapshot path MUST still succeed.
    import prismatic.telemetry as _tel_mod

    def _boom():
        raise RuntimeError("simulated telemetry outage")

    original_get_collector = _tel_mod.get_collector
    _tel_mod.get_collector = _boom
    try:
        from prismatic.vertex_telemetry import VertexBillingLedger

        ledger = VertexBillingLedger(db_path=db_path)
        records = [
            {
                "region": "us-central1",
                "model": "gemini-2.5-pro",
                "metric_type": "tpm",
                "metric_name": "test",
                "usage": 1000.0,
                "limit_value": 5000.0,
                "utilization_pct": 20.0,
            }
        ]
        # MUST NOT raise despite the telemetry outage:
        ledger.record_quota_snapshot(records, project_id="outage-pid")
    finally:
        _tel_mod.get_collector = original_get_collector

    # Verify quota data still landed despite the outage.
    conn = sqlite3.connect(db_path)
    try:
        quota_count = conn.execute(
            "SELECT COUNT(*) FROM gcp_vertex_quota_snapshots"
        ).fetchone()[0]
        assert quota_count == 1, (
            f"quota snapshot path broken by telemetry outage: "
            f"expected 1 row, got {quota_count}"
        )

        spend_count = conn.execute(
            "SELECT COUNT(*) FROM gcp_vertex_spend_events"
        ).fetchone()[0]
        # No spend rows because telemetry was down — and that's OK.
        assert spend_count == 0, (
            f"expected 0 spend rows during outage, got {spend_count}"
        )
    finally:
        conn.close()