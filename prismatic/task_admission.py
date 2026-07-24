"""Durable, authenticated dashboard task-admission ledger.

This module records exact operator intent only.  It deliberately cannot launch a
producer, contact Linear, or publish into the generic event table consumed by
the legacy dispatcher.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator, FormatChecker

_SCHEMA_PATH = Path(__file__).with_name("schemas") / "task-admission.schema.json"
_POLICY_ENV = "PRISMATIC_TASK_ADMISSION_POLICY_FILE"
_DB_ENV = "PRISMATIC_BUS_DB"
_MAX_BODY_BYTES = 32 * 1024
_lock = threading.Lock()


class TaskAdmissionError(ValueError):
    """A bounded admission failure safe to return to an operator."""

    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AdmissionResult:
    record: dict[str, Any]
    replayed: bool


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TaskAdmissionError("duplicate_json_key", 422)
        result[key] = value
    return result


def parse_admission_json(raw: bytes) -> dict[str, Any]:
    """Parse a small JSON object while rejecting duplicate keys."""

    if not raw or len(raw) > _MAX_BODY_BYTES:
        raise TaskAdmissionError("invalid_body_size", 413 if raw else 422)
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except TaskAdmissionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskAdmissionError("invalid_json", 422) from exc
    if not isinstance(payload, dict):
        raise TaskAdmissionError("body_must_be_object", 422)
    return payload


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_schema(payload: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(
            _load_schema(), format_checker=FormatChecker()
        ).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise TaskAdmissionError("schema_validation_failed", 422)


def _load_policy(path: Path) -> tuple[set[Path], set[str], int]:
    try:
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024 or path.is_symlink() or not path.is_file():
            raise ValueError
        stat = path.stat()
        mode = stat.st_mode & 0o777
        if stat.st_uid != os.geteuid() or mode & 0o077:
            raise ValueError
        data = json.loads(raw)
    except Exception as exc:
        raise TaskAdmissionError("admission_policy_unavailable", 503) from exc
    if set(data) - {"worktrees", "producers", "max_age_seconds"}:
        raise TaskAdmissionError("admission_policy_invalid", 503)
    worktrees = data.get("worktrees")
    producers = data.get("producers")
    max_age = data.get("max_age_seconds", 300)
    if (
        not isinstance(worktrees, list)
        or not worktrees
        or any(
            not isinstance(item, str) or not Path(item).is_absolute()
            for item in worktrees
        )
        or not isinstance(producers, list)
        or not producers
        or any(not isinstance(item, str) or not item for item in producers)
        or isinstance(max_age, bool)
        or not isinstance(max_age, int)
        or not 30 <= max_age <= 3600
    ):
        raise TaskAdmissionError("admission_policy_invalid", 503)
    canonical = {Path(item).resolve(strict=True) for item in worktrees}
    return canonical, set(producers), max_age


def _resolve_db_path() -> Path:
    candidate = Path(os.environ.get(_DB_ENV) or ".prismatic/bus/event_log.sqlite")
    if not candidate.is_absolute():
        candidate = Path(os.environ.get("PRISMATIC_HOME") or Path.home()) / candidate
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _default_git_runner(worktree: Path, argument: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--verify", argument],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TaskAdmissionError("worktree_revision_unavailable", 422) from exc
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise TaskAdmissionError("worktree_revision_invalid", 422)
    return value


def _parse_created_at(value: str, *, now: datetime, max_age_seconds: int) -> None:
    try:
        created = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise TaskAdmissionError("created_at_invalid", 422) from exc
    delta = abs((now - created).total_seconds())
    if delta > max_age_seconds:
        raise TaskAdmissionError("created_at_stale", 422)


def _validate_relative_task_file(worktree: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise TaskAdmissionError("task_file_invalid", 422)
    candidate = worktree.joinpath(*pure.parts)
    current = worktree
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise TaskAdmissionError("task_file_symlink", 422)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(worktree)
    except (OSError, ValueError) as exc:
        raise TaskAdmissionError("task_file_outside_worktree", 422) from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise TaskAdmissionError("task_file_not_regular", 422)
    return resolved


class TaskAdmissionStore:
    """Validate and atomically record exact task admissions and outbox intent."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        policy_path: Path | None = None,
        git_runner: Callable[[Path, str], str] = _default_git_runner,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = db_path or _resolve_db_path()
        configured_policy = policy_path or (
            Path(os.environ[_POLICY_ENV]) if os.environ.get(_POLICY_ENV) else None
        )
        if configured_policy is None:
            raise TaskAdmissionError("admission_policy_unavailable", 503)
        self.policy_path = configured_policy
        self.git_runner = git_runner
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_admissions (
                task_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                actor TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('admitted','claimed','started','cancelled')),
                received_at TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS task_admission_outbox (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE REFERENCES task_admissions(task_id),
                topic TEXT NOT NULL CHECK(topic = 'dashboard.task.admitted.v1'),
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','claimed','processed','failed')),
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS task_admission_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS task_admissions_no_update
            BEFORE UPDATE ON task_admissions BEGIN SELECT RAISE(ABORT, 'admission_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS task_admissions_no_delete
            BEFORE DELETE ON task_admissions BEGIN SELECT RAISE(ABORT, 'admission_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS task_admission_audit_no_update
            BEFORE UPDATE ON task_admission_audit BEGIN SELECT RAISE(ABORT, 'audit_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS task_admission_audit_no_delete
            BEFORE DELETE ON task_admission_audit BEGIN SELECT RAISE(ABORT, 'audit_immutable'); END;
            """
        )
        return connection

    def _validated_payload(
        self, payload: dict[str, Any], header_key: str
    ) -> tuple[dict[str, Any], str]:
        _validate_schema(payload)
        if not hmac.compare_digest(header_key, payload["idempotency_key"]):
            raise TaskAdmissionError("idempotency_key_mismatch", 422)
        worktrees, producers, max_age = _load_policy(self.policy_path)
        if payload["producer_identity"] not in producers:
            raise TaskAdmissionError("producer_not_allowed", 422)
        raw_worktree = Path(payload["worktree"])
        try:
            worktree = raw_worktree.resolve(strict=True)
        except OSError as exc:
            raise TaskAdmissionError("worktree_unavailable", 422) from exc
        if (
            payload["worktree"] != str(worktree)
            or worktree not in worktrees
            or not worktree.is_dir()
        ):
            raise TaskAdmissionError("worktree_not_allowed", 422)
        _parse_created_at(
            payload["created_at"], now=self.now(), max_age_seconds=max_age
        )
        if self.git_runner(worktree, "HEAD") != payload["base_commit"]:
            raise TaskAdmissionError("base_commit_mismatch", 422)
        if self.git_runner(worktree, "HEAD^{tree}") != payload["base_tree"]:
            raise TaskAdmissionError("base_tree_mismatch", 422)
        task_path = _validate_relative_task_file(worktree, payload["task_file"])
        actual_digest = hashlib.sha256(task_path.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual_digest, payload["task_file_sha256"]):
            raise TaskAdmissionError("task_file_hash_mismatch", 422)
        normalized = dict(payload)
        normalized["worktree"] = str(worktree)
        normalized["task_file"] = PurePosixPath(payload["task_file"]).as_posix()
        return normalized, actual_digest

    def admit(
        self, payload: dict[str, Any], *, header_key: str, actor: str
    ) -> AdmissionResult:
        if not header_key or not actor:
            raise TaskAdmissionError("admission_context_missing", 401)

        # Exact replays remain idempotent after the freshness window or after the
        # staged worktree moves.  Authenticate and validate the immutable document
        # shape first, then consult the durable record before live precondition checks.
        _validate_schema(payload)
        if not hmac.compare_digest(header_key, payload["idempotency_key"]):
            raise TaskAdmissionError("idempotency_key_mismatch", 422)
        replay_digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
        connection = self._connect()
        try:
            existing = connection.execute(
                "SELECT * FROM task_admissions WHERE idempotency_key = ? OR task_id = ?",
                (payload["idempotency_key"], payload["task_id"]),
            ).fetchone()
        finally:
            connection.close()
        if existing is not None:
            same_key = existing["idempotency_key"] == payload["idempotency_key"]
            same_payload = hmac.compare_digest(
                existing["payload_sha256"], replay_digest
            )
            if same_key and same_payload:
                return AdmissionResult(self._record(existing), replayed=True)
            code = "idempotency_conflict" if same_key else "task_already_admitted"
            raise TaskAdmissionError(code, 409)

        normalized, _ = self._validated_payload(payload, header_key)
        canonical = _canonical_json(normalized)
        payload_digest = hashlib.sha256(canonical.encode()).hexdigest()
        received_at = self.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        event_id = (
            "task-admission:"
            + hashlib.sha256(
                f"{normalized['task_id']}:{normalized['idempotency_key']}:{payload_digest}".encode()
            ).hexdigest()
        )
        outbox_payload = _canonical_json(
            {
                **normalized,
                "actor": actor,
                "payload_sha256": payload_digest,
                "event_id": event_id,
                "received_at": received_at,
            }
        )
        with _lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM task_admissions WHERE idempotency_key = ? OR task_id = ?",
                    (normalized["idempotency_key"], normalized["task_id"]),
                ).fetchone()
                if existing is not None:
                    same_key = (
                        existing["idempotency_key"] == normalized["idempotency_key"]
                    )
                    same_payload = hmac.compare_digest(
                        existing["payload_sha256"], payload_digest
                    )
                    if same_key and same_payload:
                        connection.rollback()
                        return AdmissionResult(self._record(existing), replayed=True)
                    connection.rollback()
                    code = (
                        "idempotency_conflict" if same_key else "task_already_admitted"
                    )
                    raise TaskAdmissionError(code, 409)
                connection.execute(
                    "INSERT INTO task_admissions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        normalized["task_id"],
                        normalized["idempotency_key"],
                        payload_digest,
                        canonical,
                        actor,
                        "admitted",
                        received_at,
                        event_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO task_admission_outbox(event_id,task_id,topic,payload_json,status,created_at) VALUES (?,?,?,?,?,?)",
                    (
                        event_id,
                        normalized["task_id"],
                        "dashboard.task.admitted.v1",
                        outbox_payload,
                        "pending",
                        received_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO task_admission_audit(task_id,event,actor,payload_sha256,created_at) VALUES (?,?,?,?,?)",
                    (
                        normalized["task_id"],
                        "admitted",
                        actor,
                        payload_digest,
                        received_at,
                    ),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM task_admissions WHERE task_id = ?",
                    (normalized["task_id"],),
                ).fetchone()
                assert row is not None
                return AdmissionResult(self._record(row), replayed=False)
            except TaskAdmissionError:
                raise
            except sqlite3.Error as exc:
                connection.rollback()
                raise TaskAdmissionError("admission_storage_failed", 503) from exc
            finally:
                connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        return {
            **payload,
            "actor": row["actor"],
            "payload_sha256": row["payload_sha256"],
            "received_at": row["received_at"],
            "event_id": row["event_id"],
            "status": row["status"],
        }

    def get(self, task_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM task_admissions WHERE task_id = ?", (task_id,)
            ).fetchone()
            return self._record(row) if row is not None else None
        finally:
            connection.close()

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM task_admissions ORDER BY received_at DESC, task_id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
            return [self._record(row) for row in rows]
        finally:
            connection.close()


__all__ = [
    "AdmissionResult",
    "TaskAdmissionError",
    "TaskAdmissionStore",
    "parse_admission_json",
]
