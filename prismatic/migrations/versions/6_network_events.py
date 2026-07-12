"""Network events schema

Revision ID: 6
Revises: 5
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '6'
down_revision: Union[str, None] = '5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS network_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint    TEXT NOT NULL,
        event_type  TEXT NOT NULL,
        status_code INTEGER,
        headers_json TEXT,
        created_at  TEXT NOT NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_network_events_endpoint ON network_events(endpoint, created_at);")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS network_events;")
