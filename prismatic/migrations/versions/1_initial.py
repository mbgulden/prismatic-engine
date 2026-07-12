"""Initial schema

Revision ID: 1
Revises: None
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '1'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS processed_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        processed_at TEXT NOT NULL DEFAULT (datetime('now')),
        source_agent TEXT,
        target_agent TEXT
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_dedup_key ON processed_events(dedup_key);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_events(processed_at);")

def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_processed_at;")
    op.execute("DROP INDEX IF EXISTS idx_dedup_key;")
    op.execute("DROP TABLE IF EXISTS processed_events;")
    op.execute("DROP TABLE IF EXISTS schema_version;")
