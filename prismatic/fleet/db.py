"""SQLite Multi-Agent Concurrency & WAL Hardening utilities for Prismatic Fleet.

Enforces:
- PRAGMA journal_mode = WAL;
- PRAGMA busy_timeout = 5000;
- PRAGMA synchronous = NORMAL;
- PRAGMA foreign_keys = ON;
- Exponential backoff write retries (execute_with_retry).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

logger = logging.getLogger("prismatic.db")


def init_sqlite_connection(
    db_path: Union[str, Path],
    timeout_seconds: float = 5.0,
    read_only: bool = False,
    uri: bool = False,
) -> sqlite3.Connection:
    """Initialize SQLite connection with fail-safe WAL mode and busy timeout.

    Executes mandatory PRAGMAs:
    - PRAGMA journal_mode = WAL;
    - PRAGMA busy_timeout = 5000;
    - PRAGMA synchronous = NORMAL;
    - PRAGMA foreign_keys = ON;
    """
    db_str = str(db_path)
    if read_only and not db_str.startswith("file:"):
        db_str = f"file:{db_str}?mode=ro"
        uri = True

    conn = sqlite3.connect(db_str, timeout=timeout_seconds, uri=uri)
    cursor = conn.cursor()

    is_ro = read_only or ("mode=ro" in db_str)

    if not is_ro:
        try:
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
        except sqlite3.OperationalError as exc:
            logger.debug("Could not set WAL/synchronous on %s: %s", db_str, exc)

    try:
        cursor.execute("PRAGMA busy_timeout = 5000;")       # 5000ms retry backoff
        cursor.execute("PRAGMA foreign_keys = ON;")
    except sqlite3.OperationalError as exc:
        logger.debug("Could not set busy_timeout/foreign_keys on %s: %s", db_str, exc)

    cursor.close()
    return conn


def execute_with_retry(
    conn: sqlite3.Connection,
    query_or_func: Union[str, Callable[[sqlite3.Connection], Any]],
    params: Sequence[Any] | dict[str, Any] = (),
    max_retries: int = 5,
    initial_delay: float = 0.05,
) -> Any:
    """Execute a write query or transactional callback with exponential backoff on lock contention.

    Gracefully handles transient 'database is locked' or 'busy' exceptions.
    """
    for attempt in range(max_retries):
        try:
            with conn:
                if callable(query_or_func):
                    return query_or_func(conn)
                return conn.execute(query_or_func, params)
        except sqlite3.OperationalError as exc:
            err_msg = str(exc).lower()
            if ("database is locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                sleep_time = initial_delay * (2 ** attempt)
                logger.warning(
                    "Database locked, retrying in %.2fs (attempt %d/%d)",
                    sleep_time,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(sleep_time)
            else:
                raise


def checkpoint_sqlite_database(
    db_path: Union[str, Path],
    mode: str = "TRUNCATE",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Run PRAGMA wal_checkpoint on a database to truncate WAL logs into the main db."""
    db_file = Path(db_path)
    if not db_file.exists():
        return {"path": str(db_file), "status": "NOT_FOUND"}

    try:
        conn = init_sqlite_connection(db_file, timeout_seconds=timeout_seconds)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA wal_checkpoint({mode});")
        res = cursor.fetchone()
        cursor.close()
        conn.close()
        return {
            "path": str(db_file),
            "status": "CHECKPOINTED",
            "mode": mode,
            "result": res,
        }
    except Exception as exc:
        logger.warning("Failed to checkpoint %s: %s", db_file, exc)
        return {"path": str(db_file), "status": "ERROR", "error": str(exc)}
