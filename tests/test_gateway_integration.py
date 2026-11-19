"""Gateway Server integration tests for Workspace & Deploy Routers."""

import sqlite3

from fastapi.testclient import TestClient

from prismatic.gateway.event_bus import migrate_events_to_autoincrement
from prismatic.gateway.server import app

# Legacy plain-rowid `events` schema — byte-identical columns to the live v3
# bus, minus the AUTOINCREMENT keyword (the v2-era schema the migration must
# convert). This is the exact shape of the pre-fix live table.
_PLAIN_EVENTS_DDL = """
CREATE TABLE events (
    rowid INTEGER PRIMARY KEY,
    dedup_key TEXT UNIQUE,
    topic TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ts REAL NOT NULL,
    processed INTEGER DEFAULT 0
)
"""


def _make_plain_rowid_db(path: str, rowids) -> None:
    """Create a legacy plain-rowid `events` table seeded with explicit rowids.

    ``rowids`` are inserted with their exact rowid (non-contiguous allowed) to
    mirror the live bus, where processed rows are vacuumed out leaving gaps.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_PLAIN_EVENTS_DDL)
        for rid in rowids:
            conn.execute(
                "INSERT INTO events (rowid, dedup_key, topic, payload_json, ts, processed)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (rid, f"k{rid}", "test", "{}", 1700000000.0 + rid, 1),
            )
        conn.commit()
    finally:
        conn.close()


def _ddl_is_autoincrement(path: str) -> bool:
    conn = sqlite3.connect(path)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()[0]
        return "AUTOINCREMENT" in sql.upper()
    finally:
        conn.close()


def _seq(path: str):
    conn = sqlite3.connect(path)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
        ).fetchone():
            return None
        r = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='events'"
        ).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def _rowids(path: str):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT rowid FROM events ORDER BY rowid")]
    finally:
        conn.close()


def test_bus_autoincrement_migration_preserves_rowids(tmp_path):
    """Layer (b): a plain-rowid `events` table is converted to AUTOINCREMENT
    with every existing rowid preserved, so a future emptied table can no
    longer restart rowids at 1 under the cursor (the split-bus defect).

    Fails on main (the migration function does not exist there) and passes on
    the fix branch.
    """
    db = str(tmp_path / "bus.sqlite")
    # Non-contiguous rowids mirror the live bus after a vacuum gap (max=7).
    _make_plain_rowid_db(db, [1, 2, 3, 7])

    status = migrate_events_to_autoincrement(db)
    assert status == "migrated", status

    # Schema is now the declared AUTOINCREMENT form.
    assert _ddl_is_autoincrement(db)

    # Every existing rowid is preserved exactly (nothing renumbered).
    assert _rowids(db) == [1, 2, 3, 7]

    # sqlite_sequence is pinned to the max rowid, not 0.
    assert _seq(db) == 7

    # The split-bus close condition: after the consumer's vacuum empties the
    # table, the next insert must continue from max+1 (8), NOT restart at 1.
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM events")  # what vacuum_processed() does
        conn.commit()
        conn.execute(
            "INSERT INTO events (dedup_key, topic, payload_json, ts, processed)"
            " VALUES ('fresh', 'test', '{}', 1700000999.0, 0)"
        )
        conn.commit()
        new_rowid = conn.execute(
            "SELECT rowid FROM events WHERE dedup_key='fresh'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert new_rowid == 8, f"expected rowid 8 (max+1), got {new_rowid}"


def test_bus_autoincrement_migration_is_idempotent(tmp_path):
    """Layer (b): running the migration a second time changes nothing.

    Fails on main (the migration function does not exist there) and passes on
    the fix branch.
    """
    db = str(tmp_path / "bus.sqlite")
    _make_plain_rowid_db(db, [5, 10, 14, 42])

    first = migrate_events_to_autoincrement(db)
    assert first == "migrated", first
    rowids_after_first = _rowids(db)
    seq_after_first = _seq(db)

    # Second and third runs are no-ops.
    second = migrate_events_to_autoincrement(db)
    third = migrate_events_to_autoincrement(db)
    assert second == "skipped", second
    assert third == "skipped", third

    # Nothing changed: same rowids, same seq, still AUTOINCREMENT.
    assert _rowids(db) == rowids_after_first == [5, 10, 14, 42]
    assert _seq(db) == seq_after_first == 42
    assert _ddl_is_autoincrement(db)

    # No orphaned events_old table left behind.
    conn = sqlite3.connect(db)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert "events_old" not in tables, tables


def test_gateway_workspace_and_deploy_endpoints():
    client = TestClient(app)

    r1 = client.get("/api/workspace/tree")
    assert r1.status_code == 200, f"Workspace tree endpoint returned {r1.status_code}"
    data1 = r1.json()
    assert "by_category" in data1

    r2 = client.get("/api/deploy/recent")
    assert r2.status_code == 200, f"Deploy recent endpoint returned {r2.status_code}"
    data2 = r2.json()
    assert "deploys" in data2

    # Retired links redirect into the canonical Hub Workspaces tab.
    r3 = client.get(
        "/workspaces?file=prismatic/gateway/server.py", follow_redirects=False
    )
    assert r3.status_code == 307
    assert r3.headers["location"] == (
        "/dashboard?file=prismatic%2Fgateway%2Fserver.py#workspaces"
    )
    dashboard = client.get(r3.headers["location"])
    assert dashboard.status_code == 200
    assert 'id="section-workspaces"' in dashboard.text
