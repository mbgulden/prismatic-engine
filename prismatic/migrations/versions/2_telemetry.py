"""Telemetry schema

Revision ID: 2
Revises: 1
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '2'
down_revision: Union[str, None] = '1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_loop_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      TEXT NOT NULL,
        issue_id    TEXT NOT NULL,
        agent       TEXT NOT NULL,
        loop_type   TEXT NOT NULL,
        trigger     TEXT,
        resolved    INTEGER DEFAULT 0,
        depth       INTEGER DEFAULT 0,
        parent_id   TEXT,
        created_at  TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_loop_run ON telemetry_loop_events(run_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_loop_agent ON telemetry_loop_events(agent, created_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_circuit_breakers (
        issue_id    TEXT PRIMARY KEY,
        agent       TEXT NOT NULL,
        micro_count INTEGER DEFAULT 0,
        macro_count INTEGER DEFAULT 0,
        last_seen   TEXT NOT NULL,
        tripped     INTEGER DEFAULT 0,
        tripped_at  TEXT
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_token_metrics (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        agent           TEXT NOT NULL,
        provider        TEXT NOT NULL,
        model           TEXT,
        prompt_tokens   INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        ttft_ms         REAL,
        tps             REAL,
        context_pct     REAL,
        vram_mb         INTEGER,
        recorded_at     TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_token_run ON telemetry_token_metrics(run_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_token_agent ON telemetry_token_metrics(agent, recorded_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_validation_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        agent           TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        total_tests     INTEGER DEFAULT 0,
        passed          INTEGER DEFAULT 0,
        failed          INTEGER DEFAULT 0,
        sandbox_id      TEXT,
        rollback        INTEGER DEFAULT 0,
        watch_sec       REAL,
        created_at      TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_validation_run ON telemetry_validation_events(run_id);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_agent_runs (
        run_id          TEXT PRIMARY KEY,
        agent           TEXT NOT NULL,
        provider        TEXT,
        model           TEXT,
        issue_id        TEXT,
        status          TEXT DEFAULT 'dispatched',
        start_time      TEXT NOT NULL,
        end_time        TEXT,
        exit_code       INTEGER,
        credits_spent   INTEGER DEFAULT 0,
        error_message   TEXT
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON telemetry_agent_runs(agent, start_time);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_credit_ledger (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        agent           TEXT NOT NULL,
        provider        TEXT NOT NULL,
        model           TEXT,
        credits_spent   INTEGER NOT NULL,
        operation       TEXT,
        recorded_at     TEXT NOT NULL,
        client_id       TEXT,
        project_id      TEXT
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_credit_ledger_run ON telemetry_credit_ledger(run_id);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS gcp_vertex_billing_ledger (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        recorded_at      TEXT NOT NULL,
        project          TEXT NOT NULL DEFAULT '',
        total_cost       REAL DEFAULT 0.0,
        credits          REAL DEFAULT 0.0,
        currency         TEXT DEFAULT 'USD',
        quota_data       TEXT,
        service_breakdown TEXT,
        error_info       TEXT,
        raw_payload      TEXT
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vertex_billing_time ON gcp_vertex_billing_ledger(recorded_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS gcp_vertex_spend_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ledger_id       INTEGER NOT NULL REFERENCES gcp_vertex_billing_ledger(id),
        model           TEXT,
        region          TEXT,
        tpm_used        INTEGER DEFAULT 0,
        rpm_used        INTEGER DEFAULT 0,
        context_pct     REAL DEFAULT 0.0,
        estimated_cost  REAL DEFAULT 0.0,
        operation       TEXT,
        recorded_at     TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vertex_spend_model ON gcp_vertex_spend_events(model, recorded_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS agy_live_state (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        active_model    TEXT,
        prompt_tokens   INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        context_usage_pct REAL,
        rate_limits     TEXT,
        raw_payload     TEXT,
        recorded_at     TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agy_live_run ON agy_live_state(run_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agy_live_time ON agy_live_state(recorded_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_review_completed (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        issue_id        TEXT NOT NULL,
        reviewer        TEXT NOT NULL,
        verdict         TEXT NOT NULL,
        impact          TEXT NOT NULL,
        rework_attempt  INTEGER DEFAULT 0,
        duration_sec    REAL DEFAULT 0.0,
        created_at      TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_review_completed_issue ON telemetry_review_completed(issue_id, created_at);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_review_completed_verdict ON telemetry_review_completed(verdict, created_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_plugin_registered (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        plugin_name     TEXT NOT NULL,
        plugin_version  TEXT,
        source          TEXT,
        success         INTEGER DEFAULT 0,
        error           TEXT,
        created_at      TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_plugin_name ON telemetry_plugin_registered(plugin_name, created_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_hook_fired (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        hook_name       TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        run_id          TEXT,
        issue_id        TEXT,
        success         INTEGER DEFAULT 0,
        error           TEXT,
        duration_ms     REAL DEFAULT 0.0,
        created_at      TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_hook_name ON telemetry_hook_fired(hook_name, created_at);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_hook_issue ON telemetry_hook_fired(issue_id, created_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_pipeline_action (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        action          TEXT NOT NULL,
        run_id          TEXT NOT NULL,
        issue_id        TEXT NOT NULL,
        actor           TEXT,
        details         TEXT,
        created_at      TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_action_issue ON telemetry_pipeline_action(issue_id, created_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_wakeup_empty (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        agent           TEXT NOT NULL,
        cycle_id        TEXT NOT NULL,
        duration_sec    REAL DEFAULT 0.0,
        reason          TEXT DEFAULT 'queue_empty',
        created_at      TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_wakeup_empty_agent ON telemetry_wakeup_empty(agent, created_at);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_wakeup_empty_time ON telemetry_wakeup_empty(created_at);")

def downgrade() -> None:
    tables = [
        "telemetry_wakeup_empty", "telemetry_pipeline_action", "telemetry_hook_fired",
        "telemetry_plugin_registered", "telemetry_review_completed", "agy_live_state",
        "gcp_vertex_spend_events", "gcp_vertex_billing_ledger", "telemetry_credit_ledger",
        "telemetry_agent_runs", "telemetry_validation_events", "telemetry_token_metrics",
        "telemetry_circuit_breakers", "telemetry_loop_events"
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table};")
