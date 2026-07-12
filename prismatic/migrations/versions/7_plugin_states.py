"""Plugin states schema

Revision ID: 7
Revises: 6
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '7'
down_revision: Union[str, None] = '6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS plugin_states (
        plugin_name TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        status TEXT NOT NULL,
        installed_at TEXT NOT NULL,
        last_run_at TEXT,
        error_log TEXT
    );
    """)

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plugin_states;")
