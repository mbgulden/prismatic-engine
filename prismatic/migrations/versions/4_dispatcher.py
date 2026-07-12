"""Dispatcher schema

Revision ID: 4
Revises: 3
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '4'
down_revision: Union[str, None] = '3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS launch_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_id TEXT NOT NULL,
        agent TEXT NOT NULL,
        status TEXT NOT NULL,
        launched_at TEXT NOT NULL,
        last_heartbeat TEXT NOT NULL,
        pid INTEGER,
        exit_code INTEGER,
        log_file TEXT,
        metadata_json TEXT
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_launch_records_issue ON launch_records(issue_id);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS agy_stall_tracker (
        issue_id TEXT PRIMARY KEY,
        stall_count INTEGER DEFAULT 0,
        last_stalled_at TEXT NOT NULL,
        recovery_status TEXT DEFAULT 'pending',
        remediated_at TEXT
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS dedup_log (
        dedup_key TEXT PRIMARY KEY,
        expires_at REAL NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_dedup_log_expiry ON dedup_log(expires_at);")

    op.execute("""
    CREATE TABLE IF NOT EXISTS label_snapshots (
        issue_id TEXT NOT NULL,
        label_id TEXT NOT NULL,
        label_name TEXT NOT NULL,
        snapshot_at REAL NOT NULL,
        PRIMARY KEY (issue_id, label_id)
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_label_snapshots_issue ON label_snapshots(issue_id);")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS label_snapshots;")
    op.execute("DROP TABLE IF EXISTS dedup_log;")
    op.execute("DROP TABLE IF EXISTS agy_stall_tracker;")
    op.execute("DROP TABLE IF EXISTS launch_records;")
