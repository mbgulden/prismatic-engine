"""Durable Jules daily capacity ledger and privacy-minimal public read model.

The private ledger stores only operational metadata needed to estimate the 300/day
Jules launch capacity. Public API payloads expose aggregate counts only: no
session IDs, issue IDs, repo names, paths, launch keys, titles, prompts, raw CLI
output, or credential-bearing strings.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JULES_DAILY_CAPACITY_MARKER = "JULES_DAILY_CAPACITY_RESOURCES_OK"
DAILY_LIMIT = 300
DEFAULT_RETENTION_DAYS = 14
FRESHNESS_TTL_SEC = 60 * 60

_AUTH_ERROR_RE = re.compile(
    r"\b(auth|authentication|authorize|authorization|login|logged out|credential|permission denied|unauthorized|forbidden)\b",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"\b(error|failed|exception|traceback|unavailable)\b", re.IGNORECASE
)
_LABELED_SESSION_RE = re.compile(
    r"\b(?:session(?:[_ -]?id)?|id)[:= ]+([A-Za-z0-9_.:-]{6,})\b", re.IGNORECASE
)
_NUMERIC_FIRST_SESSION_RE = re.compile(r"^\s*(\d{15,25})(?:\s+|\b)(.*)$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CREDENTIAL_RE = re.compile(
    r"(?i)(://[^/\s]+:[^@/\s]+@|[?&](?:token|api[_-]?key|key|secret|password|access_token)=|\b(?:ghp|github_pat|xox[baprs])-|\bAKIA[0-9A-Z]{16}\b|bearer\s+[A-Za-z0-9._=-]{12,}|token|secret|password)"
)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_SAFE_LOCATOR_RE = re.compile(r"^[A-Za-z0-9_.:/ -]{1,240}$")

_STATUS_WORDS = {
    "awaiting user": "awaiting_user",
    "awaiting plan": "awaiting_plan",
    "in progress": "active",
    "completed": "completed",
    "complete": "completed",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "awaiting": "awaiting_user",
    "planning": "awaiting_plan",
    "running": "active",
    "active": "active",
    "queued": "active",
    "accepted": "accepted",
    "launch_attempt": "accepted",
    "launched": "accepted",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (
        (dt or _utcnow())
        .astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _db_path() -> Path:
    override = os.environ.get("PRISMATIC_JULES_CAPACITY_DB_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".prismatic" / "db" / "jules_capacity.sqlite3"


def _protect_sqlite_artifacts(db: Path) -> None:
    os.chmod(db.parent, 0o700)
    for path in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if path.exists():
            os.chmod(path, 0o600)


def ensure_private_store(path: Path | None = None) -> Path:
    db = path or _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(db.parent, 0o700)
    if not db.exists():
        db.touch(mode=0o600)
    _protect_sqlite_artifacts(db)
    parent_mode = stat.S_IMODE(db.parent.stat().st_mode)
    db_mode = stat.S_IMODE(db.stat().st_mode)
    if parent_mode != 0o700 or db_mode != 0o600:
        raise PermissionError(
            f"unsafe Jules capacity store modes: parent={oct(parent_mode)} db={oct(db_mode)}"
        )
    return db


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db = ensure_private_store(path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _init(conn)
    _protect_sqlite_artifacts(db)
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jules_launches (
            launch_key TEXT PRIMARY KEY,
            launch_ts_utc TEXT NOT NULL,
            session_id_hash TEXT,
            issue_id_hash TEXT,
            repository_hash TEXT,
            source_path_hash TEXT,
            lifecycle_status TEXT NOT NULL,
            last_reconciled_at TEXT,
            error_class TEXT,
            source TEXT NOT NULL DEFAULT 'dispatcher',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cols = _columns(conn, "jules_launches")
    for name in (
        "session_id_hash",
        "issue_id_hash",
        "repository_hash",
        "source_path_hash",
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE jules_launches ADD COLUMN {name} TEXT")
    for name, ddl in {
        "source": "ALTER TABLE jules_launches ADD COLUMN source TEXT NOT NULL DEFAULT 'dispatcher'",
        "last_reconciled_at": "ALTER TABLE jules_launches ADD COLUMN last_reconciled_at TEXT",
        "error_class": "ALTER TABLE jules_launches ADD COLUMN error_class TEXT",
        "created_at": "ALTER TABLE jules_launches ADD COLUMN created_at TEXT",
        "updated_at": "ALTER TABLE jules_launches ADD COLUMN updated_at TEXT",
    }.items():
        if name not in cols:
            conn.execute(ddl)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jules_reconciliations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempted_at TEXT NOT NULL,
            available INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_class TEXT,
            source TEXT NOT NULL,
            session_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jules_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    now = _iso()
    conn.execute(
        "INSERT OR IGNORE INTO jules_metadata(key, value) VALUES ('ledger_created_at', ?)",
        (now,),
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jules_launch_ts ON jules_launches(launch_ts_utc)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jules_session_hash ON jules_launches(session_id_hash)"
    )


def _hash(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _validated_or_hash(value: str | None, *, locator: bool = False) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _CONTROL_RE.search(text) or _CREDENTIAL_RE.search(text):
        return _hash(text)
    if locator:
        return text[:240] if _SAFE_LOCATOR_RE.fullmatch(text) else _hash(text)
    return text[:120] if _SAFE_ID_RE.fullmatch(text) else _hash(text)


def _safe_error_class(value: str | None) -> str | None:
    text = (value or "").lower()
    if not text:
        return None
    if text in {
        "auth_unavailable",
        "timeout",
        "cli_error",
        "cli_unavailable",
        "ledger_unavailable",
    }:
        return text
    if _AUTH_ERROR_RE.search(text):
        return "auth_unavailable"
    if "timeout" in text:
        return "timeout"
    if _ERROR_RE.search(text):
        return "cli_error"
    return "cli_unavailable"


def _launch_key(
    *,
    launch_identity: str | None,
    issue_id: str | None,
    session_id: str | None,
    launch_ts_utc: str,
    repository: str | None,
    source_path: str | None,
) -> str:
    if launch_identity:
        identity = f"stable|{launch_identity}"
    else:
        identity = "|".join(
            [
                issue_id or "",
                session_id or "",
                launch_ts_utc,
                repository or "",
                source_path or "",
            ]
        )
    return hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()


def normalize_status(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return "active"
    compact = re.sub(r"\s+", " ", text)
    for needle, status in _STATUS_WORDS.items():
        if needle in compact:
            return status
    return "unknown"


def record_jules_launch(
    *,
    issue_id: str | None = None,
    repository: str | None = None,
    source_path: str | None = None,
    session_id: str | None = None,
    launch_identity: str | None = None,
    request_id: str | None = None,
    lifecycle_status: str = "accepted",
    launch_ts_utc: str | None = None,
    error_class: str | None = None,
    source: str = "dispatcher",
    db_path: Path | None = None,
) -> str:
    """Idempotently record a canonical Jules launch attempt/accepted launch.

    When ``launch_identity`` or ``request_id`` is present, that stable identity is
    the primary-key source, making replay at different timestamps one row.
    """
    ts = launch_ts_utc or _iso()
    stable_identity = _validated_or_hash(launch_identity or request_id)
    key = _launch_key(
        launch_identity=stable_identity,
        issue_id=_validated_or_hash(issue_id),
        session_id=_validated_or_hash(session_id),
        launch_ts_utc=ts,
        repository=_validated_or_hash(repository, locator=True),
        source_path=_validated_or_hash(source_path, locator=True),
    )
    now = _iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jules_launches (
                launch_key, launch_ts_utc, session_id_hash, issue_id_hash, repository_hash,
                source_path_hash, lifecycle_status, last_reconciled_at, error_class,
                source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(launch_key) DO UPDATE SET
                session_id_hash=COALESCE(excluded.session_id_hash, jules_launches.session_id_hash),
                lifecycle_status=excluded.lifecycle_status,
                last_reconciled_at=COALESCE(excluded.last_reconciled_at, jules_launches.last_reconciled_at),
                error_class=COALESCE(excluded.error_class, jules_launches.error_class),
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                key,
                ts,
                _hash(session_id),
                _hash(issue_id),
                _hash(repository),
                _hash(source_path),
                normalize_status(lifecycle_status),
                None,
                _safe_error_class(error_class),
                _validated_or_hash(source) or "dispatcher",
                now,
                now,
            ),
        )
        prune_retention(conn)
    _protect_sqlite_artifacts(db_path or _db_path())
    return key


def record_jules_session_id(
    *, launch_key: str, session_id: str, db_path: Path | None = None
) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE jules_launches SET session_id_hash=?, updated_at=? WHERE launch_key=?",
            (_hash(session_id), _iso(), launch_key),
        )
    return cur.rowcount > 0


def update_jules_lifecycle(
    *,
    session_id: str | None = None,
    launch_key: str | None = None,
    lifecycle_status: str = "active",
    error_class: str | None = None,
    db_path: Path | None = None,
) -> bool:
    if not session_id and not launch_key:
        return False
    if launch_key:
        clause = "launch_key=?"
        value = launch_key
    else:
        clause = "session_id_hash=?"
        value = _hash(session_id)
    now = _iso()
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"UPDATE jules_launches SET lifecycle_status=?, last_reconciled_at=?, error_class=COALESCE(?, error_class), updated_at=? WHERE {clause}",
            (
                normalize_status(lifecycle_status),
                now,
                _safe_error_class(error_class),
                now,
                value,
            ),
        )
    return cur.rowcount > 0


def parse_jules_remote_list(text: str, *, returncode: int = 0) -> dict[str, Any]:
    """Parse capped Jules list output without treating unavailable output as zero."""
    body = text or ""
    if returncode != 0 or _AUTH_ERROR_RE.search(body):
        return {
            "available": False,
            "status": "unavailable",
            "error_class": _safe_error_class(body) or "cli_unavailable",
            "sessions": [],
            "capped_partial": True,
        }
    sessions: list[dict[str, str | None]] = []
    for line in body.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped or set(stripped) <= {"-", "|", "+", " "}:
            continue
        if lower.startswith("id ") and "last active" in lower and "status" in lower:
            continue
        session_id = None
        remainder = stripped
        numeric_match = _NUMERIC_FIRST_SESSION_RE.match(stripped)
        if numeric_match:
            session_id = numeric_match.group(1)
            remainder = numeric_match.group(2)
        else:
            session_match = _LABELED_SESSION_RE.search(stripped)
            if session_match:
                session_id = session_match.group(1)
        status = normalize_status(remainder if numeric_match else stripped)
        if numeric_match and status == "unknown":
            # The real Jules table renders an empty Status cell for a live session.
            status = "active"
        if session_id or status != "unknown":
            sessions.append({"session_id": session_id, "status": status})
    if not sessions and re.search(
        r"(?im)^\s*(?:error|exception|traceback|unavailable)\b", body
    ):
        return {
            "available": False,
            "status": "unavailable",
            "error_class": _safe_error_class(body) or "cli_unavailable",
            "sessions": [],
            "capped_partial": True,
        }
    return {
        "available": True,
        "status": "ok",
        "error_class": None,
        "sessions": sessions,
        "capped_partial": True,
    }


def _record_reconciliation(
    conn: sqlite3.Connection,
    *,
    available: bool,
    status: str,
    error_class: str | None,
    source: str,
    session_count: int,
    attempted_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO jules_reconciliations(attempted_at, available, status, error_class, source, session_count) VALUES (?, ?, ?, ?, ?, ?)",
        (
            attempted_at or _iso(),
            1 if available else 0,
            status,
            _safe_error_class(error_class),
            _validated_or_hash(source) or "jules-remote-list",
            session_count,
        ),
    )


def reconcile_jules_list_output(
    text: str,
    *,
    returncode: int = 0,
    source: str = "jules-remote-list",
    attempted_at: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    parsed = parse_jules_remote_list(text, returncode=returncode)
    with _connect(db_path) as conn:
        _record_reconciliation(
            conn,
            available=bool(parsed["available"]),
            status=str(parsed["status"]),
            error_class=parsed.get("error_class"),
            source=source,
            session_count=len(parsed.get("sessions") or []),
            attempted_at=attempted_at,
        )
        updated = 0
        if parsed["available"]:
            for session in parsed["sessions"]:
                sid = session.get("session_id")
                if sid:
                    now = attempted_at or _iso()
                    cur = conn.execute(
                        "UPDATE jules_launches SET lifecycle_status=?, last_reconciled_at=?, updated_at=? WHERE session_id_hash=?",
                        (
                            normalize_status(str(session.get("status") or "unknown")),
                            now,
                            now,
                            _hash(str(sid)),
                        ),
                    )
                    updated += cur.rowcount
        prune_retention(conn)
    _protect_sqlite_artifacts(db_path or _db_path())
    return {**parsed, "updated": updated}


def prune_retention(
    conn: sqlite3.Connection, *, days: int = DEFAULT_RETENTION_DAYS
) -> None:
    cutoff = _iso(_utcnow() - timedelta(days=days))
    conn.execute("DELETE FROM jules_launches WHERE launch_ts_utc < ?", (cutoff,))
    conn.execute("DELETE FROM jules_reconciliations WHERE attempted_at < ?", (cutoff,))


def _latest_reconciliation(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jules_reconciliations ORDER BY attempted_at DESC, id DESC LIMIT 1"
    ).fetchone()


def capacity_payload(
    *, db_path: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    now_dt = (now or _utcnow()).astimezone(timezone.utc)
    window_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        with _connect(db_path) as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT launch_ts_utc, lifecycle_status, last_reconciled_at FROM jules_launches WHERE launch_ts_utc >= ? AND launch_ts_utc <= ? ORDER BY launch_ts_utc DESC",
                    (_iso(window_start), _iso(now_dt)),
                )
            ]
            ledger_created = conn.execute(
                "SELECT value FROM jules_metadata WHERE key='ledger_created_at'"
            ).fetchone()[0]
            first_ts = conn.execute(
                "SELECT MIN(launch_ts_utc) FROM jules_launches"
            ).fetchone()[0]
            latest = _latest_reconciliation(conn)
    except Exception:
        return {
            "ok": False,
            "limit": DAILY_LIMIT,
            "status": "unavailable",
            "source": "jules-capacity-ledger",
            "snapshot_at": None,
            "snapshot_age_sec": None,
            "observed_launches": None,
            "remaining_observed_capacity": None,
            "active": None,
            "awaiting": None,
            "completed": None,
            "failed": None,
            "coverage_state": "unavailable",
            "errors": [
                {"source": "jules-capacity-ledger", "error_class": "ledger_unavailable"}
            ],
            "non_claims": ["unavailable_data_is_not_zero", "full_day_coverage"],
            "marker": JULES_DAILY_CAPACITY_MARKER,
        }

    counts = {"active": 0, "awaiting": 0, "completed": 0, "failed": 0}
    for row in rows:
        status = row.get("lifecycle_status") or "unknown"
        if status in {"active", "accepted"}:
            counts["active"] += 1
        elif status.startswith("awaiting"):
            counts["awaiting"] += 1
        elif status == "completed":
            counts["completed"] += 1
        elif status == "failed":
            counts["failed"] += 1

    observed = len(rows)
    first_dt = _parse_iso(first_ts)
    ledger_dt = _parse_iso(ledger_created)
    full_day = bool(
        (first_dt and first_dt <= window_start)
        or (ledger_dt and ledger_dt <= window_start)
    )
    coverage_state = "fresh" if full_day else "partial_coverage"
    snapshot_at = latest["attempted_at"] if latest else (ledger_created or _iso(now_dt))
    snapshot_dt = _parse_iso(snapshot_at) or now_dt
    snapshot_age_sec = max(0, int((now_dt - snapshot_dt).total_seconds()))
    errors: list[dict[str, str]] = []
    if latest is None:
        status = "partial_coverage"
    elif not bool(latest["available"]):
        status = "unavailable"
        if latest["error_class"]:
            errors.append(
                {"source": latest["source"], "error_class": latest["error_class"]}
            )
    elif snapshot_age_sec > FRESHNESS_TTL_SEC:
        status = "stale"
    elif not full_day:
        status = "partial_coverage"
    else:
        status = "fresh"

    non_claims = (
        []
        if full_day and status == "fresh"
        else [
            "full_day_coverage",
            "cli_visible_list_is_complete",
            "unavailable_data_is_zero",
        ]
    )
    return {
        "ok": True,
        "limit": DAILY_LIMIT,
        "window_date_utc": window_start.date().isoformat(),
        "window_start_utc": _iso(window_start),
        "window_end_utc": _iso(now_dt),
        "observed_launches": observed,
        "remaining_observed_capacity": max(0, DAILY_LIMIT - observed),
        "active": counts["active"],
        "awaiting": counts["awaiting"],
        "completed": counts["completed"],
        "failed": counts["failed"],
        "snapshot_at": snapshot_at,
        "snapshot_age_sec": snapshot_age_sec,
        "source": "jules-capacity-ledger",
        "status": status,
        "coverage_state": coverage_state if status != "unavailable" else "unavailable",
        "errors": errors,
        "non_claims": non_claims,
        "marker": JULES_DAILY_CAPACITY_MARKER,
    }


__all__ = [
    "DAILY_LIMIT",
    "JULES_DAILY_CAPACITY_MARKER",
    "capacity_payload",
    "ensure_private_store",
    "normalize_status",
    "parse_jules_remote_list",
    "reconcile_jules_list_output",
    "record_jules_launch",
    "record_jules_session_id",
    "update_jules_lifecycle",
]
