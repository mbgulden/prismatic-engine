"""Costs schema

Revision ID: 3
Revises: 2
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '3'
down_revision: Union[str, None] = '2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS dispatch_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        issue_id TEXT,
        agent TEXT NOT NULL,
        model TEXT NOT NULL,
        tokens_in INTEGER NOT NULL,
        tokens_out INTEGER NOT NULL,
        cost_dollars REAL NOT NULL,
        created_at REAL NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_costs_created_at ON dispatch_costs(created_at);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_costs_agent ON dispatch_costs(agent);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_costs_model ON dispatch_costs(model);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS billing_mapping (
        issue_id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        mapped_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_media_artifacts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        agent           TEXT NOT NULL,
        artifact_name   TEXT NOT NULL,
        media_type      TEXT NOT NULL,
        size_bytes      INTEGER NOT NULL,
        storage_uri     TEXT NOT NULL,
        recorded_at     TEXT NOT NULL
    );
    """)

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS telemetry_media_artifacts;")
    op.execute("DROP TABLE IF EXISTS billing_mapping;")
    op.execute("DROP TABLE IF EXISTS dispatch_costs;")
