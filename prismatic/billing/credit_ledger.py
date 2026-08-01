"""prismatic/billing/credit_ledger.py — ACID credit ledger for tenant billing.

Provides two implementations:
- SqliteCreditLedger — for dev/testing (uses BEGIN IMMEDIATE + row-level locking)
- PostgresCreditLedger — for production (uses SELECT FOR UPDATE)

Both enforce ACID semantics to prevent double-spending under concurrency.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Optional

# ── Defaults ────────────────────────────────────────────────
DEFAULT_SQLITE_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "credit_ledger.db",
)


# ═══════════════════════════════════════════════════════════════
# Domain Types
# ═══════════════════════════════════════════════════════════════


class CreditError(Exception):
    """Raised on credit policy violations (insufficient funds, frozen)."""


class TenantState:
    """Tenant account lifecycle state."""
    ACTIVE = "active"
    FROZEN = "frozen"      # subscription cancelled / payment failed
    SUSPENDED = "suspended"  # admin action


# ═══════════════════════════════════════════════════════════════
# Abstract Ledger
# ═══════════════════════════════════════════════════════════════


class CreditLedger(ABC):
    """Abstract credit ledger with ACID semantics.

    All balance-changing operations use database-level locking to
    prevent double-spend (SELECT FOR UPDATE in Postgres, row-level
    locking in SQLite).
    """

    @abstractmethod
    def get_balance(self, tenant_id: str) -> int:
        """Return current credit balance for a tenant (micro-dollars)."""

    @abstractmethod
    def get_state(self, tenant_id: str) -> str:
        """Return tenant state: active, frozen, or suspended."""

    @abstractmethod
    def set_state(self, tenant_id: str, state: str) -> None:
        """Set tenant state (e.g. freeze on payment failure)."""

    @abstractmethod
    def add_credits(
        self, tenant_id: str, amount: int, reason: str = ""
    ) -> int:
        """Add credits to a tenant balance. Returns new balance.

        Args:
            tenant_id: Stripe customer ID or internal tenant ID.
            amount: Credits in micro-dollars (positive integer).
            reason: Audit trail string (e.g. "stripe:invoice.pi_xxx").
        """

    @abstractmethod
    def deduct_credits(
        self, tenant_id: str, amount: int, reason: str = ""
    ) -> int:
        """Deduct credits from a tenant balance under lock.

        Raises CreditError if insufficient funds or tenant frozen.
        Returns new balance.

        Args:
            tenant_id: Stripe customer ID or internal tenant ID.
            amount: Credits in micro-dollars (positive integer).
            reason: Audit trail string (e.g. "job:run_xxx").
        """

    @abstractmethod
    def has_sufficient_credits(
        self, tenant_id: str, amount: int
    ) -> bool:
        """Check whether tenant has >= amount credits without locking."""

    @abstractmethod
    def get_transaction_log(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict]:
        """Return recent transaction history for a tenant."""

    @abstractmethod
    def ensure_tenant(self, tenant_id: str) -> None:
        """Create tenant row with zero balance if not exists."""


# ═══════════════════════════════════════════════════════════════
# SQLite Implementation (dev/testing)
# ═══════════════════════════════════════════════════════════════


class SqliteCreditLedger(CreditLedger):
    """Credit ledger backed by SQLite with ACID semantics.

    Uses BEGIN IMMEDIATE to acquire a write lock at transaction start,
    preventing deadlocks under concurrent writers. Row-level locking
    is approximated by selecting the tenant row within the transaction.

    Thread-safe via connection-per-thread (SQLite default).
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_SQLITE_PATH
        self._local = threading.local()
        self._ensure_schema()

    # ── Connection management ──────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Begin IMMEDIATE transaction and yield connection.

        BEGIN IMMEDIATE acquires a reserved lock immediately — other
        writers will wait. This prevents deadlock with concurrent
        SELECT FOR UPDATE patterns.
        """
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ── Schema ─────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tenant_balance (
                    tenant_id   TEXT PRIMARY KEY,
                    balance     INTEGER NOT NULL DEFAULT 0,  -- micro-dollars
                    state       TEXT NOT NULL DEFAULT 'active',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS credit_transactions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id   TEXT NOT NULL,
                    delta       INTEGER NOT NULL,  -- positive = credit, negative = debit
                    balance_after INTEGER NOT NULL,
                    reason      TEXT DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_tenant
                    ON credit_transactions(tenant_id, created_at DESC);
            """)
            conn.commit()
        finally:
            conn.close()

    # ── Core operations (ACID) ─────────────────────────────

    def ensure_tenant(self, tenant_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tenant_balance (tenant_id, balance, state) "
                "VALUES (?, 0, 'active')",
                (tenant_id,),
            )

    def get_balance(self, tenant_id: str) -> int:
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT balance FROM tenant_balance WHERE tenant_id = ?",
            (tenant_id,),
        )
        row = cursor.fetchone()
        return row["balance"] if row else 0

    def get_state(self, tenant_id: str) -> str:
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT state FROM tenant_balance WHERE tenant_id = ?",
            (tenant_id,),
        )
        row = cursor.fetchone()
        return row["state"] if row else TenantState.ACTIVE

    def set_state(self, tenant_id: str, state: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE tenant_balance SET state = ?, updated_at = datetime('now') "
                "WHERE tenant_id = ?",
                (state, tenant_id),
            )

    def add_credits(
        self, tenant_id: str, amount: int, reason: str = ""
    ) -> int:
        if amount <= 0:
            raise ValueError(f"Credit amount must be positive: {amount}")

        with self._transaction() as conn:
            # Ensure tenant exists
            conn.execute(
                "INSERT OR IGNORE INTO tenant_balance (tenant_id, balance, state) "
                "VALUES (?, 0, 'active')",
                (tenant_id,),
            )

            # Row-level lock: update then read back
            conn.execute(
                "UPDATE tenant_balance SET balance = balance + ?, "
                "updated_at = datetime('now') WHERE tenant_id = ?",
                (amount, tenant_id),
            )

            cursor = conn.execute(
                "SELECT balance FROM tenant_balance WHERE tenant_id = ?",
                (tenant_id,),
            )
            row = cursor.fetchone()
            new_balance = row["balance"]

            conn.execute(
                "INSERT INTO credit_transactions "
                "(tenant_id, delta, balance_after, reason) "
                "VALUES (?, ?, ?, ?)",
                (tenant_id, amount, new_balance, reason or "credit_add"),
            )

        return new_balance

    def deduct_credits(
        self, tenant_id: str, amount: int, reason: str = ""
    ) -> int:
        if amount <= 0:
            raise ValueError(f"Deduction amount must be positive: {amount}")

        with self._transaction() as conn:
            # Lock and read current state in one row-level operation
            cursor = conn.execute(
                "SELECT balance, state FROM tenant_balance WHERE tenant_id = ?",
                (tenant_id,),
            )
            row = cursor.fetchone()

            if not row:
                raise CreditError(
                    f"Tenant {tenant_id} not found. Call ensure_tenant first."
                )

            if row["state"] != TenantState.ACTIVE:
                raise CreditError(
                    f"Tenant {tenant_id} is {row['state']} — cannot deduct credits."
                )

            if row["balance"] < amount:
                raise CreditError(
                    f"Tenant {tenant_id} has insufficient credits: "
                    f"{row['balance']} < {amount}"
                )

            conn.execute(
                "UPDATE tenant_balance SET balance = balance - ?, "
                "updated_at = datetime('now') WHERE tenant_id = ?",
                (amount, tenant_id),
            )

            cursor = conn.execute(
                "SELECT balance FROM tenant_balance WHERE tenant_id = ?",
                (tenant_id,),
            )
            new_balance = cursor.fetchone()["balance"]

            conn.execute(
                "INSERT INTO credit_transactions "
                "(tenant_id, delta, balance_after, reason) "
                "VALUES (?, ?, ?, ?)",
                (tenant_id, -amount, new_balance, reason or "credit_deduct"),
            )

        return new_balance

    def has_sufficient_credits(self, tenant_id: str, amount: int) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT balance, state FROM tenant_balance WHERE tenant_id = ?",
            (tenant_id,),
        )
        row = cursor.fetchone()
        if not row:
            return False
        if row["state"] != TenantState.ACTIVE:
            return False
        return row["balance"] >= amount

    def get_transaction_log(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict]:
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT id, tenant_id, delta, balance_after, reason, created_at "
            "FROM credit_transactions WHERE tenant_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (tenant_id, limit),
        )
        return [
            {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "delta": row["delta"],
                "balance_after": row["balance_after"],
                "reason": row["reason"],
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]


# ═══════════════════════════════════════════════════════════════
# PostgreSQL Implementation (production)
# ═══════════════════════════════════════════════════════════════


class PostgresCreditLedger(CreditLedger):
    """Credit ledger backed by PostgreSQL with SELECT FOR UPDATE.

    Requires psycopg2 or asyncpg installed and a valid DATABASE_URL
    in the environment.
    """

    def __init__(self, database_url: str | None = None):
        self._database_url = database_url or os.environ.get(
            "DATABASE_URL",
            "postgresql://localhost:5432/prismatic",
        )
        self._ensure_schema()

    def _get_conn(self):
        """Lazy-import psycopg2 and return a connection."""
        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgresCreditLedger. "
                "Install: pip install psycopg2-binary"
            )
        return psycopg2.connect(self._database_url)

    @contextmanager
    def _transaction(self):
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tenant_balance (
                        tenant_id   TEXT PRIMARY KEY,
                        balance     BIGINT NOT NULL DEFAULT 0,
                        state       TEXT NOT NULL DEFAULT 'active',
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE TABLE IF NOT EXISTS credit_transactions (
                        id           BIGSERIAL PRIMARY KEY,
                        tenant_id    TEXT NOT NULL,
                        delta        BIGINT NOT NULL,
                        balance_after BIGINT NOT NULL,
                        reason       TEXT DEFAULT '',
                        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_pg_trans_tenant
                        ON credit_transactions(tenant_id, created_at DESC);
                """)
            conn.commit()
        finally:
            conn.close()

    def ensure_tenant(self, tenant_id: str) -> None:
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tenant_balance (tenant_id, balance, state) "
                    "VALUES (%s, 0, 'active') "
                    "ON CONFLICT (tenant_id) DO NOTHING",
                    (tenant_id,),
                )

    def get_balance(self, tenant_id: str) -> int:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT balance FROM tenant_balance WHERE tenant_id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
                return row[0] if row else 0
        finally:
            conn.close()

    def get_state(self, tenant_id: str) -> str:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT state FROM tenant_balance WHERE tenant_id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
                return row[0] if row else TenantState.ACTIVE
        finally:
            conn.close()

    def set_state(self, tenant_id: str, state: str) -> None:
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tenant_balance SET state = %s, updated_at = NOW() "
                    "WHERE tenant_id = %s",
                    (state, tenant_id),
                )

    def add_credits(
        self, tenant_id: str, amount: int, reason: str = ""
    ) -> int:
        if amount <= 0:
            raise ValueError(f"Credit amount must be positive: {amount}")

        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tenant_balance (tenant_id, balance, state) "
                    "VALUES (%s, 0, 'active') "
                    "ON CONFLICT (tenant_id) DO NOTHING",
                    (tenant_id,),
                )

                # SELECT FOR UPDATE — lock this row
                cur.execute(
                    "SELECT balance FROM tenant_balance "
                    "WHERE tenant_id = %s FOR UPDATE",
                    (tenant_id,),
                )
                row = cur.fetchone()
                new_balance = (row[0] if row else 0) + amount

                cur.execute(
                    "UPDATE tenant_balance SET balance = %s, "
                    "updated_at = NOW() WHERE tenant_id = %s",
                    (new_balance, tenant_id),
                )
                cur.execute(
                    "INSERT INTO credit_transactions "
                    "(tenant_id, delta, balance_after, reason) "
                    "VALUES (%s, %s, %s, %s)",
                    (tenant_id, amount, new_balance, reason or "credit_add"),
                )

        return new_balance

    def deduct_credits(
        self, tenant_id: str, amount: int, reason: str = ""
    ) -> int:
        if amount <= 0:
            raise ValueError(f"Deduction amount must be positive: {amount}")

        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT balance, state FROM tenant_balance "
                    "WHERE tenant_id = %s FOR UPDATE",
                    (tenant_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise CreditError(
                        f"Tenant {tenant_id} not found. Call ensure_tenant first."
                    )

                balance, state = row
                if state != TenantState.ACTIVE:
                    raise CreditError(
                        f"Tenant {tenant_id} is {state} — cannot deduct."
                    )
                if balance < amount:
                    raise CreditError(
                        f"Tenant {tenant_id} insufficient credits: "
                        f"{balance} < {amount}"
                    )

                new_balance = balance - amount
                cur.execute(
                    "UPDATE tenant_balance SET balance = %s, "
                    "updated_at = NOW() WHERE tenant_id = %s",
                    (new_balance, tenant_id),
                )
                cur.execute(
                    "INSERT INTO credit_transactions "
                    "(tenant_id, delta, balance_after, reason) "
                    "VALUES (%s, %s, %s, %s)",
                    (tenant_id, -amount, new_balance, reason or "credit_deduct"),
                )

        return new_balance

    def has_sufficient_credits(self, tenant_id: str, amount: int) -> bool:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT balance, state FROM tenant_balance "
                    "WHERE tenant_id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                if row[1] != TenantState.ACTIVE:
                    return False
                return row[0] >= amount
        finally:
            conn.close()

    def get_transaction_log(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict]:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, tenant_id, delta, balance_after, "
                    "reason, created_at::text "
                    "FROM credit_transactions WHERE tenant_id = %s "
                    "ORDER BY id DESC LIMIT %s",
                    (tenant_id, limit),
                )
                return [
                    {
                        "id": r[0],
                        "tenant_id": r[1],
                        "delta": r[2],
                        "balance_after": r[3],
                        "reason": r[4],
                        "created_at": r[5],
                    }
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
