"""Local tasks schema

Revision ID: 5
Revises: 4
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '5'
down_revision: Union[str, None] = '4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS local_tasks (
        id TEXT PRIMARY KEY,
        agent TEXT NOT NULL,
        title TEXT NOT NULL,
        workspace TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_local_tasks_status_agent ON local_tasks(status, agent);")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS local_tasks;")
