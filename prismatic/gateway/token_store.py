"""Portal API token store (Portal Phase 1, P0 #5).

Server-side SQLite store for revocable portal API tokens scoped to the
``viewer`` / ``operator`` portal roles.  Raw secrets are shown exactly once
(at mint) and are never persisted: only their SHA-256 digests are stored.

Location: ``$PRISMATIC_STATE_DIR/db/portal_tokens.db`` (``~/.prismatic`` by
default).  The database file is created mode 0600 and opens fail closed when
group/world permission bits are present, mirroring ``control_auth``.

A token is valid only on the instance that minted it: every row carries the
minting ``instance_id`` and verification rejects rows bound to a different
instance.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

#: Token secrets look like ``pt_<urlsafe>`` so they are recognizable in logs
#: and UIs without ever revealing the full value.
TOKEN_PREFIX: Final = "pt_"
_TOKEN_RANDOM_BYTES: Final = 36

#: Database file relative to the instance state dir.
TOKEN_DB_RELATIVE: Final = Path("db") / "portal_tokens.db"

#: Roles a portal token may be scoped to.  ``admin`` is deliberately absent:
#: tokens can never mint tokens.
TOKEN_ROLES: Final = frozenset({"viewer", "operator"})

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    secret_sha256 TEXT NOT NULL UNIQUE,
    secret_prefix TEXT NOT NULL,
    role TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_secret ON api_tokens(secret_sha256);
"""


class TokenStoreError(Exception):
    """The token store is misconfigured or unreadable (fail closed)."""


@dataclass(frozen=True)
class TokenRecord:
    """A token row.  ``secret_sha256`` is internal; never expose it."""

    id: str
    name: str
    role: str
    instance_id: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None
    revoked: bool
    revoked_at: str | None
    secret_prefix: str
    secret_sha256: str = ""

    def public(self) -> dict[str, object]:
        """Metadata safe to return from the API (no secret material)."""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "secret_prefix": self.secret_prefix,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
        }

    def is_usable(self, now: datetime | None = None) -> bool:
        if self.revoked:
            return False
        if self.expires_at:
            moment = now or datetime.now(timezone.utc)
            try:
                expires = datetime.fromisoformat(self.expires_at)
            except ValueError:
                return False
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if moment >= expires:
                return False
        return True


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mint_secret() -> str:
    """Generate a new raw token secret (shown once, never stored)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_RANDOM_BYTES)


def digest_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _enforce_permissions(path: Path) -> None:
    try:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise TokenStoreError("token database path must not be a symlink")
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise TokenStoreError("token database must be a regular file")
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise TokenStoreError("token database must be mode 0600")
    except OSError as exc:
        raise TokenStoreError("token database unreadable") from exc


class TokenStore:
    """SQLite-backed portal token store for one instance."""

    def __init__(self, db_path: Path, instance_id: str) -> None:
        if not instance_id:
            raise TokenStoreError("token store needs an instance_id")
        self._db_path = db_path
        self._instance_id = instance_id

    @classmethod
    def for_state_dir(cls, state_dir: Path, instance_id: str) -> "TokenStore":
        return cls(state_dir / TOKEN_DB_RELATIVE, instance_id)

    def _connect(self) -> sqlite3.Connection:
        exists = self._db_path.exists()
        if exists:
            _enforce_permissions(self._db_path)
        else:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._db_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        connection = sqlite3.connect(str(self._db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.executescript(_SCHEMA)
        return connection

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TokenRecord:
        return TokenRecord(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            instance_id=row["instance_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            revoked=bool(row["revoked"]),
            revoked_at=row["revoked_at"],
            secret_prefix=row["secret_prefix"],
            secret_sha256=row["secret_sha256"],
        )

    def exists(self) -> bool:
        """Whether the token database file exists (absent = no tokens)."""
        return self._db_path.exists()

    def mint(
        self, name: str, role: str, expires_in_days: int | None = None
    ) -> tuple[TokenRecord, str]:
        """Create a token.  Returns (record, raw_secret shown exactly once)."""
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise TokenStoreError("token name must be a non-empty string <= 100 chars")
        if role not in TOKEN_ROLES:
            raise TokenStoreError(f"token role must be one of {sorted(TOKEN_ROLES)}")
        if expires_in_days is not None and (
            not isinstance(expires_in_days, int)
            or expires_in_days < 1
            or expires_in_days > 3650
        ):
            raise TokenStoreError("expires_in_days must be an int in 1..3650")
        secret = mint_secret()
        digest = digest_secret(secret)
        now = _utcnow_iso()
        expires_at: str | None = None
        if expires_in_days is not None:
            expires_at = datetime.fromtimestamp(
                time.time() + expires_in_days * 86400, tz=timezone.utc
            ).isoformat()
        token_id = secrets.token_hex(16)
        record = TokenRecord(
            id=token_id,
            name=name.strip(),
            role=role,
            instance_id=self._instance_id,
            created_at=now,
            expires_at=expires_at,
            last_used_at=None,
            revoked=False,
            revoked_at=None,
            secret_prefix=secret[:12],
            secret_sha256=digest,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO api_tokens
                    (id, name, secret_sha256, secret_prefix, role, instance_id,
                     created_at, expires_at, last_used_at, revoked, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                (
                    record.id,
                    record.name,
                    record.secret_sha256,
                    record.secret_prefix,
                    record.role,
                    record.instance_id,
                    record.created_at,
                    record.expires_at,
                    None,
                ),
            )
        return record, secret

    def verify(self, secret: str) -> TokenRecord | None:
        """Authenticate a presented secret.  None when unusable (fail closed)."""
        if not secret or not secret.startswith(TOKEN_PREFIX):
            return None
        digest = digest_secret(secret)
        if not self._db_path.exists():
            return None
        try:
            connection = self._connect()
        except TokenStoreError:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM api_tokens WHERE secret_sha256 = ?", (digest,)
            ).fetchone()
            if row is None:
                return None
            record = self._row_to_record(row)
            # Constant-time compare even though the lookup already matched:
            # defense in depth against partial-match weirdness.
            if not hmac.compare_digest(record.secret_sha256, digest):
                return None
            if record.instance_id != self._instance_id:
                # A token minted on another instance is never valid here.
                return None
            if not record.is_usable():
                return None
            return record
        finally:
            connection.close()

    def touch_last_used(self, token_id: str, max_age_seconds: int = 60) -> None:
        """Refresh last_used_at, throttled to avoid a write on every request."""
        if not self._db_path.exists():
            return
        try:
            connection = self._connect()
        except TokenStoreError:
            return
        try:
            row = connection.execute(
                "SELECT last_used_at FROM api_tokens WHERE id = ?", (token_id,)
            ).fetchone()
            if row is None:
                return
            last = row["last_used_at"]
            if last:
                try:
                    previous = datetime.fromisoformat(last)
                    if previous.tzinfo is None:
                        previous = previous.replace(tzinfo=timezone.utc)
                    if (
                        datetime.now(timezone.utc) - previous
                    ).total_seconds() < max_age_seconds:
                        return
                except ValueError:
                    pass
            connection.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                (_utcnow_iso(), token_id),
            )
            connection.commit()
        finally:
            connection.close()

    def list(self) -> list[TokenRecord]:
        """All token records, newest first (metadata only)."""
        if not self._db_path.exists():
            return []
        try:
            connection = self._connect()
        except TokenStoreError:
            return []
        try:
            rows = connection.execute(
                "SELECT * FROM api_tokens ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_record(row) for row in rows]
        finally:
            connection.close()

    def revoke(self, token_id: str) -> TokenRecord | None:
        """Revoke a token.  Returns the record, or None when unknown."""
        if not self._db_path.exists():
            return None
        try:
            connection = self._connect()
        except TokenStoreError:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM api_tokens WHERE id = ?", (token_id,)
            ).fetchone()
            if row is None:
                return None
            record = self._row_to_record(row)
            if not record.revoked:
                now = _utcnow_iso()
                connection.execute(
                    "UPDATE api_tokens SET revoked = 1, revoked_at = ? WHERE id = ?",
                    (now, token_id),
                )
                connection.commit()
                record = TokenRecord(
                    **{**record.__dict__, "revoked": True, "revoked_at": now}
                )
            return record
        finally:
            connection.close()
