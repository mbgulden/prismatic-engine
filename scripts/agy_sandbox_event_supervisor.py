#!/usr/bin/env python3
"""
AGY Sandbox Supervisor — Event-Driven Edition (2026-07-01, v2)

Key difference from the batch-based original:
  - Long-lived worker pool (N threads) instead of one ThreadPoolExecutor per batch
  - Event-driven swap-in: as soon as a worker finishes, the next pending task
    is pulled from the queue and launched immediately
  - Randomized timings throughout: launch jitter, completion backoff,
    worker startup stagger, and even --max-concurrent varies per run
  - Live queue: a watchdog thread periodically polls Linear for new issues
    and pushes them into the queue mid-run, so we don't have to wait for
    a new cron cycle to pick them up

Usage:
  python3 agy_sandbox_event_supervisor.py --from-linear
  python3 agy_sandbox_event_supervisor.py --issues GRO-1928,GRO-1925
  python3 agy_sandbox_event_supervisor.py --random-concurrency
  python3 agy_sandbox_event_supervisor.py --max-concurrent 2 --jitter 5-15 --backoff 8-15
"""
import os
import pwd
import sys
import json
import time
import random
import shutil
import argparse
import atexit
import subprocess
import threading
import re
import urllib.error
import urllib.request
import sqlite3
import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path
from queue import Queue, Empty
from datetime import datetime, timezone, timedelta

# Lock for event bus SQLite WAL writes
_bus_sqlite_lock = threading.Lock()

def estimate_cost(issue_id: str, model: str, elapsed_sec: float) -> float:
    """Estimate cost for an AGY session by checking the credit ledger or approximating."""
    try:
        state_dir = os.environ.get("PRISMATIC_STATE_DIR") or str(Path.home() / "work" / "prismatic-engine" / "prismatic_state")
        db_path = os.path.join(state_dir, "event_router.db")
        if os.path.exists(db_path):
            with sqlite3.connect(db_path, timeout=5) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT SUM(credits_spent) FROM telemetry_credit_ledger WHERE run_id LIKE ? OR operation LIKE ?",
                    (f"%{issue_id}%", f"%{issue_id}%")
                )
                res = cursor.fetchone()[0]
                if res is not None:
                    return float(res) / 100000.0
    except Exception:
        pass

    # Fallback to model and duration estimation
    rate_per_sec = 0.0001
    model_lower = model.lower() if model else ""
    if "flash" in model_lower:
        rate_per_sec = 0.00005
    elif "pro" in model_lower:
        rate_per_sec = 0.0005
    elif "opus" in model_lower or "gpt-4" in model_lower:
        rate_per_sec = 0.001

    estimated = rate_per_sec * elapsed_sec
    return round(max(0.001, estimated), 4)


_FAILED_PACKET_VALUES = {"ABANDONED", "BLOCKED", "ERROR", "FAIL", "FAILED"}
_EMPTY_CHANGED_FILE_VALUES = {"", "[]", "NONE", "NULL"}


def assess_result_semantics(result_path: Path, *, minimum_size: int = 1024) -> dict:
    """Fail closed when RESULT.md explicitly reports failure or missing work.

    Completion markers and peer-review labels are transport signals, not proof
    that the packet succeeded. This parser intentionally recognizes only
    explicit failure declarations; richer packet validation remains downstream.
    """
    if not result_path.is_file():
        return {
            "exists": False,
            "size": 0,
            "passed": False,
            "explicit_failure": False,
            "reasons": ["result_missing"],
        }

    text = result_path.read_text(encoding="utf-8", errors="replace")
    size = result_path.stat().st_size
    reasons: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip().replace("**", "").replace("`", "")
        line = re.sub(r"^[*-]\s+", "", line).strip()
        upper = line.upper()
        if upper.startswith("# RESULT") and "ABANDONED" in upper:
            reasons.append("status_abandoned")
        if upper.startswith("## ERROR") or re.match(r"^ERROR\s*[:=]", upper):
            reasons.append("explicit_error")
        if upper.startswith("## MISSING ARTIFACTS"):
            reasons.append("missing_artifacts")

        match = re.match(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", upper)
        if not match:
            continue
        key, value = match.groups()
        if key in {"RESULT", "STATUS"} and value in _FAILED_PACKET_VALUES:
            reasons.append(f"{key.lower()}_{value.lower()}")
        if key.endswith("_RESULT") and value in _FAILED_PACKET_VALUES:
            reasons.append(f"verifier_{key.lower()}_{value.lower()}")
        if key == "CHANGED_FILES" and value in _EMPTY_CHANGED_FILE_VALUES:
            reasons.append("changed_files_empty")

    reasons = list(dict.fromkeys(reasons))
    if size < minimum_size:
        reasons.append("result_too_small")
    explicit_failure = any(reason != "result_too_small" for reason in reasons)
    return {
        "exists": True,
        "size": size,
        "passed": not reasons,
        "explicit_failure": explicit_failure,
        "reasons": reasons,
    }


def semantic_completion(result_path: Path, *, completion_signal: bool) -> dict:
    """Bind completion signals to RESULT.md semantics."""
    assessment = assess_result_semantics(result_path)
    has_done = bool(completion_signal and assessment["passed"])
    return {
        **assessment,
        "has_done": has_done,
        "has_error": bool(assessment["explicit_failure"]),
        "has_partial_result": bool(
            assessment["exists"] and not has_done and not assessment["explicit_failure"]
        ),
    }


_AGY_PACKET_NAME = "AGY_RESULT_PACKET.json"
_AGY_COMPLETED_WORK_ROOT = (
    Path(pwd.getpwuid(os.getuid()).pw_dir) / ".prismatic" / "state" / "agy-completed-work"
)
_AGY_COMPLETED_WORK_DB = _AGY_COMPLETED_WORK_ROOT / "agy_completed_work.db"
_AGY_COMPLETED_WORK_EVIDENCE_DIR = _AGY_COMPLETED_WORK_ROOT / "evidence"


def remove_stale_agy_result_outputs(sandbox: Path) -> tuple[str, ...]:
    """Remove exact result control outputs or fail before launching AGY."""
    if type(sandbox) is not type(Path()):
        raise TypeError("sandbox must be an exact platform Path")
    removed: list[str] = []
    for name in ("RESULT.md", _AGY_PACKET_NAME):
        prior_result = sandbox / name
        if not (prior_result.exists() or prior_result.is_symlink()):
            continue
        try:
            if prior_result.is_dir() and not prior_result.is_symlink():
                shutil.rmtree(prior_result)
            else:
                prior_result.unlink()
        except OSError as exc:
            raise RuntimeError("failed to remove stale result control output") from exc
        removed.append(name)
    return tuple(removed)


def agy_raw_output_db_path() -> Path:
    """Return supervisor-owned result queue state, never release-local state."""
    override = os.environ.get("PRISMATIC_AGENT_RAW_OUTPUT_DB")
    if override:
        return Path(override).expanduser()
    return (
        _SERVICE_ACCOUNT_HOME
        / ".prismatic"
        / "state"
        / "agy-result-boundary"
        / "agent_raw_output_queue.sqlite3"
    )


def agy_completed_work_db_path() -> Path:
    """Return the dedicated supervisor-owned completed-work ledger path."""
    return _AGY_COMPLETED_WORK_DB


def agy_completed_work_evidence_dir() -> Path:
    """Return the dedicated supervisor-owned completed-work evidence path."""
    return _AGY_COMPLETED_WORK_EVIDENCE_DIR


def _bounded_nofollow_read(path: Path, byte_limit: int) -> tuple[bytes, bool]:
    """Read at most limit+1 bytes from the opened regular-file inode."""
    fd = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("selected result is not a regular file")
        chunks: list[bytes] = []
        remaining = byte_limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw_bytes = b"".join(chunks)
        return raw_bytes, len(raw_bytes) > byte_limit
    finally:
        os.close(fd)


AGY_RAW_OUTPUT_RECOVERY_MARKER = "AGY_RAW_OUTPUT_RECOVERY_OK"
AGY_RAW_OUTPUT_RECONCILIATION_GATE_MARKER = "AGY_RAW_OUTPUT_RECONCILIATION_GATE_OK"
RUNTIME_CONVERGENCE_5_MARKER = "RUNTIME_CONVERGENCE_5_OK"
_AGY_SOURCE_EVENT_RE = re.compile(
    r"^agy:(GRO-[A-Za-z0-9][A-Za-z0-9-]*):attempt:([1-9][0-9]*):sha256:([0-9a-f]{64})$"
)
_RETRY_DELAYS = (30, 120, 600, 1800, 3600)


@dataclass(frozen=True)
class AgyRawReconciliationResult:
    raw_output_id: str
    status: str
    reason: str
    completed_work_id: str | None = None
    classification: str | None = None
    integration_classification: str | None = None
    eligible_for_merge: bool = False
    ingestion_marker: str | None = None
    integration_marker: str | None = None
    recovery_marker: str | None = None
    reconciliation_gate_marker: str | None = None
    runtime_marker: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _safe_completed_result(raw_output_id, completed_row):
    from prismatic.agy_completed_work import (
        AGY_COMPLETED_WORK_INGESTION_MARKER,
        AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER,
    )
    row_dict = completed_row.as_dict()
    if (
        type(completed_row.id) is not str
        or not completed_row.id.strip()
        or type(completed_row.ingestion_marker) is not str
        or completed_row.ingestion_marker != AGY_COMPLETED_WORK_INGESTION_MARKER
        or type(row_dict) is not dict
        or type(row_dict.get("integration_marker")) is not str
        or row_dict.get("integration_marker") != AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER
    ):
        raise ValueError("invalid completed-work return")
    return AgyRawReconciliationResult(
        raw_output_id=raw_output_id,
        status="succeeded",
        reason="completed_work_persisted",
        completed_work_id=completed_row.id,
        classification=row_dict.get("classification"),
        integration_classification=row_dict.get("integration_classification"),
        eligible_for_merge=row_dict.get("eligible_for_merge") is True,
        ingestion_marker=completed_row.ingestion_marker,
        integration_marker=row_dict["integration_marker"],
        recovery_marker=AGY_RAW_OUTPUT_RECOVERY_MARKER,
        reconciliation_gate_marker=AGY_RAW_OUTPUT_RECONCILIATION_GATE_MARKER,
        runtime_marker=RUNTIME_CONVERGENCE_5_MARKER,
    )


def _load_succeeded_reconciliation(
    *, raw_output_id, delivery, completed_work_db_path, completed_work_evidence_dir
):
    if delivery.status != "succeeded":
        return None
    if type(delivery.completed_work_id) is not str or not delivery.completed_work_id.strip():
        return AgyRawReconciliationResult(
            raw_output_id, "succeeded_unavailable", "completed_work_storage_failed"
        )
    try:
        from prismatic.agy_completed_work import AgyCompletedWorkStore

        row = AgyCompletedWorkStore(
            completed_work_db_path, evidence_dir=completed_work_evidence_dir
        ).get(delivery.completed_work_id)
        return _safe_completed_result(raw_output_id, row)
    except Exception:
        return AgyRawReconciliationResult(
            raw_output_id,
            "succeeded_unavailable",
            "completed_work_storage_failed",
            completed_work_id=delivery.completed_work_id,
        )


def _terminal_reconciliation(store, claim, code, disposition, now):
    if not store.mark_delivery_failed(
        claim, error_code=code, terminal_disposition=disposition, now=now
    ):
        return AgyRawReconciliationResult(
            claim.raw_output_id, "stale_claim", "stale_claim"
        )
    return AgyRawReconciliationResult(
        claim.raw_output_id, "terminal_failed", code
    )


def _retry_reconciliation(store, claim, code, now):
    instant = now or datetime.now(timezone.utc)
    if isinstance(instant, str):
        instant = datetime.fromisoformat(instant)
    retry_at = instant + timedelta(seconds=_RETRY_DELAYS[min(claim.retry_count, 4)])
    if not store.mark_delivery_failed(
        claim, error_code=code, retry_at=retry_at, now=instant
    ):
        return AgyRawReconciliationResult(
            claim.raw_output_id, "stale_claim", "stale_claim"
        )
    delivery = store.get_delivery(claim.raw_output_id)
    return AgyRawReconciliationResult(
        claim.raw_output_id, delivery.status, delivery.last_error_code or code
    )


def _reconcile_agy_claim(
    *, claim, store, completed_work_db_path, completed_work_evidence_dir, now=None
):
    try:
        raw_text, raw_row = store._raw_text_for_delivery_claim(claim)
    except Exception:
        return _retry_reconciliation(store, claim, "completed_work_storage_failed", now)
    if Path(raw_row.raw_text_or_artifact_path).name != _AGY_PACKET_NAME:
        return _terminal_reconciliation(
            store, claim, "source_artifact_invalid", "ineligible", now
        )
    if raw_row.agent != "agy":
        return _terminal_reconciliation(store, claim, "agent_ineligible", "ineligible", now)
    if type(raw_row.task_id) is not str or not re.fullmatch(
        r"GRO-[A-Za-z0-9][A-Za-z0-9-]*", raw_row.task_id
    ):
        return _terminal_reconciliation(store, claim, "task_identity_invalid", "ineligible", now)
    match = _AGY_SOURCE_EVENT_RE.fullmatch(raw_row.source_event_id or "")
    if match is None or match.group(1) != raw_row.task_id:
        return _terminal_reconciliation(
            store, claim, "source_provenance_invalid", "provenance_invalid", now
        )
    if hashlib.sha256(raw_text.encode("utf-8")).hexdigest() != match.group(3):
        return _terminal_reconciliation(store, claim, "digest_mismatch", "provenance_invalid", now)
    try:
        packet = json.loads(raw_text)
        if type(packet) is not dict:
            raise ValueError
    except Exception:
        return _terminal_reconciliation(store, claim, "raw_json_invalid", "malformed", now)
    from prismatic.agy_result_packet import is_raw_agy_result_packet, require_valid_packet
    if not is_raw_agy_result_packet(packet):
        return _terminal_reconciliation(store, claim, "raw_dialect_invalid", "malformed", now)
    try:
        require_valid_packet(packet)
    except Exception:
        return _terminal_reconciliation(store, claim, "packet_schema_invalid", "malformed", now)
    if packet.get("issue_identifier") != raw_row.task_id:
        return _terminal_reconciliation(store, claim, "issue_identity_mismatch", "provenance_invalid", now)
    try:
        from prismatic.agy_completed_work import AgyCompletedWorkStore
        completed_row = AgyCompletedWorkStore(
            completed_work_db_path, evidence_dir=completed_work_evidence_dir
        ).ingest(packet)
        result = _safe_completed_result(claim.raw_output_id, completed_row)
    except Exception:
        return _retry_reconciliation(store, claim, "completed_work_storage_failed", now)
    if not store.mark_delivery_succeeded(
        claim, completed_work_id=result.completed_work_id, now=now
    ):
        return AgyRawReconciliationResult(claim.raw_output_id, "stale_claim", "stale_claim")
    return result


def reconcile_agy_raw_output(
    *, raw_output_id, raw_db_path, completed_work_db_path,
    completed_work_evidence_dir, lease_owner, now=None
):
    from prismatic.agent_raw_output_queue import RawAgentOutputStore
    store = RawAgentOutputStore(raw_db_path)
    delivery = store.get_delivery(raw_output_id)
    existing = _load_succeeded_reconciliation(
        raw_output_id=raw_output_id,
        delivery=delivery,
        completed_work_db_path=completed_work_db_path,
        completed_work_evidence_dir=completed_work_evidence_dir,
    )
    if existing is not None:
        return existing
    claim = store.claim_delivery(raw_output_id, lease_owner=lease_owner, now=now)
    if claim is None:
        delivery = store.get_delivery(raw_output_id)
        existing = _load_succeeded_reconciliation(
            raw_output_id=raw_output_id,
            delivery=delivery,
            completed_work_db_path=completed_work_db_path,
            completed_work_evidence_dir=completed_work_evidence_dir,
        )
        if existing is not None:
            return existing
        return AgyRawReconciliationResult(
            raw_output_id, delivery.status, delivery.last_error_code or "not_claimable",
            completed_work_id=delivery.completed_work_id,
        )
    return _reconcile_agy_claim(
        claim=claim, store=store, completed_work_db_path=completed_work_db_path,
        completed_work_evidence_dir=completed_work_evidence_dir, now=now,
    )


def reconcile_pending_agy_raw_outputs(
    *, raw_db_path, completed_work_db_path, completed_work_evidence_dir,
    lease_owner, limit=10, now=None
):
    from prismatic.agent_raw_output_queue import RawAgentOutputStore
    store = RawAgentOutputStore(raw_db_path)
    claims = store.claim_pending_deliveries(
        limit=limit, lease_owner=lease_owner, now=now
    )
    return tuple(
        _reconcile_agy_claim(
            claim=claim, store=store, completed_work_db_path=completed_work_db_path,
            completed_work_evidence_dir=completed_work_evidence_dir, now=now,
        )
        for claim in claims
    )


def run_agy_raw_recovery_batch(*, lease_owner: str, limit: int = 10):
    try:
        results = reconcile_pending_agy_raw_outputs(
            raw_db_path=agy_raw_output_db_path(),
            completed_work_db_path=agy_completed_work_db_path(),
            completed_work_evidence_dir=agy_completed_work_evidence_dir(),
            lease_owner=lease_owner, limit=limit,
        )
    except Exception:
        print("  [raw-recovery] status=storage_failed count=0", flush=True)
        return ()
    counts = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    safe_counts = ",".join(f"{key}:{counts[key]}" for key in sorted(counts)) or "none:0"
    print(f"  [raw-recovery] {safe_counts}", flush=True)
    return results


def capture_and_validate_agy_result(
    *,
    issue_id: str,
    attempt: int,
    result_path: Path,
    packet_path: Path,
    raw_output_db: Path,
    completed_work_db: Path,
    completed_work_evidence_dir: Path,
) -> dict:
    """Durably capture selected AGY output before strict packet validation."""
    path_type = type(Path())
    if type(issue_id) is not str:
        raise TypeError("issue_id must be an exact string")
    if type(attempt) is not int:
        raise TypeError("attempt must be an exact integer")
    for name, value in (
        ("result_path", result_path),
        ("packet_path", packet_path),
        ("raw_output_db", raw_output_db),
        ("completed_work_db", completed_work_db),
        ("completed_work_evidence_dir", completed_work_evidence_dir),
    ):
        if type(value) is not path_type:
            raise TypeError(f"{name} must be an exact platform Path")
    if not issue_id or attempt < 1:
        raise ValueError("issue_id and positive attempt are required")
    if (
        not result_path.is_absolute()
        or not packet_path.is_absolute()
        or not raw_output_db.is_absolute()
        or not completed_work_db.is_absolute()
        or not completed_work_evidence_dir.is_absolute()
        or packet_path.name != _AGY_PACKET_NAME
        or packet_path.parent != result_path.parent
        or ".." in packet_path.parts
    ):
        return {
            "boundary_state": "canonical_invalid",
            "boundary_reason": "unsafe_result_path",
            "raw_capture_succeeded": False,
            "completion_eligible": False,
        }

    try:
        os.lstat(packet_path)
    except FileNotFoundError:
        selected = result_path
        canonical = False
    except OSError:
        return {
            "boundary_state": "canonical_invalid",
            "boundary_reason": "unsafe_canonical_sidecar",
            "raw_capture_succeeded": False,
            "completion_eligible": False,
        }
    else:
        selected = packet_path
        canonical = True

    # Lazy imports preserve side-effect-free supervisor import.
    from prismatic.agent_raw_output_queue import (
        RawAgentOutputStore,
        max_raw_payload_bytes,
    )

    try:
        raw_bytes, oversized = _bounded_nofollow_read(
            selected, max_raw_payload_bytes()
        )
    except FileNotFoundError:
        return {
            "boundary_state": "result_missing",
            "boundary_reason": "selected_result_missing",
            "raw_capture_succeeded": False,
            "completion_eligible": False,
        }
    except (OSError, ValueError):
        return {
            "boundary_state": "canonical_invalid" if canonical else "result_missing",
            "boundary_reason": "selected_result_unsafe",
            "raw_capture_succeeded": False,
            "completion_eligible": False,
        }

    raw_text = raw_bytes.decode("utf-8", errors="replace")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    source_event_id = f"agy:{issue_id}:attempt:{attempt}:sha256:{digest}"
    try:
        row = RawAgentOutputStore(raw_output_db).persist(
            raw_text=raw_text,
            agent="agy",
            task_id=issue_id,
            source_event_id=source_event_id,
            raw_text_or_artifact_path=str(selected),
            expected_agent="agy",
        )
    except Exception:
        return {
            "boundary_state": "raw_capture_failed",
            "boundary_reason": "raw_queue_persist_failed",
            "raw_capture_succeeded": False,
            "completion_eligible": False,
        }

    if type(row.raw_output_id) is not str or not row.raw_output_id.strip():
        return {
            "boundary_state": "raw_capture_failed",
            "boundary_reason": "raw_queue_identity_missing",
            "raw_capture_succeeded": False,
            "completion_eligible": False,
        }

    common = {
        "raw_capture_succeeded": True,
        "raw_output_id": row.raw_output_id,
        "source_event_id": source_event_id,
        "selected_source": str(selected),
        "normalization_status": row.normalization_status,
        "canonical_packet_id": row.canonical_packet_id,
        "queue_rejection_reason": row.rejection_reason,
        "queue_repair_hint": row.repair_hint,
        "completion_eligible": False,
    }
    try:
        reconciliation = reconcile_agy_raw_output(
            raw_output_id=row.raw_output_id,
            raw_db_path=raw_output_db,
            completed_work_db_path=completed_work_db,
            completed_work_evidence_dir=completed_work_evidence_dir,
            lease_owner=f"immediate-{os.getpid()}",
        )
    except Exception:
        return {
            **common,
            "boundary_state": (
                "completed_work_persist_failed" if canonical else "legacy_unvalidated"
            ),
            "boundary_reason": (
                "completed_work_ledger_persist_failed"
                if canonical
                else "legacy_result_held_unvalidated"
            ),
            "completed_work_persisted": False,
            "delivery_status": "storage_failed",
        }
    if not canonical:
        return {
            **common,
            "boundary_state": "legacy_unvalidated",
            "boundary_reason": row.repair_hint or "legacy_result_held_unvalidated",
            "delivery_status": reconciliation.status,
        }
    if reconciliation.status != "succeeded":
        canonical_reason = {
            "issue_identity_mismatch": "active_issue_identity_mismatch",
            "completed_work_storage_failed": "completed_work_ledger_persist_failed",
        }.get(reconciliation.reason, "canonical_packet_invalid")
        if oversized:
            canonical_reason = "oversized_payload"
        state = (
            "completed_work_persist_failed"
            if reconciliation.status in {"retry_wait", "succeeded_unavailable"}
            else "canonical_invalid"
        )
        return {
            **common,
            "boundary_state": state,
            "boundary_reason": canonical_reason,
            "completed_work_persisted": False,
            "delivery_status": reconciliation.status,
        }
    return {
        **common,
        "boundary_state": "canonical_valid",
        "boundary_reason": "canonical_packet_accepted",
        "packet_issue_identifier": issue_id,
        "completed_work_persisted": True,
        "completed_work_id": reconciliation.completed_work_id,
        "completed_work_classification": reconciliation.classification,
        "completed_work_integration_classification": reconciliation.integration_classification,
        "completed_work_ingestion_marker": reconciliation.ingestion_marker,
        "completed_work_integration_marker": reconciliation.integration_marker,
        "completed_work_eligible_for_merge": reconciliation.eligible_for_merge,
        "delivery_status": reconciliation.status,
        "raw_output_recovery_marker": reconciliation.recovery_marker,
        "raw_output_reconciliation_gate_marker": reconciliation.reconciliation_gate_marker,
        "runtime_convergence_marker": reconciliation.runtime_marker,
        "completion_eligible": True,
    }


def publish_agent_completed(
    issue_id: str, payload: dict, *, topic: str = "agent.completed"
) -> None:
    """Publish a semantic result event to the durable SQLite bus.

    Bus path resolution (Jul 1 2026 — fixed split with consumer):
      1. PRISMATIC_BUS_DB env var if set (explicit override wins)
      2. Otherwise: $HOME/.prismatic/bus/event_log.sqlite (the canonical path
         consumers/factory_monitor/clear_stale_escalations read from)

      ⚠️ Do NOT use $PRISMATIC_HOME — it's set to the per-project work
      directory by the Hermes profile, which would point at the orphan
      project-bus at $PRISMATIC_HOME/.prismatic/bus/event_log.sqlite. Earlier fix (Jul 1)
      used PRISMATIC_HOME first, which silently routed writes to the orphan
      bus for 7+ hours. Use $HOME exclusively.
    """
    if os.environ.get("PRISMATIC_BUS_DB"):
        db_path = os.environ["PRISMATIC_BUS_DB"]
    else:
        home = os.path.expanduser("~")  # NEVER use $PRISMATIC_HOME
        db_path = os.path.join(home, ".prismatic", "bus", "event_log.sqlite")
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    retention_days = int(os.environ.get("PRISMATIC_BUS_RETENTION_DAYS", "14"))
    max_events = int(os.environ.get("PRISMATIC_BUS_MAX_EVENTS", "10000"))

    event_dict = {
        "type": topic,
        "source": "supervisor",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload
    }

    lane = payload.get("lane", "default")
    worker_id = payload.get("worker_id", 0)
    attempt = payload.get("attempt", 1)
    dedup_key = f"{topic}:{issue_id}:{lane}:{worker_id}:{attempt}"

    with _bus_sqlite_lock:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedup_key TEXT UNIQUE,
                    topic TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ts REAL NOT NULL,
                    processed INTEGER DEFAULT 0
                )
                """
            )
            try:
                conn.execute("ALTER TABLE events ADD COLUMN processed INTEGER DEFAULT 0")
            except Exception:
                pass
            conn.execute(
                "INSERT OR IGNORE INTO events (dedup_key, topic, payload_json, ts) VALUES (?, ?, ?, ?)",
                (
                    dedup_key,
                    topic,
                    json.dumps(event_dict, default=str),
                    time.time(),
                ),
            )
            conn.commit()

            conn.execute(
                "DELETE FROM events WHERE ts < ?",
                (time.time() - retention_days * 86400,),
            )
            conn.execute(
                "DELETE FROM events WHERE rowid IN (SELECT rowid FROM events ORDER BY rowid DESC LIMIT -1 OFFSET ?)",
                (max_events,),
            )
            conn.commit()
        finally:
            conn.close()


def publish_agent_recovered(issue_id: str, payload: dict) -> None:
    """Publish an agent.recovered event to the durable SQLite bus.

    Bus path resolution matches publish_agent_completed.
    """
    if os.environ.get("PRISMATIC_BUS_DB"):
        db_path = os.environ["PRISMATIC_BUS_DB"]
    else:
        home = os.path.expanduser("~")
        db_path = os.path.join(home, ".prismatic", "bus", "event_log.sqlite")
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    event_dict = {
        "type": "agent.recovered",
        "source": "supervisor",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload
    }

    lane = payload.get("lane", "default")
    recovery_count = payload.get("recovery_count", 0)
    dedup_key = f"agent.recovered:{issue_id}:{lane}:{recovery_count}:{time.time()}"

    with _bus_sqlite_lock:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedup_key TEXT UNIQUE,
                    topic TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ts REAL NOT NULL,
                    processed INTEGER DEFAULT 0
                )
                """
            )
            try:
                conn.execute("ALTER TABLE events ADD COLUMN processed INTEGER DEFAULT 0")
            except Exception:
                pass
            conn.execute(
                "INSERT OR IGNORE INTO events (dedup_key, topic, payload_json, ts) VALUES (?, ?, ?, ?)",
                (
                    dedup_key,
                    "agent.recovered",
                    json.dumps(event_dict, default=str),
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


# Add script directory to sys.path and import Linear helpers
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from linear_helpers import linear_update_issue, linear_comment
except ImportError:
    def linear_update_issue(identifier, state): return False
    def linear_comment(identifier, body): return False

# ── Configuration ─────────────────────────────────────────
# Sandbox storage policy (verified 2026-06-26):
#   PRIMARY: local fast SSD at /archive/agy_sandboxes (/dev/sdb, ~1.2TB).
#            User explicitly moved active work off NAS Jun 26 2026: NAS is archive,
#            not the active read/write area for AGY.
#   ARCHIVE: NAS-backed NFS mount at ~/mounts/synology-agentic-context
#            (Synology /volume1/agentic-context, 192.168.1.40). Use for completed
#            artifact archival / evidence bundles, not active sandbox I/O.
#   FALLBACK: local tmpfs at /tmp/agy_sandboxes (20G, fstab-mounted).
# NEVER rm -rf sandbox roots blindly (Jun 25 incident). Archive first.
FAST_SANDBOX_ROOT = Path(os.environ.get("FAST_SANDBOX_ROOT", "/archive/agy_sandboxes"))
NAS_SANDBOX_ARCHIVE_ROOT = Path(os.environ.get("NAS_SANDBOX_ARCHIVE_ROOT", str(Path.home() / "mounts" / "synology-agentic-context" / "agy_sandboxes")))
LOCAL_SANDBOX_ROOT = Path("/tmp/agy_sandboxes")


def _select_sandbox_root() -> Path:
    """Prefer local fast SSD; fall back to local tmpfs if unavailable.

    NAS is deliberately not selected as the active root anymore. It remains the
    archive/evidence target. Selection happens once per supervisor process.
    """
    try:
        FAST_SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        probe = FAST_SANDBOX_ROOT / ".supervisor_write_probe"
        probe.write_text("ok")
        probe.unlink()
        print(f"[sandbox-root] fast-ssd primary: {FAST_SANDBOX_ROOT}")
        return FAST_SANDBOX_ROOT
    except Exception as e:
        print(
            f"[sandbox-root] 🟡 WARN: fast SSD path {FAST_SANDBOX_ROOT} not writable "
            f"({type(e).__name__}: {e}); falling back to local tmpfs {LOCAL_SANDBOX_ROOT}"
        )
        LOCAL_SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        return LOCAL_SANDBOX_ROOT


SANDBOX_ROOT = _select_sandbox_root()
LOGS_ROOT = Path(os.environ.get("AGY_LOGS_ROOT", "/archive/agy_sandbox_logs"))
RESULTS_ROOT = Path(os.environ.get("AGY_RESULTS_ROOT", "/archive/agy_sandbox_results"))
AGY_BIN = os.environ.get("AGY_BIN", str(Path.home() / ".local" / "bin" / "agy"))
# Abandonment guard: sentinel that catches AGY exiting without writing RESULT.md
# Per Michael 2026-06-23: "Ned had a lot of complaints. He's maxing out his tool
# calls and then abandoning the task so he doesn't push partial fixes."
# Solution: wrap AGY with this sentinel — if no RESULT.md, write one + mark
# the Linear issue as agent:needs-human-review so the supervisor stops
# re-dispatching it.
# This supervisor can be launched under multiple Hermes profile HOME values. Use
# the service account's OS home, not the mutable HOME environment variable.
_SERVICE_ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
AGY_ABANDONMENT_GUARD = os.environ.get(
    "AGY_ABANDONMENT_GUARD",
    str(
        _SERVICE_ACCOUNT_HOME
        / ".hermes"
        / "profiles"
        / "orchestrator"
        / "scripts"
        / "agy_abandonment_guard.py"
    ),
)
AGENT_NEEDS_HUMAN_LABEL = "agent:needs-human-review"
# FIX 2026-06-24: Path("...li") was a typo. Use the actual token dir.
AGY_TOKEN_DIR = Path(os.environ.get("AGY_TOKEN_DIR", str(Path.home() / ".gemini" / "antigravity-cli")))

# Single-account organic scaling. Each Google account = 2-3 concurrent
# (Gemini backend cap per user). Default 3 hits the ceiling — proven to
# work with the event-driven pattern (1 timeout acceptable per cycle).
# The flag --random-concurrency varies this 1-3 per run to avoid
# predictable patterns.
MAX_CONCURRENT_DEFAULT = 3

# Randomized timing ranges (seconds). Each worker picks its own
# values per launch and per completion, so no two runs look the same.
LAUNCH_JITTER_RANGE = (5.0, 15.0)       # Before each AGY launch
COMPLETION_BACKOFF_RANGE = (8.0, 15.0)  # After each AGY completes
WORKER_STARTUP_STAGGER = (2.0, 8.0)     # Stagger worker threads at boot

# AGY subprocess timeout (24h)
PRINT_TIMEOUT = "24h0m0s"
DEFAULT_MODEL = "Gemini 3.5 Flash (Medium)"  # AGY CLI display label; -High burns daily quota overnight

# Watchdog: how often to poll Linear for new issues
# Jul 1 2026: demoted to 600s (10 min) safety net. The bus-subscriber thread
# is now the primary dispatch mechanism (~500ms event wake), so this only
# catches events that fell through the cracks.
LINEAR_POLL_INTERVAL = 600  # seconds

# AGY inactivity kill (Option B2, refined by second-opinion AGY 2026-06-26):
# Do NOT use a flat wall-clock ceiling. Kill only when the sandbox has had
# zero file modifications for this long. This bounds bg-subprocess hangs
# (e.g. "find &" / "pytest &" wait loops) without killing productive slow work.
AGY_INACTIVITY_KILL_SEC = int(os.environ.get("AGY_INACTIVITY_KILL_SEC", "120"))
AGY_DONE_WAIT_SEC = int(os.environ.get("AGY_DONE_WAIT_SEC", "600"))
MIN_TMP_FREE_GB = float(os.environ.get("AGY_MIN_TMP_FREE_GB", "10"))
MIN_ARCHIVE_FREE_GB = float(os.environ.get("AGY_MIN_ARCHIVE_FREE_GB", "50"))
CRON_JOBS_PATH = Path(os.environ.get("CRON_JOBS_PATH", str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "cron" / "jobs.json")))
AUTO_RESUME_ALERT_ISSUES = [x.strip() for x in os.environ.get("AGY_ALERT_ISSUES", "GRO-2492,GRO-2551").split(",") if x.strip()]
CIRCUIT_BREAKER_FAILURE_LIMIT = int(os.environ.get("AGY_CIRCUIT_BREAKER_FAILURE_LIMIT", "2"))


def agy_cli_child_env() -> dict[str, str]:
    """Environment for AGY CLI subprocesses only.

    Supervisor state continues to use this process HOME/Path.home(); AGY child
    processes may need a profile-scoped HOME where `agy models` and auth work.
    """
    env = dict(os.environ)
    env["HOME"] = os.environ.get("AGY_CLI_HOME") or os.environ.get("HOME", str(Path.home()))
    return env


def _read_linear_api_key() -> str | None:
    key = os.environ.get("LINEAR_API_KEY")
    if key:
        return key
    key_path = Path(os.environ.get("HERMES_PROFILE_ENV", str(Path.home() / ".hermes" / "profiles" / "orchestrator" / ".env")))
    if key_path.exists():
        for line in key_path.read_text().split("\n"):
            if "LINEAR" in line and "KEY" in line and "=" in line:
                return line.split("=", 1)[1].strip().strip("\"'")
    fallback = Path.home() / ".linear_api_key"
    if fallback.exists():
        return fallback.read_text().strip()
    return None


def pause_supervisor_cron(reason: str) -> bool:
    """Flip the Hermes AGY supervisor cron to paused in the local jobs DB."""
    try:
        if not CRON_JOBS_PATH.exists():
            print(f"[auto-resume-gate] WARN: cron jobs DB missing: {CRON_JOBS_PATH}", flush=True)
            return False
        data = json.loads(CRON_JOBS_PATH.read_text())
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        changed = False
        now = datetime.now().isoformat()
        for job in jobs:
            name = (job.get("name") or "").lower()
            script = (job.get("script") or "").lower()
            if "sandbox supervisor" in name or "agy_sandbox_event_supervisor" in script:
                job["enabled"] = False
                job["state"] = "paused"
                job["paused_at"] = now
                job["paused_reason"] = reason
                changed = True
        if changed:
            CRON_JOBS_PATH.write_text(json.dumps(data, indent=2))
            print(f"[auto-resume-gate] cron paused: {reason}", flush=True)
        return changed
    except Exception as e:
        print(f"[auto-resume-gate] WARN: failed to pause cron: {e}", flush=True)
        return False


def post_gate_alert(reason: str, detail: str = "") -> None:
    body = "AUTO-RESUME GATE ALERT\n\n" + reason
    if detail:
        body += "\n\n" + detail
    for iid in AUTO_RESUME_ALERT_ISSUES:
        try:
            linear_comment(iid, body)
        except Exception as e:
            print(f"[auto-resume-gate] WARN: Linear alert failed for {iid}: {e}", flush=True)


def _free_gb(path: str) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)


def check_storage_gate(pause_on_failure: bool = False) -> bool:
    tmp_free = _free_gb("/tmp")
    archive_free = _free_gb("/archive")
    ok = tmp_free >= MIN_TMP_FREE_GB and archive_free >= MIN_ARCHIVE_FREE_GB
    print(
        f"[auto-resume-gate] storage: /tmp={tmp_free:.1f}GB free "
        f"(min {MIN_TMP_FREE_GB:.0f}), /archive={archive_free:.1f}GB free "
        f"(min {MIN_ARCHIVE_FREE_GB:.0f})",
        flush=True,
    )
    if not ok:
        reason = "Storage gate failed: /tmp or /archive free space below threshold."
        detail = f"/tmp={tmp_free:.1f}GB free, /archive={archive_free:.1f}GB free"
        if pause_on_failure:
            pause_supervisor_cron(reason)
        post_gate_alert(reason, detail)
    return ok


def preflight_linear_api() -> tuple[bool, str]:
    key = _read_linear_api_key()
    if not key:
        return False, "LINEAR_API_KEY missing"
    payload = {"query": "query { viewer { id name } }"}
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps(payload).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        if data.get("errors"):
            return False, "Linear GraphQL errors: " + str(data["errors"][:1])
        return True, "Linear API OK"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return False, "Linear API 429 rate limited"
        return False, f"Linear API HTTP {e.code}"
    except Exception as e:
        return False, f"Linear API probe failed: {type(e).__name__}: {e}"


def preflight_agy_backend(model: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [AGY_BIN, "--print", "Reply with exactly: OK", "--print-timeout", "30s", "--model", model],
            capture_output=True,
            text=True,
            timeout=45,
            env=agy_cli_child_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "AGY backend probe timed out"
    except Exception as e:
        return False, f"AGY backend probe failed: {type(e).__name__}: {e}"
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    lower = combined.lower()
    if "429" in combined or "rate limit" in lower:
        return False, "AGY backend probe hit 429/rate limit"
    if "timed out waiting for response" in lower or proc.returncode != 0:
        return False, f"AGY backend probe failed rc={proc.returncode}: {combined[-300:]}"
    return True, "AGY backend OK"


def run_auto_resume_gates(model: str, cron_mode: bool, skip_agy_probe: bool = False) -> bool:
    if not check_storage_gate(pause_on_failure=cron_mode):
        return False
    ok, msg = preflight_linear_api()
    print(f"[auto-resume-gate] {msg}", flush=True)
    if not ok:
        if cron_mode:
            pause_supervisor_cron(msg)
        post_gate_alert("API preflight failed", msg)
        return False
    if not skip_agy_probe:
        ok, msg = preflight_agy_backend(model)
        print(f"[auto-resume-gate] {msg}", flush=True)
        if not ok:
            if cron_mode:
                pause_supervisor_cron(msg)
            post_gate_alert("API preflight failed", msg)
            return False
    return True

# Workdir resolver (matches the dispatcher's WORKSPACE_RULES)
WORKSPACE_RULES = {
    "darius": f"{os.environ.get('PRISMATIC_HOME', str(Path.home() / 'work'))}/darius-star",
    "active-oahu": f"{os.environ.get('PRISMATIC_HOME', str(Path.home() / 'work'))}/active-oahu-static",
    "prismatic": f"{os.environ.get('PRISMATIC_HOME', str(Path.home() / 'work'))}/prismatic-engine",
    "agentic": f"{os.environ.get('PRISMATIC_HOME', str(Path.home() / 'work'))}/agentic-swarm-ops",
    "hd-platform": f"{os.environ.get('PRISMATIC_HOME', str(Path.home() / 'work'))}/hd-platform",
}


def resolve_workdir(workdir_arg: str) -> Path:
    p = Path(workdir_arg)
    if p.is_absolute() and p.exists():
        return p
    if workdir_arg in WORKSPACE_RULES:
        return Path(WORKSPACE_RULES[workdir_arg])
    for key, path in WORKSPACE_RULES.items():
        if workdir_arg.startswith(key):
            return Path(path)
    raise ValueError(f"Cannot resolve workdir: {workdir_arg}")


# ── Token Pool (kept from original, for future multi-account support) ──
class TokenPool:
    def __init__(self):
        self.tokens = []
        self._scan_tokens()

    def _scan_tokens(self):
        if not AGY_TOKEN_DIR.exists():
            return
        primary = AGY_TOKEN_DIR / "antigravity-oauth-token"
        if primary.exists():
            self.tokens.append({"name": "primary", "path": str(primary)})
        for token_dir in sorted(AGY_TOKEN_DIR.parent.iterdir()):
            if not token_dir.is_dir():
                continue
            if not token_dir.name.startswith("antigravity-cli-"):
                continue
            if token_dir.name.endswith(".bak"):
                continue
            token_file = token_dir / "antigravity-oauth-token"
            if token_file.exists():
                account_name = token_dir.name[len("antigravity-cli-"):]
                self.tokens.append({"name": account_name, "path": str(token_file)})

    def acquire(self) -> dict | None:
        if not self.tokens:
            return None
        token = self.tokens.pop(0)
        self.tokens.append(token)
        return token

    def stats(self) -> dict:
        return {
            "pool_size": len(self.tokens),
            "tokens": [t["name"] for t in self.tokens],
        }


# ── Sandbox creation (kept from original) ──
def apply_repair_seed_manifest(
    sandbox: Path,
    manifest_path: Path,
    expected_manifest_sha256: str,
) -> list[str]:
    """Copy a hash-bound repair snapshot into a newly created sandbox.

    The manifest is explicit recovery input, never implicit dirty-state reuse.
    All entries are validated before any destination is written.
    """
    manifest_path = manifest_path.expanduser()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RuntimeError(f"repair seed manifest is not a regular file: {manifest_path}")
    expected_manifest_sha256 = expected_manifest_sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise RuntimeError("repair seed manifest SHA-256 must be 64 lowercase hex characters")
    manifest_bytes = manifest_path.read_bytes()
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise RuntimeError(
            "repair seed manifest hash mismatch: "
            f"expected {expected_manifest_sha256}, got {actual_manifest_sha256}"
        )

    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"repair seed manifest is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise RuntimeError("repair seed manifest must be an object with version=1")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("repair seed manifest files must be a non-empty list")
    if len(entries) > 100:
        raise RuntimeError("repair seed manifest exceeds 100 files")

    sandbox_root = sandbox.resolve(strict=True)
    staged: list[tuple[Path, bytes, str]] = []
    destination_targets: set[Path] = set()
    total_bytes = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RuntimeError(f"repair seed entry {index} must be an object")
        source_text = entry.get("source")
        destination_text = entry.get("destination")
        expected_file_sha256 = str(entry.get("sha256") or "").lower()
        if not isinstance(source_text, str) or not Path(source_text).is_absolute():
            raise RuntimeError(f"repair seed entry {index} source must be absolute")
        source = Path(source_text)
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"repair seed entry {index} source is not a regular file")
        if not isinstance(destination_text, str):
            raise RuntimeError(f"repair seed entry {index} destination must be a string")
        destination = Path(destination_text)
        if destination.is_absolute() or not destination.parts or ".." in destination.parts:
            raise RuntimeError(f"repair seed entry {index} destination is unsafe")
        if destination.parts[0] == ".git" or destination.name in {
            "AGY_TASK.md",
            "STARTED.md",
            "RESULT.md",
            "AGY_RESULT_PACKET.json",
            "DONE.md",
        }:
            raise RuntimeError(f"repair seed entry {index} targets a protected control path")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_file_sha256):
            raise RuntimeError(f"repair seed entry {index} SHA-256 is invalid")
        payload = source.read_bytes()
        total_bytes += len(payload)
        if total_bytes > 50 * 1024 * 1024:
            raise RuntimeError("repair seed manifest exceeds 50 MiB total")
        actual_file_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_file_sha256 != expected_file_sha256:
            raise RuntimeError(
                f"repair seed entry {index} hash mismatch: "
                f"expected {expected_file_sha256}, got {actual_file_sha256}"
            )
        target = (sandbox_root / destination).resolve(strict=False)
        if not target.is_relative_to(sandbox_root):
            raise RuntimeError(f"repair seed entry {index} escapes sandbox")
        if target in destination_targets:
            raise RuntimeError(f"repair seed entry {index} destination is duplicated")
        destination_targets.add(target)
        staged.append((target, payload, destination_text))

    for target, payload, _ in staged:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.repair-seed.tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
    return [destination for _, _, destination in staged]


def create_sandbox(issue_id: str, source: Path) -> Path:
    sandbox = SANDBOX_ROOT / issue_id
    if sandbox.is_symlink():
        sandbox.unlink()
    if sandbox.exists():
        if sandbox.is_dir() and not sandbox.is_symlink():
            try:
                shutil.rmtree(sandbox)
            except OSError as e:
                print(f"  [{issue_id}] partial cleanup ({e}); proceeding with new dir")
                sandbox = SANDBOX_ROOT / f"{issue_id}-{int(time.time())}"
        else:
            sandbox.unlink()
    sandbox.mkdir(parents=True, exist_ok=True)

    cache_name = source.name
    warm_cache = Path(os.environ.get("AGY_WARM_CACHE_ROOT", str(Path.home() / "work" / "agy_warm_cache"))) / cache_name
    if warm_cache.exists() and warm_cache.is_dir():
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--no-local", str(warm_cache), str(sandbox)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print(f"  [{issue_id}] warm-cache clone from {cache_name}", flush=True)
            return sandbox
        print(f"  [{issue_id}] warm-cache clone failed, falling back...", flush=True)

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--no-local", str(source), str(sandbox)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print(f"  [{issue_id}] shallow-cloned from {source}", flush=True)
            return sandbox
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    print(f"  [{issue_id}] git clone failed, falling back to rsync...", flush=True)
    result = subprocess.run(
        ["rsync", "-a", "--exclude", ".git", "--exclude", "node_modules",
         f"{source}/", f"{sandbox}/"],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"rsync failed: {result.stderr}")

    subprocess.run(["git", "init"], cwd=sandbox, capture_output=True, timeout=10)
    subprocess.run(["git", "add", "-A"], cwd=sandbox, capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-m", f"initial sandbox for {issue_id}"],
                   cwd=sandbox, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "Sandbox",
                        "GIT_AUTHOR_EMAIL": "sandbox@local",
                        "GIT_COMMITTER_NAME": "Sandbox",
                        "GIT_COMMITTER_EMAIL": "sandbox@local"},
                   timeout=30)
    print(f"  [{issue_id}] rsync'd + git init'd", flush=True)
    return sandbox


def write_task_file(issue_id: str, task_content: str) -> Path:
    task_path = SANDBOX_ROOT / issue_id / "AGY_TASK.md"
    task_path.write_text(task_content)
    return task_path


def heartbeat_watcher(issue_id, sandbox, proc_pid,
                      result_event, stagnation_warn_event, stagnation_kill_event,
                      stagnation_warn_sec=180,
                      inactivity_kill_sec=None,
                      log_path=None):
    """Sidecar: watches for RESULT.md and sandbox file activity while AGY runs.

    Note: `inactivity_kill_sec` has no default — must be passed explicitly so the
    current value of AGY_INACTIVITY_KILL_SEC (which may be updated by the cron-mode
    gate at runtime) is honored. If None, falls back to the module-level value.

    v6 fix (2026-06-29): also watch the AGY log file mtime (stdout/stderr
    stream). AGY in --print mode writes reasoning/tool-call output to the log
    even when sandbox files are unchanged (read-then-write phases). Without
    this, a 10min read phase gets killed at the old 240s ceiling. Activity is
    now: sandbox file mtime OR log file mtime updated in last N seconds.

    v5 fix (2026-06-26): inactivity-based kill. If sandbox files show zero
    modifications for AGY_INACTIVITY_KILL_SEC, signal the main loop to
    terminate AGY and let the abandonment guard write a diagnostic
    RESULT.md. This is NOT a wall-clock task timeout; productive slow tasks
    survive as long as they keep touching files.

    v4 fix (2026-06-23): Early-status reporting — within 2 minutes of AGY launch
    we now print a clear "healthy" or "stuck" verdict so the operator doesn't
    wait 5+ min staring at silence. Also dynamic check interval: every 30s in
    first 5 min (early signal), every 60s after.
    """
    if inactivity_kill_sec is None:
        inactivity_kill_sec = globals().get("AGY_INACTIVITY_KILL_SEC", 120)
    result_path = sandbox / "RESULT.md"
    stagnation_since = time.time()
    last_mtime = 0
    started_at = time.time()
    early_signal_emitted = False
    result_seen_emitted = False
    while True:
        # Dynamic check interval: 30s early (first 5 min), 60s after
        interval = 30 if (time.time() - started_at) < 300 else 60
        time.sleep(interval)
        # 1. RESULT.md appeared? This is progress, NOT completion. AGY must
        # still run self-review and emit DONE or exit naturally. Do not signal
        # the main loop to terminate here; that race killed self-review.
        if result_path.exists() and not result_seen_emitted:
            result_seen_emitted = True
            print(
                f"  [{issue_id}] 🟢 RESULT.md detected — waiting for self-review/DONE or natural exit",
                flush=True,
            )
        # 2. Process still alive?
        try:
            os.kill(proc_pid, 0)
        except ProcessLookupError:
            return  # AGY exited naturally, main thread will handle
        except OSError:
            return
        # 3. Sandbox activity OR log activity? (any file modified = progress)
        # v6: AGY in --print mode writes reasoning/tool-call output to stdout.
        # Watching log file mtime catches read-heavy phases that don't touch
        # the sandbox but ARE actively making progress.
        try:
            sandbox_mtime = max(
                (f.stat().st_mtime for f in sandbox.rglob("*") if f.is_file()),
                default=0,
            )
        except (OSError, PermissionError):
            sandbox_mtime = last_mtime
        log_mtime = 0
        if log_path is not None:
            try:
                log_mtime = log_path.stat().st_mtime
            except (OSError, FileNotFoundError):
                log_mtime = 0
        current_mtime = max(sandbox_mtime, log_mtime)
        if current_mtime > last_mtime:
            last_mtime = current_mtime
            stagnation_since = time.time()

        # Early signal: at 2 minutes, print a verdict
        elapsed = time.time() - started_at
        if not early_signal_emitted and elapsed >= 120:
            early_signal_emitted = True
            if os.path.exists(result_path):
                print(f"  [{issue_id}] 🟢 2-min signal: AGY wrote RESULT.md — done!", flush=True)
            elif current_mtime > last_mtime - 1:
                # File modified within last second → very recent activity
                print(f"  [{issue_id}] 🟢 2-min signal: healthy — sandbox/log activity recent", flush=True)
            else:
                age = time.time() - current_mtime if current_mtime else 99999
                if age < 60:
                    print(f"  [{issue_id}] 🟢 2-min signal: healthy — last activity {int(age)}s ago", flush=True)
                elif age < 300:
                    print(f"  [{issue_id}] 🟡 2-min signal: slow (reading/thinking) — last activity {int(age)}s ago", flush=True)
                else:
                    # v6: at >5min no activity, also check if AGY process is still alive
                    # and using CPU. If alive but no I/O, it may be in a long reasoning loop
                    # (Gemini / Claude can take 3-5min on a hard reasoning pass without output).
                    try:
                        import resource  # not used yet, placeholder for future CPU check
                        proc_alive = True
                        try:
                            os.kill(proc_pid, 0)
                            proc_alive = True
                        except (OSError, ProcessLookupError):
                            proc_alive = False
                        if proc_alive:
                            print(f"  [{issue_id}] 🟠 2-min signal: AGY process alive but no I/O for {int(age)}s — may be long-reasoning; will warn at stagnation", flush=True)
                        else:
                            print(f"  [{issue_id}] 🔴 2-min signal: AGY process dead, no activity for {int(age)}s", flush=True)
                    except Exception:
                        print(f"  [{issue_id}] 🔴 2-min signal: stuck — no activity for {int(age)}s, will warn at 5 min", flush=True)

        # Stagnation warning + inactivity kill. The warning is informational;
        # the kill event is acted on by the main run loop so process cleanup
        # is centralized there.
        stagnant_for = time.time() - stagnation_since
        if stagnant_for > stagnation_warn_sec:
            stagnation_warn_event.set()
        if stagnant_for > inactivity_kill_sec:
            print(
                f"  [{issue_id}] 🛑 INACTIVITY_KILL: no sandbox file changes for "
                f"{int(stagnant_for)}s (limit {int(inactivity_kill_sec)}s)",
                flush=True,
            )
            stagnation_kill_event.set()
            return


# Module-level registry of currently-running AGY subprocesses so shutdown()
# can guarantee no orphan agy-bin processes survive a circuit trip.
# Populated in run_agy_session, cleared on natural exit or terminate.
_ACTIVE_PROCS: dict[str, "subprocess.Popen"] = {}
_ACTIVE_PROCS_LOCK = threading.Lock()

def _register_proc(issue_id: str, proc: "subprocess.Popen") -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS[issue_id] = proc

def _unregister_proc(issue_id: str) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.pop(issue_id, None)

def terminate_all_active_procs(timeout: float = 5.0) -> int:
    """Terminate every active agy-bin subprocess. Returns count killed.
    Called from EventDrivenSupervisor.shutdown() to prevent orphans.
    """
    killed = 0
    with _ACTIVE_PROCS_LOCK:
        snapshot = list(_ACTIVE_PROCS.items())
    for issue_id, proc in snapshot:
        try:
            if proc.poll() is None:  # still running
                print(f"  [shutdown] terminating active agy-bin for {issue_id} (pid {proc.pid})", flush=True)
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    print(f"  [shutdown] agy-bin for {issue_id} did not exit, sending SIGKILL", flush=True)
                    proc.kill()
                    proc.wait()
                killed += 1
        except Exception as e:
            print(f"  [shutdown] failed to terminate agy-bin for {issue_id}: {e}", flush=True)
        finally:
            _unregister_proc(issue_id)
    return killed

# ── AGY session runner (kept from original) ──
def build_agy_command(sandbox: Path, prompt: str, model: str) -> list[str]:
    """Build the filesystem-scoped AGY command without side effects."""
    if type(sandbox) is not type(Path()):
        raise TypeError("sandbox must be an exact platform Path")
    if type(prompt) is not str:
        raise TypeError("prompt must be an exact string")
    if type(model) is not str:
        raise TypeError("model must be an exact string")
    return [
        AGY_BIN,
        "--dir",
        str(sandbox),
        "--print",
        prompt,
        "--dangerously-skip-permissions",
        "--print-timeout",
        PRINT_TIMEOUT,
        "--sandbox",
        "--model",
        model,
    ]


def _start_agy_process(cmd: list[str], logf, sandbox: Path):
    """Start AGY with isolated child HOME and no writable stdin transport."""
    return subprocess.Popen(
        cmd,
        stdout=logf,
        stderr=subprocess.STDOUT,
        stdin=None,
        cwd=str(sandbox),
        env=agy_cli_child_env(),
    )


def run_agy_session(issue_id: str, sandbox: Path, task_path: Path, log_path: Path,
                    model: str, token_pool: TokenPool = None,
                    jitter_range: tuple = LAUNCH_JITTER_RANGE,
                    token: str = None,
                    lane: str = "default") -> dict:
    # Per-launch random jitter (independent per session)
    jitter = random.uniform(*jitter_range)
    print(f"  [{issue_id}] jitter: {jitter:.1f}s before launch", flush=True)
    time.sleep(jitter)

    prompt = (
        f"Read {task_path}. Follow ALL instructions exactly. "
        f"Work on issue {issue_id} in this directory ({sandbox}). "
        f"\n\n**MANDATORY FINISH PROTOCOL:**\n"
        f"1. Before saying 'DONE', you MUST write a complete summary to `{sandbox}/RESULT.md` "
        f"(use the Write tool — do NOT just print to stdout).\n"
        f"2. The RESULT.md must include: what you did, files changed, test results (if any), "
        f"commit hashes, and any follow-ups.\n"
        f"   You MUST also write pure JSON to `{sandbox}/AGY_RESULT_PACKET.json` with marker "
        f"`AGY_TASK_RESULT_PACKET_OK`, agent=`agy`, issue_identifier=`{issue_id}`, relative "
        f"result artifact paths (prefer `RESULT.md`), no secrets/generated/cache paths, truthful "
        f"verification command/result/log/type, and no fabricated source/base/PR values.\n"
        f"3. After RESULT.md is saved, you MUST run the self-review protocol:\n"
        f"   `python3 ~/.hermes/profiles/orchestrator/scripts/agy_self_review.py {issue_id}`\n"
        f"   This is NON-OPTIONAL. It posts a Self-Review comment to Linear and "
        f"   transitions the issue to `agent:peer-review` for independent verification.\n"
        f"4. If self-review fails (no RESULT.md, missing artifacts), DO NOT mark the work "
        f"   complete. Fix the issue and re-run self-review.\n"
        f"5. Only AFTER the self-review comment is posted and the issue is labeled "
                f"   `agent:peer-review`, output `DONE: {issue_id} <one-line summary>` as the LAST line.\n"
                f"6. If you cannot save RESULT.md (e.g. permission error), output "
                f"`ERROR: {issue_id} <reason>` instead — do NOT say DONE without the file.\n"
                f"\n**OTHER INSTRUCTIONS:**\n"
                f"- Do NOT ask for clarification — read the task file and act.\n"
                f"- Use git for all commits (branch: feature/{issue_id.lower()}).\n"
                f"- Follow AGY_TASK.md exactly. If it says output-driven/read-only, do NOT run "
                f"tests, builds, Lighthouse, package installs, git fetch/clone, or background tasks. "
                f"If required artifacts are missing, write a MISSING ARTIFACTS section in RESULT.md.\n"
                f"\n**SEARCH BOUNDARY (Jun 26 2026 fix):**\n"
                f"- Your working directory is `{sandbox}`. Search ONLY inside it.\n"
                f"- DO NOT search these paths — they are slow network mounts that will hang your tool loop:\n"
                f"  - $HOME/mounts/*  (Synology NFS — random I/O ~1000x slower than /archive)\n"
                f"  - $HOME/.gemini/* (agy config cache, irrelevant to project work)\n"
                f"- If a search returns 0 hits inside the sandbox, STOP searching and write "
                f"a MISSING ARTIFACTS section in RESULT.md instead of widening the search.\n"
                f"\n**TOOL-LOOP GUARD (Jun 26 2026 fix):**\n"
                f"- If you have called the same read/search/find tool more than 5 times without "
                f"making progress, STOP and write a TOOL LOOP DETECTED section to RESULT.md with "
                f"what you tried and what you suspect.\n"
                f"- Prefer 1-3 targeted tool calls over 20+ speculative searches.\n"
            )

    cmd = build_agy_command(sandbox, prompt, model)

    token_name = token
    if not token_name and token_pool:
        token_info = token_pool.acquire()
        if token_info:
            token_name = token_info["name"]

    if token_name:
        print(f"  [{issue_id}] token: {token_name}", flush=True)

    print(f"  [{issue_id}] launching AGY in sandbox {sandbox.name}", flush=True)
    started_at = time.time()

    result_event = threading.Event()
    stagnation_warn_event = threading.Event()
    stagnation_kill_event = threading.Event()
    # Initialized before subprocess/shutdown paths; semantic assessment overwrites it.
    has_done = False

    try:
        # Pre-cleanup: stale proof must never survive into a new attempt.
        result_path = sandbox / "RESULT.md"
        for removed_name in remove_stale_agy_result_outputs(sandbox):
            print(
                f"  [{issue_id}] deleted prior {removed_name} from sandbox before launch",
                flush=True,
            )

        # Pre-cleanup: delete prior STARTED.md files to prevent stale acknowledgements
        started_md_tmp = Path("/tmp/agy_sandboxes") / issue_id / "STARTED.md"
        started_md_sandbox = sandbox / "STARTED.md"
        for p in (started_md_tmp, started_md_sandbox):
            if p.exists() or p.is_symlink():
                try:
                    if p.is_dir() and not p.is_symlink():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                    print(f"  [{issue_id}] deleted prior {p.name} before launch", flush=True)
                except OSError as e:
                    print(f"  [{issue_id}] failed to delete prior {p.name}: {e}", flush=True)

        # buffering=1 = line-buffered so the heartbeat watcher sees mtime updates
        # in real time (otherwise 4KB-buffered writes can sit idle for minutes while
        # AGY actively reasons — we'd incorrectly flag as stagnant).
        logf = open(log_path, "w", buffering=1)
        try:
            proc = _start_agy_process(cmd, logf, sandbox)
            _register_proc(issue_id, proc)
        except Exception as e:
            logf.close()
            raise e

        # Start the heartbeat watcher (v6: pass log_path so it can detect
        # read-heavy phases that don't touch the sandbox file tree)
        watcher = threading.Thread(
            target=heartbeat_watcher,
            args=(issue_id, sandbox, proc.pid, result_event, stagnation_warn_event, stagnation_kill_event),
            kwargs={"log_path": log_path},
            name=f"heartbeat-{issue_id}",
            daemon=True
        )
        watcher.start()

        # Main wait loop
        exit_code = None
        aborted_cleanly = False
        killed_for_inactivity = False

        recovery_count = 0
        last_log_pos = 0
        permission_denied_seen = False
        permission_denied_time = 0.0

        done_found = False
        error_found = False
        error_reason = ""
        done_summary = ""
        result_appeared_at = None

        started_time = time.time()
        started_md_checked = False
        has_start_timeout = False

        try:
            while True:
                # Check if process has terminated
                ret = proc.poll()
                if ret is not None:
                    exit_code = ret
                    break

                # Monitor STARTED.md within 30 seconds of launch
                if not started_md_checked:
                    elapsed_start = time.time() - started_time
                    started_md_tmp = Path("/tmp/agy_sandboxes") / issue_id / "STARTED.md"
                    started_md_sandbox = sandbox / "STARTED.md"
                    if started_md_tmp.exists() or started_md_sandbox.exists():
                        started_md_checked = True
                        print(f"  [{issue_id}] 🟢 STARTED.md detected — task picked up", flush=True)
                    elif elapsed_start > 30.0:
                        print(f"  [{issue_id}] 🛑 START_TIMEOUT: STARTED.md not written within 30s. Killing AGY.", flush=True)
                        has_start_timeout = True
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        except Exception:
                            pass
                        exit_code = proc.returncode if proc.returncode is not None else -99
                        break

                # Safety net: kill if RESULT.md exists for AGY_DONE_WAIT_SEC but no DONE printed
                if result_path.exists():
                    if result_appeared_at is None:
                        result_appeared_at = time.time()
                        print(f"  [{issue_id}] 🟢 RESULT.md appeared — safety-net timer started (limit {AGY_DONE_WAIT_SEC}s)", flush=True)
                    elif time.time() - result_appeared_at > AGY_DONE_WAIT_SEC:
                        print(f"  [{issue_id}] 🛑 DONE_WAIT_TIMEOUT: RESULT.md exists but no DONE in stdout for {int(time.time() - result_appeared_at)}s (limit {AGY_DONE_WAIT_SEC}s). Killing AGY.", flush=True)
                        try:
                            proc.terminate()
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        except Exception:
                            pass
                        exit_code = proc.returncode if proc.returncode is not None else -15
                        break

                # RESULT.md is progress, not completion. Do not terminate AGY here;
                # AGY must run self-review and emit DONE or exit naturally. The
                # result_event is retained only for backward compatibility with
                # older watcher signatures and should not be used as a completion
                # signal.
                # Check inactivity kill from heartbeat_watcher. This is a sandbox
                # inactivity ceiling, not a flat wall-clock timeout. If AGY is
                # alive but no files are changing for AGY_INACTIVITY_KILL_SEC,
                # terminate it and let the abandonment guard create RESULT.md.
                if stagnation_kill_event.is_set():
                    killed_for_inactivity = True
                    try:
                        proc.terminate()
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    except Exception:
                        pass
                    exit_code = proc.returncode if proc.returncode is not None else -15
                    break

                # Check stagnation_warn_event
                if stagnation_warn_event.is_set():
                    print(
                        f"  [{issue_id}] ⚠️ WARNING: {AGY_INACTIVITY_KILL_SEC}s inactivity guard armed "
                        f"(no sandbox growth; will kill if stagnant)",
                        flush=True,
                    )
                    stagnation_warn_event.clear()

                # Scan log_path for permission prompts or denied states
                prompt_detected = False
                reason = ""
                new_content = ""
                if log_path.exists():
                    try:
                        with log_path.open("r", errors="replace") as lf:
                            lf.seek(last_log_pos)
                            new_content = lf.read()
                            last_log_pos = lf.tell()
                    except Exception as e:
                        print(f"  [{issue_id}] Error reading log file: {e}", flush=True)

                if new_content:
                    if "are you sure you want to" in new_content.lower():
                        prompt_detected = True
                        reason = "Are you sure you want to... prompt"
                    elif "[y/n]" in new_content.lower() or "[y/N]" in new_content:
                        prompt_detected = True
                        reason = "[y/N] prompt"
                    elif "press enter to continue" in new_content.lower():
                        prompt_detected = True
                        reason = "Press Enter to continue prompt"

                    if "permission denied" in new_content.lower():
                        permission_denied_seen = True
                        permission_denied_time = time.time()
                    elif permission_denied_seen:
                        # Log grew/progress was made after permission denied, reset
                        permission_denied_seen = False

                    # Check for goal-state signals in new stdout content
                    for line in new_content.splitlines():
                        line = line.strip()
                        done_match = re.match(rf"^DONE:\s*<?{re.escape(issue_id)}>?\s+(.+)$", line)
                        error_match = re.match(rf"^ERROR:\s*<?{re.escape(issue_id)}>?\s+(.+)$", line)
                        if done_match:
                            done_found = True
                            done_summary = done_match.group(1)
                            print(f"  [{issue_id}] 🟢 Goal state detected: DONE ({done_summary})", flush=True)
                            break
                        elif error_match:
                            error_found = True
                            error_reason = error_match.group(1)
                            print(f"  [{issue_id}] 🔴 Goal state detected: ERROR ({error_reason})", flush=True)
                            break

                    if done_found or error_found:
                        print(f"  [{issue_id}] Terminating AGY early (sigterm) due to goal-state match", flush=True)
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        except Exception:
                            pass
                        exit_code = proc.returncode if proc.returncode is not None else -15
                        break

                if permission_denied_seen and (time.time() - permission_denied_time > 15):
                    prompt_detected = True
                    reason = "Permission denied followed by no recovery action"
                    permission_denied_seen = False

                if prompt_detected:
                    recovery_count += 1
                    action_type = "pipe_stdin" if proc.stdin is not None else "relaunch"
                    print(f"  [{issue_id}] ⚠️ Detect waiting for permission state: {reason} (count {recovery_count}/3, action: {action_type})", flush=True)

                    # Log to bus as agent.recovered event
                    payload = {
                        "issue_id": issue_id,
                        "lane": lane,
                        "model": model,
                        "reason": reason,
                        "recovery_count": recovery_count,
                        "action": action_type
                    }
                    try:
                        publish_agent_recovered(issue_id, payload)
                    except Exception as p_err:
                        print(f"  [{issue_id}] ⚠️ agent.recovered publish failed: {p_err}", flush=True)

                    # Post Linear comment
                    try:
                        linear_comment(
                            issue_id,
                            f"⚠️ **Auto-recovery triggered** (count {recovery_count}/3): {reason}. Action: {action_type}."
                        )
                    except Exception as lc_err:
                        print(f"  [{issue_id}] Linear comment failed: {lc_err}", flush=True)

                    if recovery_count > 3:
                        print(f"  [{issue_id}] 🛑 Maximum recovery count (>3) exceeded. Marking task as failed.", flush=True)
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        exit_code = -99
                        break

                    if proc.stdin is not None:
                        # pipe y or yes into AGY's stdin
                        try:
                            proc.stdin.write(b"yes\n")
                            proc.stdin.flush()
                            print(f"  [{issue_id}] Piped 'yes' to stdin", flush=True)
                        except Exception as stdin_err:
                            print(f"  [{issue_id}] Failed to pipe to stdin: {stdin_err}", flush=True)
                    else:
                        # SIGTERM + relaunch with the identical bounded command.
                        print(f"  [{issue_id}] Stdin not available. Sending SIGTERM to relaunch...", flush=True)
                        try:
                            proc.terminate()
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            try:
                                proc.kill()
                                proc.wait()
                            except Exception:
                                pass
                        except Exception:
                            pass

                        print(f"  [{issue_id}] Relaunching with cmd: {cmd}", flush=True)
                        logf.close()
                        logf = open(log_path, "a", buffering=1)
                        try:
                            proc = _start_agy_process(cmd, logf, sandbox)
                            _register_proc(issue_id, proc)
                        except Exception as relaunch_err:
                            print(f"  [{issue_id}] Relaunch failed: {relaunch_err}", flush=True)
                            logf.close()
                            raise relaunch_err

                        # Spawn new heartbeat watcher for new pid
                        watcher = threading.Thread(
                            target=heartbeat_watcher,
                            args=(issue_id, sandbox, proc.pid, result_event, stagnation_warn_event, stagnation_kill_event),
                            kwargs={"log_path": log_path},
                            name=f"heartbeat-{issue_id}",
                            daemon=True
                        )
                        watcher.start()

                        last_log_pos = log_path.stat().st_size if log_path.exists() else 0
                        permission_denied_seen = False
                        continue

                time.sleep(1)
        finally:
            # Always unregister so shutdown() doesn't try to terminate a dead proc.
            _unregister_proc(issue_id)

        logf.close()
        elapsed = time.time() - started_at

        log_content = log_path.read_text() if log_path.exists() else ""
        result_path = sandbox / "RESULT.md"
        has_result = result_path.exists()
        lower_log = log_content.lower()
        has_self_review = (
            "self-review passed" in lower_log
            or "self-review —" in log_content
            or "self-review protocol" in lower_log
            or "self-review script" in lower_log
            or "self-review complete" in lower_log
            or "self-review checks passed" in lower_log
            or "ran the self-review script" in lower_log
            or "successfully ran the self-review" in lower_log
            or "agy_self_review.py" in lower_log
            or "agent:peer-review" in lower_log
        )
        
        # Check Linear for peer-review label (success confirmed)
        has_peer_review_label = False
        try:
            issue_node = fetch_single_linear_issue(issue_id)
            if issue_node:
                labels = issue_labels(issue_node)
                if "agent:peer-review" in labels:
                    has_peer_review_label = True
        except Exception as e:
            print(f"  [{issue_id}] Error fetching issue from Linear: {e}", flush=True)

        has_success_confirmed = has_result and has_peer_review_label
        # RESULT.md alone is progress, not final completion. Completion markers
        # and peer-review labels remain transport signals until packet semantics
        # pass fail-closed assessment.
        completion_signal = (
            ("DONE:" in log_content and has_self_review)
            or has_success_confirmed
            or done_found
        )
        result_semantics = semantic_completion(
            result_path, completion_signal=completion_signal
        )
        has_done = result_semantics["has_done"]
        semantic_error = result_semantics["has_error"]
        has_partial_result = result_semantics["has_partial_result"]
        if completion_signal and semantic_error:
            semantic_reason = ", ".join(result_semantics["reasons"])
            print(
                f"  [{issue_id}] 🛑 semantic completion rejected: {semantic_reason}",
                flush=True,
            )
        # AGY/Gemini backend transport timeout, NOT our process timeout.
        # The supervisor still launches AGY with --print-timeout 24h0m0s and
        # does not kill active sessions for time. This flag means AGY itself
        # exited after its upstream/backend stopped responding.
        has_backend_timeout = "timed out waiting for response" in log_content
        has_clarify = "please clarify" in log_content.lower()

        # ABANDONMENT GUARD (per Michael 2026-06-23):
        # If AGY exited without writing RESULT.md, run the sentinel that:
        # 1. Writes a diagnostic RESULT.md
        # 2. Adds agent:needs-human-review label (stops re-dispatch loop)
        # 3. Posts a Linear comment explaining what happened
        if not has_result and not has_done and not has_backend_timeout and not has_start_timeout:
            print(f"  [{issue_id}] 🛡️  ABANDONMENT DETECTED — invoking guard", flush=True)
            try:
                guard_cmd = [
                    sys.executable, AGY_ABANDONMENT_GUARD,
                    "--issue", issue_id,
                    "--post-hoc",
                ]
                gr = subprocess.run(guard_cmd, cwd=str(sandbox),
                                    capture_output=True, text=True, timeout=60)
                if gr.returncode == 0:
                    print(f"  [{issue_id}] ✅ Guard wrote RESULT.md + marked for human review", flush=True)
                elif gr.returncode == 1:
                    # rc=1 = abandonment detected, guard handled it
                    print(f"  [{issue_id}] ✅ Guard handled abandonment (rc=1)", flush=True)
                else:
                    print(f"  [{issue_id}] ⚠️ Guard failed (rc={gr.returncode}): {gr.stderr[-300:]}", flush=True)
            except Exception as e:
                print(f"  [{issue_id}] ⚠️ Guard exception: {e}", flush=True)

        return {
            "issue_id": issue_id,
            "sandbox": str(sandbox),
            "elapsed_sec": int(elapsed),
            "exit_code": exit_code,
            "has_done": has_done,
            "has_error": error_found or semantic_error or has_start_timeout,
            "error_reason": (
                "Task failed to start (STARTED.md not written within 30s)"
                if has_start_timeout
                else error_reason
                or (", ".join(result_semantics["reasons"]) if semantic_error else "")
            ),
            "has_result": has_result,
            "has_self_review": has_self_review,
            "has_partial_result": has_partial_result,
            "has_backend_timeout": has_backend_timeout,
            "has_inactivity_kill": killed_for_inactivity,
            "has_start_timeout": has_start_timeout,
            "has_clarify": has_clarify,
            "result_semantics": result_semantics,
            "log_size": len(log_content),
            "log_path": str(log_path),
            "token": token_name,
        }
    except Exception as e:
        return {
            "issue_id": issue_id,
            "sandbox": str(sandbox),
            "elapsed_sec": int(time.time() - started_at),
            "exit_code": -1,
            "error": str(e),
            "log_path": str(log_path),
        }


# ── Lane-aware dispatch v2 ─────────────────────────────────
# All agent:* labels the factory dispatches. Includes AGY route labels
# (agent:agy-*) AND lane labels (agent:fred, agent:ned,
# agent:jules, agent:kai). MUST be kept in sync with the lane labels
# used in Linear tickets — otherwise the Linear fetch query silently
# filters out all lane-labeled work and the factory idles.
# Verified Jul 1 2026: this list was missing lane labels, which is why
# the factory looked "event-driven" but did nothing.
# Jul 1 2026: removed agent:codex — Michael directive "we never use it".
AGY_LABELS_DEFAULT = [
    "agent:agy",
    "agent:agy-pro",
    "agent:agy-lite",
    "agent:agy-flash-high",
    "agent:agy-sonnet",
    "agent:agy-thinking",
    "agent:agy-opus",
    "agent:agy-gpt-oss",
    "agent:agy-gemini-pro",
    "agent:agy-research",
    "agent:fred",
    "agent:ned",
    "agent:ned-code",
    "agent:ned-infra",
    "agent:ned-audit",
    "agent:jules",
    "agent:kai",
    "agent:kai-content",
    "agent:kai-css",
    "agent:kai-js",
    "agent:autobot",
    "agent:local-hermes",
    "agent:orchestrator",
    "agent:hermes",
    "agent:qwen-local",
    "agent:post-publish-doc-update",
    "agent:post-publish-done",
    "agent:antigravity-cli",
]
LANE_ORDER = ["on-demand", "priority", "project", "backlog"]
LANE_CAP_DEFAULTS = {"on-demand": 1, "priority": 2, "project": 1, "backlog": 1}

def _load_lane_caps_into_defaults():
    global LANE_CAP_DEFAULTS
    from pathlib import Path
    caps_path = Path.home() / ".prismatic" / "lane_caps.yaml"
    if caps_path.exists():
        try:
            import yaml
            with open(caps_path, "r") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    res = {}
                    for k, v in data.items():
                        if isinstance(k, str) and isinstance(v, (int, float)):
                            res[k] = int(v)
                    if res:
                        LANE_CAP_DEFAULTS.clear()
                        LANE_CAP_DEFAULTS.update(res)
        except Exception as e:
            print(f"Error loading lane caps from {caps_path}: {e}", flush=True)


# Per-issue model routing: when an issue has an agent:agy-* label, override the
# supervisor's default model. Mirrors agent_dispatcher.py:893-905 LABEL_TO_MODEL.
# Source of truth: agy_pool_aware_router.py ANTHROPIC_TIERS / GEMINI_TIERS.
LABEL_TO_MODEL = {
    "agent:agy":              "gemini-3.5-flash",
    "agent:agy-flash-high":   "gemini-3.5-flash",
    "agent:agy-pro":          "gemini-3.1-pro-high",
    "agent:agy-sonnet":       "claude-sonnet-4.6-thinking",
    "agent:agy-thinking":     "claude-opus-4.6-thinking",
    "agent:agy-opus":         "claude-opus-4.6-thinking",
    "agent:agy-gemini-pro":   "gemini-3.1-pro-high",
    "agent:agy-gpt-oss":      "gemini-3.5-flash",
    "agent:antigravity-cli":  "gemini-3.5-flash",
}
import sys
import os
from pathlib import Path

# Add active workspace (sandbox or main work dir) to sys.path
# v7 fix (Jul 2 2026): The system has a PEP 660 editable install of
# prismatic-engine at /home/ubuntu/work/prismatic-engine (the LIVE source
# tree) that adds itself to sys.path via __editable__...finder.__path_hook__.
# This shadows any cwd-based override because finder hooks are checked
# after sys.path entries. The fix: BEFORE adding cwd, verify the
# issue_to_task.py file exists at that cwd. If not, skip (don't pollute
# sys.path with a half-broken tree). And remove the editable install from
# sys.path so the cwd path wins.
import sys as _sys
# Strip the editable install hook
_sys.path = [p for p in _sys.path if "__editable__" not in p and "prismatic_engine" not in p]

# The executing supervisor checkout is authoritative. Fall back only when this
# source file is detached from a complete repository; never let later mutable
# profile/live paths override a valid immutable/current checkout.
for path_candidate in [
    str(Path(__file__).resolve().parent.parent),
    os.getcwd(),
    os.environ.get("PRISMATIC_HOME", str(Path.home() / "work")) + "/prismatic-engine",
    str(Path.home() / "work" / "prismatic-engine"),
]:
    if os.path.isfile(
        os.path.join(path_candidate, "prismatic", "curator", "issue_to_task.py")
    ):
        if path_candidate in _sys.path:
            _sys.path.remove(path_candidate)
        _sys.path.insert(0, path_candidate)
        break

from prismatic.curator.issue_to_task import (
    BLOCK_LABELS,
    BACKLOG_READY_LABELS,
    PRIORITY_LABELS,
    PROJECT_PWP_LABELS,
    REVIEW_ONLY_LABELS,
    _parse_linear_datetime,
    issue_labels,
    is_review_only_issue,
    task_priority_score,
    assign_lane,
    build_task_content_from_issue,
    issue_to_task,
)


def get_agy_labels() -> list[str]:
    """Return AGY routing labels from Antigravity config or defaults."""
    labels = AGY_LABELS_DEFAULT[:]
    config_path = Path(os.environ.get("AGY_CONFIG_PATH", str(Path.home() / ".antigravity" / "config.json")))
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
                if "labels" in config:
                    labels = config["labels"]
                elif "model_bindings" in config:
                    config_labels = [k for k in config["model_bindings"].keys() if k.startswith("agent:agy")]
                    if config_labels:
                        labels = config_labels
        except Exception as e:
            print(f"Failed to read labels from config: {e}")
    return labels


# The functions issue_labels, task_priority_score, and assign_lane are imported from prismatic.curator.issue_to_task above.


def compute_lane_caps(max_concurrent: int, lane_mode: str = "off") -> dict[str, int]:
    if lane_mode == "off":
        return {"default": max(1, max_concurrent)}
    _load_lane_caps_into_defaults()
    # Soft allocation: preserve the 3/2/1 intent when max_concurrent >= 6,
    # but never let all lanes go to zero when the operator runs a small test.
    if max_concurrent <= 1:
        return {"on-demand": 1, "priority": 1, "project": 1, "backlog": 1}
    if max_concurrent == 2:
        return {"on-demand": 1, "priority": 1, "project": 1, "backlog": 1}
    if max_concurrent == 3:
        return {"on-demand": 1, "priority": 2, "project": 1, "backlog": 1}
    if max_concurrent <= 5:
        return {"on-demand": 1, "priority": 2, "project": 2, "backlog": 1}
    return LANE_CAP_DEFAULTS.copy()


class LaneScheduler:
    """Thread-safe central scheduler for lane-aware dispatch.

    Workers stay unified. They ask the scheduler for the next task; the
    scheduler selects the highest-priority lane with queued work whose active
    count is below its cap. Active-count checks and increments happen under one
    condition lock to avoid the race AGY flagged in plan review.
    """

    def __init__(self, lane_caps: dict[str, int], lane_order: list[str] | None = None, max_total: int | None = None, max_concurrent_research: int | None = None, lane_mode: str = "off", max_concurrent: int = 1):
        self.lane_caps = lane_caps
        self.lane_order = lane_order or list(lane_caps.keys())
        self.max_total = max_total or max(1, sum(lane_caps.values()))
        self.queues = {lane: [] for lane in self.lane_order}
        for lane in lane_caps:
            self.queues.setdefault(lane, [])
        self.active = {lane: 0 for lane in self.queues}
        self.active_tasks = []
        self.max_concurrent_research = max_concurrent_research
        self.known_ids: set[str] = set()
        self.completed_ids: set[str] = set()
        self.condition = threading.Condition()

        # Hot-reloading state
        self.lane_mode = lane_mode
        self.max_concurrent = max_concurrent
        self.last_caps_mtime = None
        self.caps_path = Path.home() / ".prismatic" / "lane_caps.yaml"
        self._maybe_reload_caps()

    def _maybe_reload_caps(self) -> None:
        if self.lane_mode == "off":
            return
        try:
            if not self.caps_path.exists():
                current_mtime = 0
            else:
                current_mtime = self.caps_path.stat().st_mtime

            if self.last_caps_mtime != current_mtime:
                self.last_caps_mtime = current_mtime
                new_defaults = None
                if current_mtime > 0:
                    import yaml
                    with open(self.caps_path, "r") as f:
                        data = yaml.safe_load(f)
                        if isinstance(data, dict):
                            res = {}
                            for k, v in data.items():
                                if isinstance(k, str) and isinstance(v, (int, float)):
                                    res[k] = int(v)
                            if res:
                                new_defaults = res

                if new_defaults is None:
                    new_defaults = {"on-demand": 1, "priority": 2, "project": 1, "backlog": 1}

                global LANE_CAP_DEFAULTS
                LANE_CAP_DEFAULTS.clear()
                LANE_CAP_DEFAULTS.update(new_defaults)

                new_caps = compute_lane_caps(self.max_concurrent, lane_mode=self.lane_mode)
                with self.condition:
                    self.lane_caps = new_caps
                    for lane in self.lane_caps:
                        self.queues.setdefault(lane, [])
                        self.active.setdefault(lane, 0)
                    self.max_total = self.max_concurrent or max(1, sum(self.lane_caps.values()))
                    self.condition.notify_all()
                print(f"[LaneScheduler] Hot-reloaded lane caps: {self.lane_caps} (max_total={self.max_total})", flush=True)
        except Exception as e:
            print(f"[LaneScheduler] Error reloading lane caps: {e}", flush=True)


    def add(self, task: dict) -> bool:
        issue_id = task["issue_id"]
        lane = task.get("lane") or self.lane_order[-1]
        if lane not in self.queues:
            lane = self.lane_order[-1]
            task["lane"] = lane
        with self.condition:
            if issue_id in self.known_ids or issue_id in self.completed_ids:
                return False
            self.known_ids.add(issue_id)
            task["labels"] = task.get("labels") or set()
            self.queues[lane].append(task)
            # Keep each lane internally priority-ranked; FIFO only breaks ties.
            self.queues[lane].sort(key=lambda t: int(t.get("priority_score") or 0), reverse=True)
            self.condition.notify_all()
            return True

    def _is_task_eligible(self, task: dict) -> bool:
        if self.max_concurrent_research is None:
            return True
        labels = task.get("labels") or []
        if "agent:agy-research" in labels:
            active_research = sum(1 for t in self.active_tasks if "agent:agy-research" in (t.get("labels") or []))
            if active_research >= self.max_concurrent_research:
                return False
        return True

    def get_next(self, shutdown_event: threading.Event):
        self._maybe_reload_caps()
        with self.condition:
            while not shutdown_event.is_set():
                total_active = sum(self.active.values())
                # First pass: respect per-lane caps.
                for lane in self.lane_order:
                    cap = self.lane_caps.get(lane, 0)
                    if cap <= 0 or not self.queues.get(lane):
                        continue
                    if self.active.get(lane, 0) < cap:
                        found_idx = -1
                        for idx, task in enumerate(self.queues[lane]):
                            if self._is_task_eligible(task):
                                found_idx = idx
                                break
                        if found_idx != -1:
                            task = self.queues[lane].pop(found_idx)
                            task["lane"] = lane
                            task["lane_borrowed"] = False
                            self.active[lane] = self.active.get(lane, 0) + 1
                            self.active_tasks.append(task)
                            return task

                # Second pass: priority-ranked fallback borrowing. If a lane has
                # no eligible work, its unused capacity falls back to the highest
                # priority remaining task across all lanes — not FIFO backlog and
                # not arbitrary lane order.
                if total_active < self.max_total:
                    candidates = []
                    for lane in self.lane_order:
                        for idx, queued_task in enumerate(self.queues.get(lane, [])):
                            if self._is_task_eligible(queued_task):
                                candidates.append((int(queued_task.get("priority_score") or 0), -idx, lane, idx, queued_task))
                    if candidates:
                        _, _, lane, idx, task = max(candidates, key=lambda item: (item[0], item[1]))
                        self.queues[lane].pop(idx)
                        task["lane"] = lane
                        task["lane_borrowed"] = True
                        task["fallback_reason"] = "priority-ranked-borrow"
                        self.active[lane] = self.active.get(lane, 0) + 1
                        self.active_tasks.append(task)
                        return task

                self.condition.wait(timeout=1.0)
        return None

    def finish(self, task: dict, *, allow_requeue: bool = False):
        issue_id = task["issue_id"]
        lane = task.get("lane") or self.lane_order[-1]
        with self.condition:
            if lane in self.active and self.active[lane] > 0:
                self.active[lane] -= 1
            self.active_tasks = [t for t in self.active_tasks if t["issue_id"] != issue_id]
            self.known_ids.discard(issue_id)
            if not allow_requeue:
                self.completed_ids.add(issue_id)
            self.condition.notify_all()

    def queued_count(self) -> int:
        with self.condition:
            return sum(len(q) for q in self.queues.values())

    def active_count(self) -> int:
        with self.condition:
            return sum(self.active.values())

    def snapshot(self) -> dict:
        with self.condition:
            return {
                lane: {"queued": len(self.queues.get(lane, [])), "active": self.active.get(lane, 0), "cap": self.lane_caps.get(lane, 0)}
                for lane in self.lane_order
            }

    def is_idle(self) -> bool:
        with self.condition:
            return self.queued_count() == 0 and self.active_count() == 0


# ── Linear fetcher (kept from original) ──
def fetch_linear_issues(strict_opt_in: bool = False) -> list:
    import urllib.request
    key_path = Path(os.environ.get("HERMES_PROFILE_ENV", str(Path.home() / ".hermes" / "profiles" / "orchestrator" / ".env")))
    if not key_path.exists():
        return []
    key = None
    for line in key_path.read_text().split("\n"):
        if "LINEAR" in line and "KEY" in line and "=" in line:
            key = line.split("=", 1)[1].strip().strip("\"'")
            break
    if not key:
        return []

    labels = [label for label in get_agy_labels() if label not in REVIEW_ONLY_LABELS]

    query = """
    {
      issues(filter: {
        labels: {some: {name: {in: %s}}},
        state: {name: {in: ["Todo", "Backlog"]}}
      }, orderBy: createdAt, first: 50) {
        nodes { id identifier title description createdAt priority state { name } labels { nodes { name } } }
      }
    }
    """ % json.dumps(labels)

    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        nodes = data.get("data", {}).get("issues", {}).get("nodes", [])
        nodes = [node for node in nodes if not is_review_only_issue(node)]
        if strict_opt_in:
            # strict_opt_in (cron-mode) filter: only dispatch:ready or
            # dispatch:priority. The lane-aware auto-eligibility in
            # assign_lane() also accepts (lane label + prio 1 OR (prio 2 + recent)),
            # but that's a SECOND filter applied AFTER this Linear fetch.
            # To avoid the bypass, include the same lane+priority combinations
            # here that assign_lane() would accept (Jul 1 2026 — Fred).
            allowed = {"dispatch:ready", "dispatch:priority"}
            PRIO1_OR_RECENT = []  # built below after we know priority + age
            filtered = []
            for node in nodes:
                node_labels = {l["name"] for l in (node.get("labels") or {}).get("nodes", [])}
                if node_labels & allowed:
                    filtered.append(node)
                    continue
                # Auto-eligibility mirror: lane label + prio 1 OR (prio 2 + age < 14d)
                has_lane = any(l.startswith("agent:") for l in node_labels)
                priority = int(node.get("priority") or 0)
                created_at = (node.get("createdAt") or "")
                if has_lane and priority == 1:
                    filtered.append(node)
                    continue
                if has_lane and priority == 2 and created_at:
                    try:
                        from datetime import datetime, timezone
                        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        age_days = (datetime.now(timezone.utc) - created).days
                        if age_days < 14:
                            filtered.append(node)
                            continue
                    except Exception:
                        pass
                print(f"  [auto-resume-gate] strict-opt-in skip {node.get('identifier')}: missing dispatch:ready/dispatch:priority and not auto-eligible", flush=True)
            return filtered
        return nodes
    except Exception as e:
        print(f"Linear fetch failed: {e}")
        return []


def parse_linear_identifier(identifier: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z][A-Z0-9]{1,15})-([1-9][0-9]*)", identifier.strip().upper())
    if not match:
        raise ValueError(f"invalid Linear identifier: {identifier!r}")
    return match.group(1), int(match.group(2))


def fetch_single_linear_issue(
    identifier: str, *, issue_uuid: str | None = None
) -> dict | None:
    """Fetch one exact Linear issue by stable UUID or human identifier."""
    import urllib.request

    key = _read_linear_api_key()
    if not key:
        return None
    try:
        team_key, issue_number = parse_linear_identifier(identifier)
    except ValueError as exc:
        print(f"  [linear-fetch] {exc}", flush=True)
        return None
    normalized = f"{team_key}-{issue_number}"

    if issue_uuid is not None:
        issue_uuid = issue_uuid.strip().lower()
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            issue_uuid,
        ):
            print(f"  [linear-fetch] invalid issue UUID for {normalized}", flush=True)
            return None
        query = """
        query($issueUuid: String!) {
          issue(id: $issueUuid) {
            id identifier title description priority
            state { name }
            labels { nodes { name } }
          }
        }
        """
        variables = {"issueUuid": issue_uuid}
    else:
        query = """
        query($teamKey: String!, $number: Float!) {
          issues(
            first: 2
            filter: { team: { key: { eq: $teamKey } }, number: { eq: $number } }
          ) {
            nodes {
              id identifier title description priority
              state { name }
              labels { nodes { name } }
            }
          }
        }
        """
        variables = {"teamKey": team_key, "number": float(issue_number)}

    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())
        if data.get("errors"):
            raise RuntimeError(data["errors"][0].get("message", "Linear GraphQL error"))
        if issue_uuid is not None:
            matches = [data.get("data", {}).get("issue")]
        else:
            matches = data.get("data", {}).get("issues", {}).get("nodes", [])
        matches = [node for node in matches if node and node.get("identifier") == normalized]
        if len(matches) != 1:
            print(
                f"  [linear-fetch] {normalized} expected one exact match, got {len(matches)}",
                flush=True,
            )
            return None
        return matches[0]
    except Exception as exc:
        print(f"  [linear-fetch] {normalized} failed: {exc}", flush=True)
        return None


def _task_headers(task_content: str) -> tuple[str | None, set[str]]:
    workdir = None
    labels: set[str] = set()
    for line in task_content.splitlines()[:15]:
        if line.startswith("WORKDIR:"):
            workdir = line.split(":", 1)[1].strip() or None
        elif line.startswith("LABELS:"):
            labels = {
                part.strip()
                for part in line.split(":", 1)[1].split(",")
                if part.strip()
            }
    return workdir, labels


def resolve_exact_task(
    identifier: str,
    *,
    task_file: Path | None = None,
    linear_issue_uuid: str | None = None,
    workdir_override: str | None = None,
    fetch_issue=None,
) -> dict:
    """Resolve one exact task with explicit provenance and no mutable cache input."""
    fetch_issue = fetch_issue or fetch_single_linear_issue
    issue = None
    source = ""
    source_path = None
    if task_file is not None:
        task_file = task_file.expanduser()
        if not task_file.is_file() or task_file.is_symlink():
            raise RuntimeError(f"explicit task file is not a regular file: {task_file}")
        task_content = task_file.read_text()
        source = "explicit_task_file"
        source_path = str(task_file.resolve())
    else:
        if linear_issue_uuid is None:
            issue = fetch_issue(identifier)
        else:
            issue = fetch_issue(identifier, issue_uuid=linear_issue_uuid)
        if issue is None:
            raise RuntimeError(
                f"{identifier}: live Linear fetch failed; exact mode refuses mutable cache fallback"
            )
        task_content = build_task_content_from_issue(identifier, issue)
        source = "linear_uuid" if linear_issue_uuid else "linear_identifier"
    if not task_content.strip():
        raise RuntimeError(f"{identifier}: resolved task content is empty")
    header_workdir, header_labels = _task_headers(task_content)
    labels = issue_labels(issue) if issue is not None else header_labels
    workdir = workdir_override or header_workdir or "prismatic"
    return {
        "issue_id": identifier,
        "task_content": task_content,
        "workdir": workdir,
        "labels": labels,
        "task_source": source,
        "task_source_path": source_path,
        "task_sha256": hashlib.sha256(task_content.encode()).hexdigest(),
    }


# The functions build_task_content_from_issue and issue_to_task are imported from prismatic.curator.issue_to_task above.


# ── Dependency-injected clients for testing in isolation ──
class LinearClient:
    def fetch_issues(self, strict_opt_in: bool = False) -> list:
        return fetch_linear_issues(strict_opt_in)

    def fetch_single_issue(self, identifier: str) -> dict | None:
        return fetch_single_linear_issue(identifier)

    def update_issue(self, identifier: str, state: str) -> bool:
        return linear_update_issue(identifier, state)

    def add_comment(self, identifier: str, body: str) -> bool:
        return linear_comment(identifier, body)

    def add_labels(self, identifier: str, labels: list[str]) -> bool:
        try:
            from linear_helpers import linear_add_labels
            linear_add_labels(identifier, labels)
            return True
        except Exception:
            return False


class BusClient:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path

    def _get_db_path(self) -> str:
        return self.db_path or get_canonical_bus_path()

    def publish_completed(self, issue_id: str, payload: dict) -> None:
        publish_agent_completed(issue_id, payload)

    def publish_rejected(self, issue_id: str, payload: dict) -> None:
        publish_agent_completed(issue_id, payload, topic="agent.result.rejected")

    def publish_recovered(self, issue_id: str, payload: dict) -> None:
        publish_agent_recovered(issue_id, payload)

    def publish_canonical(self, topic: str, source: str, payload: dict) -> None:
        try:
            from publish_to_canonical_bus import publish_to_canonical_bus
            publish_to_canonical_bus(topic, source, payload)
        except Exception:
            pass

    def get_max_rowid(self) -> int:
        bus_db = self._get_db_path()
        if not os.path.exists(bus_db):
            return 0
        try:
            import sqlite3
            conn = sqlite3.connect(bus_db, timeout=2)
            try:
                row = conn.execute("SELECT MAX(rowid) FROM events").fetchone()
                return row[0] or 0
            finally:
                conn.close()
        except Exception:
            return 0

    def fetch_new_events(self, last_seen_rowid: int, limit: int = 50) -> list:
        bus_db = self._get_db_path()
        if not os.path.exists(bus_db):
            return []
        try:
            import sqlite3
            conn = sqlite3.connect(bus_db, timeout=2)
            try:
                rows = conn.execute(
                    "SELECT rowid, topic, payload_json, ts FROM events WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
                    (last_seen_rowid, limit),
                ).fetchall()
                return rows
            finally:
                conn.close()
        except Exception:
            return []

    def get_completed_attempt_count(self, issue_id: str) -> int:
        bus_db = self._get_db_path()
        if not os.path.exists(bus_db):
            return 0
        try:
            import sqlite3
            with sqlite3.connect(bus_db, timeout=5) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM events WHERE topic = 'agent.completed' AND payload_json LIKE ?",
                    (f'%"{issue_id}"%',)
                )
                return cursor.fetchone()[0]
        except Exception:
            return 0


class GatewayClient:
    def register(self, name: str, **kwargs) -> bool:
        try:
            import prismatic_service_registry as registry
            try:
                registry.deregister(name)
            except Exception:
                pass
            registry.register(name, **kwargs)
            return True
        except Exception:
            return False

    def heartbeat(self, name: str) -> bool:
        try:
            import prismatic_service_registry as registry
            registry.heartbeat(name)
            return True
        except Exception:
            return False


class CronClient:
    def pause_cron(self, reason: str) -> bool:
        return pause_supervisor_cron(reason)


class QuotaClient:
    def check_quota(self, model: str) -> tuple[bool, str]:
        try:
            import sys
            sys.path.insert(0, "/home/ubuntu/.hermes/profiles/orchestrator/scripts")
            from agy_quota_state import QuotaState
            qstate = QuotaState()
            return qstate.can_dispatch(model)
        except ImportError as imp_err:
            return True, f"quota check skipped: {imp_err}"
        except Exception as qe:
            return True, f"quota check error: {qe}"


# ── Event-driven worker pool ─────────────────────────────
class EventDrivenSupervisor:
    """
    Long-lived worker pool with event-driven task dispatch.

    Pattern:
      - N workers (N = max_concurrent) run in parallel as long-lived threads
      - Each worker waits on a queue.Queue
      - When a task is pushed to the queue, ANY waiting worker picks it up
      - When a worker finishes, it waits on the queue again
      - Result: a finished task is immediately replaced by a new one (event-driven)
      - All timings are randomized per-launch and per-completion
    """

    def __init__(self, max_concurrent: int, model: str, token_pool: TokenPool,
                 launch_jitter_range: tuple, backoff_range: tuple,
                 issue_batches_dir: Path = Path("/tmp/issue-batches"),
                 lane_mode: str = "off", active_project: str = "pwp",
                 backlog_age_days: int = 30, long_run: bool = False,
                 max_concurrent_research: int | None = None,
                 bus_client: BusClient | None = None,
                 gateway_client: GatewayClient | None = None,
                 linear_client: LinearClient | None = None,
                 cron_client: CronClient | None = None,
                 quota_client: QuotaClient | None = None):
        self.max_concurrent = max_concurrent
        self.model = model
        self.token_pool = token_pool
        self.launch_jitter_range = launch_jitter_range
        self.backoff_range = backoff_range
        self.issue_batches_dir = issue_batches_dir
        self.lane_mode = lane_mode
        self.active_project = active_project
        self.backlog_age_days = backlog_age_days
        self.long_run = long_run  # passed to circuit-breaker check
        self.max_concurrent_research = max_concurrent_research
        self.lane_caps = compute_lane_caps(max_concurrent, lane_mode=lane_mode)
        self.scheduler = LaneScheduler(self.lane_caps, ["default"] if lane_mode == "off" else LANE_ORDER, max_total=max_concurrent, max_concurrent_research=max_concurrent_research, lane_mode=lane_mode, max_concurrent=max_concurrent)

        self.completed_issues: set = set()
        self.completed_lock = threading.Lock()
        self.shutdown_event = threading.Event()
        self.workers: list[threading.Thread] = []
        self.results: list = []
        self.results_lock = threading.Lock()
        self.active_count = 0
        self.active_lock = threading.Lock()
        self.idle_event = threading.Event()  # Set when no workers are active
        self.consecutive_failures = 0
        self.circuit_lock = threading.Lock()
        self.circuit_tripped = False

        # Injected clients
        self.bus_client = bus_client or BusClient()
        self.gateway_client = gateway_client or GatewayClient()
        self.linear_client = linear_client or LinearClient()
        self.cron_client = cron_client or CronClient()
        self.quota_client = quota_client or QuotaClient()

    def _is_circuit_failure(self, result: dict) -> bool:
        # Only the current task's final completed-work-gated success may reset
        # consecutive failures. Every non-completing outcome remains a circuit
        # failure, including semantic/identity rejection and ledger failure.
        return result.get("completion_eligible") is not True

    def record_result_for_circuit(self, issue_id: str, result: dict) -> None:
        with self.circuit_lock:
            if self._is_circuit_failure(result):
                self.consecutive_failures += 1
                print(
                    f"  [circuit-breaker] consecutive failure {self.consecutive_failures}/"
                    f"{CIRCUIT_BREAKER_FAILURE_LIMIT} after {issue_id}",
                    flush=True,
                )
            else:
                self.consecutive_failures = 0
            # Long-run mode ignores circuit breaker — keeps the supervisor running
            # through transient AGY backend transport timeouts. Operators can
            # SIGTERM if they want a clean exit.
            if (self.consecutive_failures >= CIRCUIT_BREAKER_FAILURE_LIMIT
                    and not self.circuit_tripped
                    and not getattr(self, "long_run", False)):
                self.circuit_tripped = True
                reason = (
                    f"Circuit breaker tripped: {self.consecutive_failures} consecutive AGY failures "
                    f"(INACTIVITY_KILL/AGY_BACKEND_TIMEOUT/PARTIAL_RESULT)."
                )
                self.cron_client.pause_cron(reason)
                self.post_gate_alert(reason, f"Last issue: {issue_id}; sandbox/log: {result.get('sandbox')} / {result.get('log_path')}")
                self.shutdown_event.set()

    def add_task(self, task: dict):
        """Add a task to the lane scheduler. Safe to call from any thread.

        v6 fix (Jul 1 2026, GRO-3160): Also clear idle_event so the
        supervisor's wait_for_completion() loop wakes up immediately when
        a bus-subscriber event triggers a new task. Without this, the
        supervisor would stay asleep in long_run idle mode until the
        next 60s heartbeat check.
        """
        issue_id = task["issue_id"]
        if self.lane_mode == "off":
            task["lane"] = "default"
        added = self.scheduler.add(task)
        if added:
            # Wake up the main loop from long-run idle wait
            self.idle_event.clear()
            snap = self.scheduler.snapshot()
            lane = task.get("lane", "default")
            print(f"  [queue:{lane}] + {issue_id} (snapshot: {snap})", flush=True)
        return added

    def mark_completed(self, issue_id: str):
        with self.completed_lock:
            self.completed_issues.add(issue_id)

    def create_sandbox_env(self, task: dict) -> tuple[Path, Path, Path]:
        issue_id = task["issue_id"]
        src = resolve_workdir(task["workdir"])
        sandbox = create_sandbox(issue_id, src)
        manifest_path = task.get("repair_seed_manifest")
        if manifest_path:
            seeded = apply_repair_seed_manifest(
                sandbox,
                Path(manifest_path),
                task["repair_seed_sha256"],
            )
            print(
                f"  [{issue_id}] repair seed applied ({len(seeded)} files): "
                + ", ".join(seeded),
                flush=True,
            )
        task_path = write_task_file(issue_id, task["task_content"])
        log_path = LOGS_ROOT / f"{issue_id}.log"
        return sandbox, task_path, log_path

    def run_session(self, issue_id: str, sandbox: Path, task_path: Path, log_path: Path, run_model: str, token_name: str, lane: str) -> dict:
        return run_agy_session(
            issue_id, sandbox, task_path, log_path,
            run_model, jitter_range=self.launch_jitter_range,
            token=token_name,
            lane=lane
        )

    def post_gate_alert(self, reason: str, detail: str = "") -> None:
        body = "AUTO-RESUME GATE ALERT\n\n" + reason
        if detail:
            body += "\n\n" + detail
        for iid in AUTO_RESUME_ALERT_ISSUES:
            try:
                self.linear_client.add_comment(iid, body)
            except Exception as e:
                print(f"[auto-resume-gate] WARN: Linear alert failed for {iid}: {e}", flush=True)

    def register_service(self):
        if not self.gateway_client:
            return
        registered = self.gateway_client.register(
            "prismatic.supervisor",
            host="localhost",
            port=0,  # Supervisor doesn't bind a port
            version="0.1.0",
            role="event_driven_dispatcher",
            model=self.model,
            max_concurrent=self.max_concurrent,
            lane_mode=self.lane_mode,
            active_project=self.active_project,
            long_run=self.long_run,
        )
        if registered:
            print(f"  [registry] Registered prismatic.supervisor in service registry", flush=True)

            # Start a heartbeat thread (30s interval)
            def _heartbeat_loop():
                while not self.shutdown_event.is_set():
                    try:
                        self.gateway_client.heartbeat("prismatic.supervisor")
                    except Exception:
                        pass
                    self.shutdown_event.wait(timeout=30)
            threading.Thread(target=_heartbeat_loop, daemon=True).start()

    def worker_loop(self, worker_id: int):
        """
        Long-lived worker thread. Pulls tasks from the queue and runs them.
        Each iteration:
          1. Wait for a task (blocking, with shutdown check)
          2. Run the task (with launch jitter)
          3. Sleep completion backoff (random per-completion)
          4. Loop
        """
        # Stagger worker startup so they don't all race for the queue at t=0
        startup_delay = random.uniform(*WORKER_STARTUP_STAGGER)
        if startup_delay > 0:
            print(f"  [worker-{worker_id}] starting in {startup_delay:.1f}s", flush=True)
            if self.shutdown_event.wait(timeout=startup_delay):
                return

        while not self.shutdown_event.is_set():
            task = self.scheduler.get_next(self.shutdown_event)
            if task is None:
                continue

            issue_id = task["issue_id"]
            lane = task.get("lane", "default")
            # Per-iteration result state must be reset before any early exit.
            # Otherwise a prior task's successful result can mark this task
            # completed/non-requeueable from the finally block.
            result = None
            with self.active_lock:
                self.active_count += 1
                self.idle_event.clear()

            try:
                # Create sandbox (idempotent)
                try:
                    sandbox, task_path, log_path = self.create_sandbox_env(task)
                except Exception as e:
                    print(f"  [{issue_id}] sandbox failed: {e}", flush=True)
                    continue

                # At task pickup (before run_agy_session):
                token_info = None
                if self.token_pool:
                    token_info = self.token_pool.acquire()
                token_name = token_info["name"] if token_info else "none"

                try:
                    self.linear_client.update_issue(issue_id, state="In Progress")
                    # Per-issue model override: scan labels for agent:agy-* and
                    # override self.model if found. This lets "thinky" tickets
                    # opt into claude-opus-4.6-thinking while the rest of the
                    # batch stays on the supervisor's default model. (Jun 30
                    # 2026 — Michael: "Use agent:agy-opus for the thinky stuff.")
                    task_labels = task.get("labels") or []
                    per_issue_model = None
                    for lbl in task_labels:
                        if lbl.startswith("agent:agy-"):
                            per_issue_model = LABEL_TO_MODEL.get(lbl)
                            if per_issue_model:
                                print(f"  [{issue_id}] per-issue model override: {lbl} → {per_issue_model}", flush=True)
                            break
                    run_model = per_issue_model or self.model

                    # Quota throttle (GRO-3160/agy-quota-state, Jul 2 2026):
                    # Check the quota state module before launching AGY.
                    try:
                        can_dispatch, reason = self.quota_client.check_quota(run_model)
                        if not can_dispatch:
                            print(f"  [{issue_id}] ⛔ quota throttle: {reason}", flush=True)
                            # Try to alert via the bus so the UI shows it
                            self.bus_client.publish_canonical(
                                "quota.dispatch_blocked",
                                "supervisor",
                                {
                                    "issue_id": issue_id,
                                    "model": run_model,
                                    "reason": reason,
                                }
                            )
                            # Revert Linear state and skip
                            try:
                                self.linear_client.update_issue(issue_id, state="Todo")
                            except Exception:
                                pass
                            # Re-queue: put it back in the lane scheduler to be
                            # tried again on the next worker tick.
                            task["_quota_blocked"] = True
                            task["_quota_blocked_at"] = time.time()
                            if self.add_task(task):
                                # Count it as not-added (we already had it)
                                pass
                            # Don't decrement active_count — we never launched
                            self.idle_event.set()  # wake the main loop on next tick
                            continue
                        elif "critical" in reason or "below critical" in reason:
                            print(f"  [{issue_id}] ⚠️  quota warning: {reason}", flush=True)
                    except Exception as qe:
                        print(f"  [{issue_id}] ⚠️  quota check error: {qe}", flush=True)

                    self.linear_client.add_comment(issue_id,
                        "Started: " + datetime.utcnow().isoformat() + "Z | lane: " + lane + " | sandbox: "
                        + str(sandbox) + " | model: " + run_model + " | token: " + token_name)
                except Exception as ex:
                    print(f"  [{issue_id}] Linear transition failed (start): {ex}", flush=True)
                    run_model = self.model

                # Run the session
                print(f"  [worker-{worker_id}:{lane}] picked up {issue_id}", flush=True)
                result = self.run_session(
                    issue_id, sandbox, task_path, log_path,
                    run_model, token_name, lane
                )
                result["worker_id"] = worker_id
                result["lane"] = lane

                # Post-condition check: did AGY write RESULT.md?
                # AGY's --print mode outputs to stdout; even if it says "DONE",
                # it might not have saved the file. Check the file system.
                result_path = sandbox / "RESULT.md"
                result["has_result_file"] = result_path.exists() and result_path.stat().st_size >= 1024
                result_semantics = semantic_completion(
                    result_path, completion_signal=bool(result.get("has_done"))
                )
                result["result_semantics"] = result_semantics
                if result.get("has_done") and not result_semantics["has_done"]:
                    print(
                        f"  [worker-{worker_id}] {issue_id} completion rejected by RESULT.md semantics: "
                        f"{', '.join(result_semantics['reasons'])}",
                        flush=True,
                    )
                    result["has_done"] = False
                    result["has_error"] = result_semantics["has_error"]
                    result["error_reason"] = ", ".join(result_semantics["reasons"])
                    result["has_partial_result"] = result_semantics["has_partial_result"]
                elif result.get("has_done") and not result["has_result_file"]:
                    # AGY said DONE but no file or too small — downgrade to "missing_result"
                    print(f"  [worker-{worker_id}] {issue_id} said DONE but no/small RESULT.md found — marking as 🟡 missing_result", flush=True)
                    result["has_done"] = False
                    result["has_missing_result"] = True

                # Resolve attempt before durable capture/publication, preserving
                # the existing task/label/bus precedence.
                attempt_value = (
                    task.get("attempt")
                    or task.get("attempt_count")
                    or task.get("retry_count")
                )
                attempt = None
                if attempt_value:
                    try:
                        parsed_attempt = int(attempt_value)
                        if parsed_attempt > 0:
                            attempt = parsed_attempt
                    except (TypeError, ValueError):
                        pass
                if not attempt:
                    for lbl in task.get("labels", ()):
                        if "attempt" in lbl.lower():
                            digits = "".join(c for c in lbl if c.isdigit())
                            if digits:
                                attempt = int(digits)
                                break
                if not attempt:
                    attempt = self.bus_client.get_completed_attempt_count(issue_id) + 1
                if not attempt:
                    attempt = 1

                boundary = capture_and_validate_agy_result(
                    issue_id=issue_id,
                    attempt=attempt,
                    result_path=result_path,
                    packet_path=sandbox / _AGY_PACKET_NAME,
                    raw_output_db=agy_raw_output_db_path(),
                    completed_work_db=agy_completed_work_db_path(),
                    completed_work_evidence_dir=agy_completed_work_evidence_dir(),
                )
                completion_eligible = bool(
                    result.get("has_done")
                    and result_semantics["has_done"]
                    and boundary["raw_capture_succeeded"]
                    and boundary["boundary_state"] == "canonical_valid"
                    and boundary.get("packet_issue_identifier") == issue_id
                    and boundary.get("completed_work_persisted") is True
                )
                result["result_boundary"] = boundary
                result["completion_eligible"] = completion_eligible

                # Log status
                status = ("✅ DONE" if completion_eligible else
                          "🔴 ERROR" if result.get("has_error") else
                          "🛑 INACTIVITY_KILL" if result.get("has_inactivity_kill") else
                          "⏳ AGY_BACKEND_TIMEOUT" if result.get("has_backend_timeout") else
                          "🟠 PARTIAL_RESULT" if result.get("has_partial_result") else
                          "❓ CLARIFY" if result.get("has_clarify") else
                          "🟡 MISSING_RESULT" if result.get("has_missing_result") else
                          f"🏁 EXIT {result.get('exit_code', '?')}")
                print(f"  [worker-{worker_id}] {issue_id} {status} | "
                      f"{result.get('elapsed_sec', 0)}s | log: {result.get('log_size', 0)}b",
                      flush=True)

                # Publish agent.completed event (GRO-3094)
                try:
                    result_status = ("has_done" if completion_eligible else
                                     boundary["boundary_state"] if result.get("has_done") else
                                     "has_error" if result.get("has_error") else
                                     "has_inactivity_kill" if result.get("has_inactivity_kill") else
                                     "has_backend_timeout" if result.get("has_backend_timeout") else
                                     "has_partial_result" if result.get("has_partial_result") else
                                     "has_clarify" if result.get("has_clarify") else
                                     "has_missing_result" if result.get("has_missing_result") else
                                     "exit_other")
                    
                    elapsed_sec = result.get("elapsed_sec", 0)
                    cost_usd_estimated = estimate_cost(issue_id, self.model, elapsed_sec)

                    payload = {
                        "issue_id": issue_id,
                        "lane": lane,
                        "model": self.model,
                        "worker_id": worker_id,
                        "elapsed_sec": elapsed_sec,
                        "exit_code": result.get("exit_code", -1),
                        "result_status": result_status,
                        "result_path": str(result_path),
                        "has_result_file": result.get("has_result_file", False),
                        "result_semantic_pass": result_semantics["passed"],
                        "result_semantic_reasons": result_semantics["reasons"],
                        "boundary_state": boundary["boundary_state"],
                        "boundary_reason": boundary["boundary_reason"],
                        "raw_output_id": boundary.get("raw_output_id"),
                        "normalization_status": boundary.get("normalization_status"),
                        "canonical_packet_id": boundary.get("canonical_packet_id"),
                        "queue_rejection_reason": boundary.get("queue_rejection_reason"),
                        "queue_repair_hint": boundary.get("queue_repair_hint"),
                        "completed_work_persisted": boundary.get(
                            "completed_work_persisted", False
                        ),
                        "completed_work_id": boundary.get("completed_work_id"),
                        "completed_work_classification": boundary.get(
                            "completed_work_classification"
                        ),
                        "completed_work_integration_classification": boundary.get(
                            "completed_work_integration_classification"
                        ),
                        "completed_work_ingestion_marker": boundary.get(
                            "completed_work_ingestion_marker"
                        ),
                        "completed_work_integration_marker": boundary.get(
                            "completed_work_integration_marker"
                        ),
                        "completed_work_eligible_for_merge": boundary.get(
                            "completed_work_eligible_for_merge", False
                        ),
                        "completion_eligible": completion_eligible,
                        "cost_usd_estimated": cost_usd_estimated,
                        "attempt": attempt
                    }
                    if completion_eligible:
                        self.bus_client.publish_completed(issue_id, payload)
                        published_topic = "agent.completed"
                    else:
                        self.bus_client.publish_rejected(issue_id, payload)
                        published_topic = "agent.result.rejected"
                    print(
                        f"  [{issue_id}] Published {published_topic} to event bus WAL with status: {result_status}",
                        flush=True,
                    )
                except Exception as p_err:
                    print(f"  [{issue_id}] ⚠️ agent.completed publish failed: {p_err}", flush=True)

                # At task completion (after result processing):
                try:
                    elapsed_sec = result.get("elapsed_sec", 0)
                    if completion_eligible:
                        self.linear_client.update_issue(issue_id, state="Done")
                        self.linear_client.add_comment(issue_id,
                            "Completed: " + str(elapsed_sec) + "s | lane: " + lane + " | exit_code: "
                            + str(result["exit_code"]) + " | result: " + result["log_path"])
                        # Jun 30 2026 fix: also fire the post-publish chain by adding
                        # agent:done label. The webhook_event_bridge listens for this
                        # label and runs post_publish_review_orchestrator.py.
                        try:
                            self.linear_client.add_labels(issue_id, ["agent:done"])
                        except Exception as _e:
                            # Don't fail the run on label-write error
                            pass
                    elif result.get("has_inactivity_kill"):
                        self.linear_client.add_comment(issue_id, "INACTIVITY_KILL after " + str(elapsed_sec) + "s — no sandbox file changes for " + str(AGY_INACTIVITY_KILL_SEC) + "s; terminated AGY and ran abandonment guard")
                    elif result.get("has_backend_timeout"):
                        # Stay in In Progress; this is AGY/Gemini backend transport timeout,
                        # not a Fred/supervisor time limit.
                        self.linear_client.add_comment(issue_id, "AGY_BACKEND_TIMEOUT after " + str(elapsed_sec) + "s — AGY exited after upstream/backend stopped responding; supervisor did not kill the session")
                    elif result.get("has_partial_result"):
                        # A partial packet is never equivalent to completed work. Size is not
                        # semantic proof and must not transition Linear to Done.
                        partial_size = result_path.stat().st_size if result_path.exists() else 0
                        self.linear_client.add_comment(
                            issue_id,
                            "⚠️ PARTIAL_RESULT after "
                            + str(elapsed_sec)
                            + f"s — RESULT.md exists ({partial_size}b) without a semantically valid completion; leaving In Progress for review. Sandbox: "
                            + str(sandbox),
                        )
                    elif result.get("has_missing_result"):
                        # Downgrade state back to Todo
                        self.linear_client.update_issue(issue_id, state="Todo")
                        self.linear_client.add_comment(issue_id,
                            f"🟡 MissingResult: Task reported DONE but no RESULT.md found in sandbox after {elapsed_sec}s. Reverted to Todo.")
                    elif result.get("has_start_timeout"):
                        # Downgrade state back to Todo
                        self.linear_client.update_issue(issue_id, state="Todo")
                        self.linear_client.add_comment(issue_id,
                            f"🛑 StartTimeout: STARTED.md was not written within 30s of launch. Terminated AGY and reverted to Todo.")
                    elif result.get("has_error"):
                        # Downgrade state back to Todo
                        self.linear_client.update_issue(issue_id, state="Todo")
                        self.linear_client.add_comment(issue_id,
                            f"🔴 ERROR: Task reported failure: {result.get('error_reason', 'unknown reason')} after {elapsed_sec}s. Reverted to Todo.")
                except Exception as ex:
                    print(f"  [{issue_id}] Linear transition failed (completion): {ex}", flush=True)

                with self.results_lock:
                    self.results.append(result)
                self.record_result_for_circuit(issue_id, result)

                # Quality gate post-condition (Jun 30 hook): if task has
                # RESULT.md, fire-and-forget agent_output_validator so peer
                # review can run on real artifacts. Don't block — failure
                # here is logged but doesn't change supervisor state.
                try:
                    sandbox_dir = SANDBOX_ROOT / issue_id
                    result_md = sandbox_dir / "RESULT.md"
                    if result_md.exists() and completion_eligible:
                        import subprocess
                        validator = (
                            os.environ.get("HERMES_PROFILE_ROOT", str(Path.home() / ".hermes" / "profiles" / "orchestrator")) + "/"
                            "scripts/agent_output_validator.py"
                        )
                        # Async fire; supervisor continues
                        subprocess.Popen(
                            ["python3", validator, issue_id, "--quiet"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        print(f"  [{issue_id}] ✅ quality-gate fired", flush=True)
                except Exception as e:
                    # Quality gate fail is non-fatal
                    pass

                # Per-completion randomized backoff — the heart of organic scaling
                backoff = random.uniform(*self.backoff_range)
                print(f"  [worker-{worker_id}] backoff: {backoff:.1f}s before next task",
                      flush=True)
                if self.shutdown_event.wait(timeout=backoff):
                    if completion_eligible:
                        self.mark_completed(issue_id)
                    break
            finally:
                completion_eligible = bool(
                    isinstance(result, dict)
                    and result.get("completion_eligible") is True
                )
                allow_requeue = not completion_eligible
                if completion_eligible:
                    self.mark_completed(issue_id)
                self.scheduler.finish(task, allow_requeue=allow_requeue)
                with self.active_lock:
                    self.active_count -= 1
                    if self.active_count == 0:
                        self.idle_event.set()

    def start_workers(self):
        for i in range(self.max_concurrent):
            t = threading.Thread(target=self.worker_loop, args=(i + 1,),
                                 name=f"agy-worker-{i+1}", daemon=True)
            t.start()
            self.workers.append(t)
        print(f"  Started {self.max_concurrent} workers", flush=True)

    def wait_for_completion(self, idle_timeout: float = 60.0, max_cap_sec: float = 3600.0, long_run: bool = False):
        """
        Wait until the queue is empty AND all workers are idle.
        idle_timeout: how long to wait after the last worker becomes idle
        (gives Linear watchdog time to add new issues before declaring done).

        v5 fix (2026-06-23):
        - Initial cap raised from 30min to 1hr (3600s) per Michael's directive
        - DYNAMIC ROOF RAISE: if all workers are still active but sandbox has
          recent activity at the cap, automatically extend the cap by 1hr and
          keep waiting. Only force-shutdown when ALL sandboxes show no activity
          for >90s.
        - Per Michael: "raise the limit to 1hr. Or, there is no hard limit,
          like, if at 55mins, it's still going, it gets the roof raised and
          keeps checking?"
        - Early 2-min "knowing" signal: heartbeat_watcher emits a verdict
          within 2 min of each AGY launch (separate fix).

        v6 fix (Jul 1 2026, GRO-3160):
        - In long_run mode, never return on idle. The supervisor must keep
          running so the bus-subscriber can pick up new events as they arrive.
          Previously: idle_event.wait(86400) timed out, hit line 2228 `return`,
          and the supervisor exited. The watchdog then restarted it, but
          there was a 30s+ window where no work was being processed AND
          no new work was being detected (linear.issue.create events
          landing on the bus during that window were missed).
        - Now: long_run mode keeps the main loop alive indefinitely.
          SIGTERM (or shutdown_event) is the only clean exit.
        - Operators should NEVER wait >2 min without knowing what's happening.
        """
        # Wait for all queued tasks + active workers to drain. LaneScheduler replaces Queue.join().
        # Initial cap: 1 hour. If workers are still active but sandboxes have
        # recent activity, raise the roof (extend) and keep waiting.
        while True:
            if not self.idle_event.wait(timeout=max_cap_sec):
                # At the cap. Check if any worker is making progress.
                # If sandboxes have been modified in the last 90s, RAISE THE ROOF
                # by another 1hr and keep waiting.
                stuck_workers = []
                active_workers = []
                for sandbox_dir in Path(SANDBOX_ROOT).iterdir():
                    if not sandbox_dir.is_dir():
                        continue
                    try:
                        last_mtime = max(
                            (f.stat().st_mtime for f in sandbox_dir.rglob("*") if f.is_file()),
                            default=0
                        )
                        if last_mtime and (time.time() - last_mtime > 90):
                            stuck_workers.append(sandbox_dir.name)
                        else:
                            active_workers.append(sandbox_dir.name)
                    except (OSError, PermissionError):
                        stuck_workers.append(sandbox_dir.name)
                if stuck_workers and not active_workers:
                    # All stuck, no activity — force-shutdown truly stuck workers
                    print(f"  ⚠️  {len(stuck_workers)} stuck worker(s) at {int(max_cap_sec/60)}min cap — force-shutting down: {stuck_workers[:5]}", flush=True)
                    self.shutdown_event.set()
                    for t in self.workers:
                        t.join(timeout=10)
                    return
                elif stuck_workers and active_workers:
                    # Mixed: kill only the stuck ones, let the active ones keep going
                    print(f"  ⚠️  {len(stuck_workers)} stuck, {len(active_workers)} active — force-killing stuck, extending cap by 1hr for active", flush=True)
                    # Note: we can't kill specific workers mid-loop, so extend cap.
                    max_cap_sec += 3600
                    print(f"  🔄 Roof raised to {int(max_cap_sec/60)} min — active workers: {active_workers[:3]}", flush=True)
                else:
                    # All active — raise the roof and keep going
                    print(f"  ℹ️  All {len(active_workers)} workers active at {int(max_cap_sec/60)}min cap — raising roof by 1hr", flush=True)
                    max_cap_sec += 3600
                    print(f"  🔄 New cap: {int(max_cap_sec/60)} min", flush=True)
            else:
                # All workers idle. Wait a bit more in case watchdog adds more.
                print(f"  All workers idle. Waiting {idle_timeout}s for new arrivals...",
                      flush=True)
                if not self.idle_event.wait(timeout=idle_timeout):
                    # New work arrived during the wait — loop again
                    continue
                # No new work during the wait. In long_run mode, keep looping
                # forever (the bus-subscriber thread is the primary dispatch
                # mechanism and can wake us via idle_event when a new bus
                # event arrives). The only clean exit is shutdown_event.
                if long_run:
                    print(f"  🔁 long_run: idle but staying alive. "
                          f"Waiting for new bus events or SIGTERM...", flush=True)
                    # Block until either shutdown is requested or new work arrives
                    while not self.shutdown_event.is_set():
                        # Wait with periodic check (1min granularity for log heartbeat)
                        woke = self.idle_event.wait(timeout=60.0)
                        if woke:
                            # New work arrived — go back to the top of the loop
                            print(f"  ⚡ New work arrived during long-run idle wait", flush=True)
                            break
                    if self.shutdown_event.is_set():
                        print(f"  🛑 Shutdown requested during long-run idle", flush=True)
                        return
                    continue
                # Non-long-run: exit after idle_timeout
                return

    def shutdown(self):
        self.shutdown_event.set()
        # Terminate any in-flight agy-bin subprocesses before joining threads.
        # Without this, a circuit-trip mid-run would leave orphans because
        # worker threads die without sending SIGTERM to their children.
        killed = terminate_all_active_procs(timeout=5.0)
        if killed:
            print(f"  [shutdown] terminated {killed} active agy-bin subprocess(es)", flush=True)
        for t in self.workers:
            t.join(timeout=10)


# ── Linear watchdog thread ──


# ── Bus event subscriber (event-driven path, Jul 1 2026) ─────
# Replaces the 120s Linear polling as the PRIMARY dispatch mechanism.
# Polls the canonical bus SQLite for new rows since last_seen_rowid.
# On agent.completed: nothing — supervisor.add_task was already called
#   by the producer side (publish_agent_completed runs in the same process).
#   We just log it for observability.
# On issue.created (if Linear webhook ever fires it via the bus): fetch and add.
# On agent.failed/agent.died: log for visibility.

DEFAULT_BUS_POLL_INTERVAL = 0.5  # 500ms cadence — fast enough to feel event-driven

def get_canonical_bus_path() -> str:
    """Same path resolution as publish_agent_completed — must stay in sync."""
    if os.environ.get("PRISMATIC_BUS_DB"):
        return os.environ["PRISMATIC_BUS_DB"]
    home = os.path.expanduser("~")  # NOT $PRISMATIC_HOME
    return os.path.join(home, ".prismatic", "bus", "event_log.sqlite")


def bus_event_subscriber_loop(supervisor, stop_event: threading.Event, poll_interval: float = DEFAULT_BUS_POLL_INTERVAL):
    """Event-driven subscriber — poll the canonical bus SQLite for new events.

    Replaces the 120s Linear polling as the primary dispatch mechanism.
    Falls back to 600s Linear polling for missed events (handled by
    linear_watchdog_loop running in parallel).
    """
    last_seen_rowid = supervisor.bus_client.get_max_rowid()
    print(f"  [bus-subscriber] watching bus from rowid={last_seen_rowid} (poll every {poll_interval}s)", flush=True)

    while not stop_event.is_set():
        try:
            rows = supervisor.bus_client.fetch_new_events(last_seen_rowid, limit=50)
            for rowid, topic, payload_json, ts in rows:
                last_seen_rowid = max(last_seen_rowid, rowid)
                if topic == "agent.completed":
                    # Producer side already advanced the queue. Log for observability.
                    try:
                        import json as _json
                        p = _json.loads(payload_json).get("payload", {})
                        print(f"  [bus] agent.completed: {p.get('issue_id', '?')} status={p.get('result_status', '?')}", flush=True)
                    except Exception:
                        print(f"  [bus] agent.completed rowid={rowid}", flush=True)
                elif topic == "agent.failed":
                    try:
                        import json as _json
                        p = _json.loads(payload_json).get("payload", {})
                        print(f"  [bus] agent.failed: {p.get('issue_id', '?')} err={p.get('error', '?')[:80]}", flush=True)
                    except Exception:
                        print(f"  [bus] agent.failed rowid={rowid}", flush=True)
                elif topic == "agent.died":
                    print(f"  [bus] agent.died rowid={rowid}", flush=True)
                elif topic.startswith("linear.issue.") or topic == "issue.created":
                    # Linear webhook fired (or issue.created). Fetch the issue and
                    # enqueue it for dispatch. This is the real event-driven path
                    # that was missing — GRO-3151 / GRO-3156 wired the webhook
                    # to publish these events; this handler consumes them.
                    try:
                        import json as _json
                        p = _json.loads(payload_json).get("payload", {})
                        iid = p.get("issue_id") or p.get("identifier")
                        if iid:
                            # Fetch the issue from Linear and build a task
                            try:
                                node = supervisor.linear_client.fetch_single_issue(iid)
                                if node:
                                    task = issue_to_task(
                                        node,
                                        lane_mode=supervisor.lane_mode,
                                        active_project=supervisor.active_project,
                                        backlog_age_days=supervisor.backlog_age_days,
                                    )
                                    if task:
                                        added = supervisor.add_task(task)
                                        if added:
                                            print(f"  [bus] {topic}: {iid} -> enqueued for dispatch", flush=True)
                                        else:
                                            print(f"  [bus] {topic}: {iid} -> queue full, skipped", flush=True)
                                    else:
                                        print(f"  [bus] {topic}: {iid} -> no task (skipped)", flush=True)
                                else:
                                    print(f"  [bus] {topic}: {iid} -> fetch failed", flush=True)
                            except Exception as fetch_exc:
                                print(f"  [bus] {topic}: {iid} -> fetch error: {fetch_exc}", flush=True)
                    except Exception as exc:
                        print(f"  [bus] {topic} parse error: {exc}", flush=True)
                elif topic.startswith("linear.comment."):
                    # Linear comment events — log only, not dispatched (comments
                    # are reference data, not work).
                    try:
                        import json as _json
                        p = _json.loads(payload_json).get("payload", {})
                        iid = p.get("issue_id", "?")
                        print(f"  [bus] {topic}: {iid} (comment, logged only)", flush=True)
                    except Exception:
                        pass
            stop_event.wait(timeout=poll_interval)
        except Exception as e:
            print(f"  [bus-subscriber] error: {e}", flush=True)
            stop_event.wait(timeout=poll_interval * 2)


# ── Single-instance lock (prevent watchdog cascade) ───────────
SUPERVISOR_LOCK_PATH = os.path.join(
    os.path.expanduser("~"), ".prismatic", "run", "supervisor.lock"
)


def acquire_supervisor_lock() -> bool:
    """Acquire single-instance lock. Returns True if acquired, False if held.

    Writes our PID to a lockfile. Stale lockfiles (older than 5 min) are
    treated as dead and overwritten — this handles the case where a
    supervisor crashed without releasing the lock.
    """
    import fcntl
    lock_path = SUPERVISOR_LOCK_PATH
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    try:
        # Check for stale lock
        if os.path.exists(lock_path):
            try:
                with open(lock_path) as f:
                    old_pid = int(f.read().strip() or "0")
                # Is the old PID still alive?
                try:
                    os.kill(old_pid, 0)
                    if old_pid != os.getpid():
                        return False  # Held by another live process
                except OSError:
                    pass  # Stale
            except (ValueError, OSError):
                pass
        # Acquire
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        print(f"  [lock] failed: {e}", flush=True)
        return False


def release_supervisor_lock() -> None:
    try:
        if os.path.exists(SUPERVISOR_LOCK_PATH):
            with open(SUPERVISOR_LOCK_PATH) as f:
                pid = int(f.read().strip() or "0")
            if pid == os.getpid():
                os.unlink(SUPERVISOR_LOCK_PATH)
    except Exception:
        pass
def linear_watchdog_loop(supervisor: EventDrivenSupervisor, stop_event: threading.Event, strict_opt_in: bool = False):
    """Periodically poll Linear and add new issues to the queue."""
    print(f"  [watchdog] polling Linear every {LINEAR_POLL_INTERVAL}s", flush=True)
    poll_count = 0
    while not stop_event.is_set():
        try:
            poll_count += 1
            run_agy_raw_recovery_batch(
                lease_owner=f"watchdog-{os.getpid()}", limit=10
            )
            t_start = time.time()
            issues = supervisor.linear_client.fetch_issues(strict_opt_in=strict_opt_in)
            elapsed = time.time() - t_start
            added = 0
            for issue in issues:
                task = issue_to_task(issue, lane_mode=supervisor.lane_mode, active_project=supervisor.active_project, backlog_age_days=supervisor.backlog_age_days)
                if task and task["issue_id"] not in supervisor.completed_issues:
                    supervisor.add_task(task)
                    added += 1
            if added:
                print(f"  [watchdog poll #{poll_count}] added {added} new issue(s) from Linear ({elapsed:.1f}s)", flush=True)
            elif poll_count <= 3 or poll_count % 5 == 0:
                # Print on first 3 polls + every 5th after, so we can see it's alive
                print(f"  [watchdog poll #{poll_count}] fetched {len(issues)} issue(s), {added} new ({elapsed:.1f}s)", flush=True)
        except Exception as e:
            print(f"  [watchdog poll #{poll_count}] error: {e}", flush=True)
        # Sleep with shutdown awareness
        stop_event.wait(timeout=LINEAR_POLL_INTERVAL)


# ── Main ─────────────────────────────────────────────────
def build_argument_parser() -> argparse.ArgumentParser:
    """Build the supervisor CLI parser without acquiring locks or touching runtime state."""
    parser = argparse.ArgumentParser(description="AGY Sandbox Event-Driven Supervisor")
    parser.add_argument("--issue", help="Single issue ID (e.g., GRO-1928)")
    parser.add_argument("--issues", help="Comma-separated issue IDs")
    parser.add_argument("--from-linear", action="store_true", help="Fetch from Linear at start")
    parser.add_argument("--workdir", help="Source workdir (shorthand or absolute)")
    parser.add_argument(
        "--task-file",
        type=Path,
        help="Explicit exact-task packet; exact mode never reads mutable cache",
    )
    parser.add_argument(
        "--linear-issue-uuid",
        help="Stable Linear issue UUID; response identifier must match --issue",
    )
    parser.add_argument(
        "--repair-seed-manifest",
        type=Path,
        help="Hash-bound JSON manifest of files to copy after clean sandbox creation",
    )
    parser.add_argument(
        "--repair-seed-sha256",
        help="Required SHA-256 of --repair-seed-manifest",
    )
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_DEFAULT,
                        help=f"Max concurrent workers (default {MAX_CONCURRENT_DEFAULT})")
    parser.add_argument("--max-concurrent-research", type=int, default=None,
                        help="Max concurrent research tasks matching label agent:agy-research")
    parser.add_argument("--research-concurrency", type=int, default=None,
                        help="Alias for --max-concurrent-research")
    parser.add_argument("--random-concurrency", action="store_true",
                        help="Randomize max-concurrent 1-3 at startup")
    parser.add_argument("--model", default=None,
                        help=f"Model override. Default = pool-aware router picks. "
                             f"Falls back to {DEFAULT_MODEL} when router is unavailable.")
    parser.add_argument("--jitter", default=f"{LAUNCH_JITTER_RANGE[0]}-{LAUNCH_JITTER_RANGE[1]}",
                        help=f"Launch jitter range in seconds (default {LAUNCH_JITTER_RANGE[0]}-{LAUNCH_JITTER_RANGE[1]})")
    parser.add_argument("--backoff", default=f"{COMPLETION_BACKOFF_RANGE[0]}-{COMPLETION_BACKOFF_RANGE[1]}",
                        help=f"Completion backoff range (default {COMPLETION_BACKOFF_RANGE[0]}-{COMPLETION_BACKOFF_RANGE[1]})")
    parser.add_argument("--watchdog", action="store_true",
                        help="Enable Linear polling during run (event-driven swap-in)")
    parser.add_argument("--watchdog-interval", type=int, default=LINEAR_POLL_INTERVAL,
                        help=f"Watchdog poll interval (default {LINEAR_POLL_INTERVAL}s)")
    parser.add_argument("--lane-mode", choices=["off", "auto"], default="off",
                        help="Enable lane-aware dispatch v2 (default off for compatibility)")
    parser.add_argument("--active-project", default="pwp",
                        help="Active project for project lane routing (default pwp)")
    parser.add_argument("--backlog-age-days", type=int, default=30,
                        help="Skip backlog issues older than N days unless explicitly ready/backlog (default 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve/fetch tasks and print lane assignment without launching AGY")
    parser.add_argument("--cron-mode", action="store_true",
                        help="Enable auto-resume safety gates for scheduled supervisor runs")
    parser.add_argument("--skip-agy-preflight", action="store_true",
                        help="Skip AGY backend preflight probe (debug/manual only)")
    parser.add_argument("--long-run", action="store_true",
                        help="Run continuously: idle_timeout becomes infinite, "
                             "supervisor stays alive forever until SIGTERM. "
                             "Replaces the cron-cycle exit pattern (Jun 30 fix).")
    return parser


def main():
    # Single-instance lock: prevent watchdog cascade. (Jul 1 2026)
    # The event_driven_watchdog.sh respawns this script every 5 min if it
    # dies, and previously 13 zombie supervisors piled up. The lock ensures
    # only one supervisor runs at a time.
    if not acquire_supervisor_lock():
        print(f"  [lock] another supervisor holds {SUPERVISOR_LOCK_PATH} — exiting", flush=True)
        sys.exit(0)
    atexit.register(release_supervisor_lock)
    # Note: --issue and --issues modes (one-shot dispatches) bypass the lock
    # so multiple ad-hoc invocations can still run in parallel.

    parser = build_argument_parser()
    args = parser.parse_args()
    if args.task_file is not None and (
        not args.issue or args.issues or args.from_linear
    ):
        parser.error("--task-file requires exclusive --issue mode")
    if args.linear_issue_uuid is not None and (
        not args.issue or args.issues or args.from_linear or args.task_file is not None
    ):
        parser.error("--linear-issue-uuid requires exclusive --issue mode without --task-file")
    if (args.repair_seed_manifest is None) != (args.repair_seed_sha256 is None):
        parser.error("--repair-seed-manifest and --repair-seed-sha256 are required together")
    if args.repair_seed_manifest is not None and (
        not args.issue or args.issues or args.from_linear
    ):
        parser.error("repair seed options require exclusive --issue mode")

    # Model selection: pool-aware router when in cron/long-run mode (Jun 30 2026).
    if args.model is None:
        try:
            router_path = Path(os.environ.get("AGY_POOL_ROUTER_PATH", str(Path.home() / ".hermes" / "profiles" / "orchestrator" / "scripts" / "agy_pool_aware_router.py")))
            if router_path.exists():
                model = subprocess.check_output(
                    ["python3", str(router_path), "model"],
                    timeout=5
                ).decode().strip()
                if model and model in (
                    "Gemini 3.5 Flash (Medium)", "Gemini 3.5 Flash (High)",
                    "gemini-3.5-flash-high", "gemini-3.5-flash",
                    "gemini-3.1-pro-high", "gemini-3.1-flash-lite",
                    # Anthropic strings (verified working in agent_dispatcher.py
                    # line 893, used in production Jun 28-29)
                    "claude-sonnet-4.6-thinking",
                    "claude-opus-4.6-thinking",
                ):
                    args.model = model
                else:
                    args.model = DEFAULT_MODEL
            else:
                args.model = DEFAULT_MODEL
        except Exception:
            args.model = DEFAULT_MODEL
    else:
        # If user explicitly set --model, propagate.
        pass
    if (args.cron_mode or args.long_run) and hasattr(args, 'model'):
        print(f"  [pool-router] selected model: {args.model}", flush=True)

    # Cron/auto mode is intentionally stricter than manual targeted waves.
    if args.cron_mode:
        # Long-run mode runs in the same single-account token ceiling as the
        # cron wrapper, but allows the operator-specified concurrency up to 3.
        if args.long_run:
            target = args.max_concurrent
            if target > 3:
                print(f"[auto-resume-gate] capping --long-run max-concurrent=3 (was {target})", flush=True)
                target = 3
            if args.max_concurrent != target:
                args.max_concurrent = target
        else:
            if args.max_concurrent != 3:
                print(f"[auto-resume-gate] enforcing cron max-concurrent=3 (was {args.max_concurrent})", flush=True)
                args.max_concurrent = 3
        args.jitter = "15-30"
        global AGY_INACTIVITY_KILL_SEC
        # Real Phase 2/4 build tasks need 10-15min of read-then-write time.
        # Bounded tasks finish in <5min. 900s (15min) ceiling handles both.
        if AGY_INACTIVITY_KILL_SEC < 600:
            print(f"[auto-resume-gate] enforcing inactivity-kill=900s (was {AGY_INACTIVITY_KILL_SEC})", flush=True)
            AGY_INACTIVITY_KILL_SEC = 900

    if args.research_concurrency is not None:
        args.max_concurrent_research = args.research_concurrency

    # Parse ranges
    jitter_lo, jitter_hi = (float(x) for x in args.jitter.split("-"))
    backoff_lo, backoff_hi = (float(x) for x in args.backoff.split("-"))
    launch_jitter_range = (jitter_lo, jitter_hi)
    backoff_range = (backoff_lo, backoff_hi)

    # Random concurrency if requested
    if args.random_concurrency:
        args.max_concurrent = random.randint(1, 3)
        print(f"  🎲 Random concurrency: this run will use {args.max_concurrent} workers")

    # Prepare dirs
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

    if args.cron_mode:
        if not run_auto_resume_gates(args.model, cron_mode=True, skip_agy_probe=args.skip_agy_preflight or args.dry_run):
            print("[auto-resume-gate] startup gates failed; aborting before worker spawn", flush=True)
            return

    # Build initial task list
    initial_tasks = []
    if args.from_linear:
        linear_issues = fetch_linear_issues(strict_opt_in=args.cron_mode)
        for li in linear_issues:
            task = issue_to_task(li, lane_mode=args.lane_mode, active_project=args.active_project, backlog_age_days=args.backlog_age_days)
            if task:
                initial_tasks.append(task)
    elif args.issue:
        task = resolve_exact_task(
            args.issue,
            task_file=args.task_file,
            linear_issue_uuid=args.linear_issue_uuid,
            workdir_override=args.workdir,
        )
        lane, _ = assign_lane(
            None,
            explicit=True,
            lane_mode=args.lane_mode,
            active_project=args.active_project,
            backlog_age_days=args.backlog_age_days,
        )
        task["lane"] = lane
        if args.repair_seed_manifest is not None:
            task["repair_seed_manifest"] = str(args.repair_seed_manifest.resolve())
            task["repair_seed_sha256"] = args.repair_seed_sha256.lower()
        print(
            f"  [task-source] {args.issue}: {task['task_source']} "
            f"sha256={task['task_sha256']}",
            flush=True,
        )
        initial_tasks.append(task)
    elif args.issues:
        for iid in args.issues.split(","):
            iid = iid.strip()
            task = resolve_exact_task(iid, workdir_override=args.workdir)
            lane, _ = assign_lane(
                None,
                explicit=True,
                lane_mode=args.lane_mode,
                active_project=args.active_project,
                backlog_age_days=args.backlog_age_days,
            )
            task["lane"] = lane
            print(
                f"  [task-source] {iid}: {task['task_source']} "
                f"sha256={task['task_sha256']}",
                flush=True,
            )
            initial_tasks.append(task)
    else:
        # Default: process all issue-batches/*.txt
        issue_batches = Path("/tmp/issue-batches")
        if issue_batches.exists():
            for f in sorted(issue_batches.glob("GRO-*.txt")):
                iid = f.stem
                content = f.read_text()
                wd = "prismatic"
                for line in content.split("\n")[:5]:
                    if line.startswith("WORKDIR:"):
                        wd = line.split(":", 1)[1].strip()
                        break
                labels = set()
                for line in content.split("\n")[:15]:
                    if line.startswith("LABELS:"):
                        labels = {p.strip() for p in line.split(":", 1)[1].split(",")}
                        break
                initial_tasks.append({"issue_id": iid, "task_content": content, "workdir": wd, "lane": "backlog" if args.lane_mode == "auto" else "default", "labels": labels})

    if args.dry_run:
        print("=" * 70)
        print("AGY SANDBOX SUPERVISOR — DRY RUN")
        print("=" * 70)
        print(f"  Lane mode:            {args.lane_mode}")
        print(f"  Active project:       {args.active_project}")
        print(f"  Max concurrent:       {args.max_concurrent}")
        print(f"  Lane caps:            {compute_lane_caps(args.max_concurrent, args.lane_mode)}")
        print(f"  Sandbox root:         {SANDBOX_ROOT}")
        for task in initial_tasks:
            print(f"  - {task['issue_id']} | lane={task.get('lane', 'default')} | score={task.get('priority_score', 0)} | workdir={task.get('workdir')}")
        return

    if not initial_tasks and not args.watchdog:
        print("No tasks to process. Use --issue, --issues, --from-linear, or --watchdog.")
        return

    # Banner
    print("=" * 70)
    print("AGY SANDBOX SUPERVISOR — EVENT-DRIVEN EDITION")
    print("=" * 70)
    print(f"  Initial tasks:        {len(initial_tasks)}")
    print(f"  Max concurrent:       {args.max_concurrent}")
    print(f"  Lane mode:            {args.lane_mode}")
    print(f"  Active project:       {args.active_project}")
    print(f"  Lane caps:            {compute_lane_caps(args.max_concurrent, args.lane_mode)}")
    print(f"  Launch jitter:        {launch_jitter_range[0]:.1f}-{launch_jitter_range[1]:.1f}s (random per launch)")
    print(f"  Completion backoff:   {backoff_range[0]:.1f}-{backoff_range[1]:.1f}s (random per completion)")
    print(f"  Worker startup:       {WORKER_STARTUP_STAGGER[0]:.1f}-{WORKER_STARTUP_STAGGER[1]:.1f}s (staggered)")
    print(f"  Model:                {args.model}")
    print(f"  Print timeout:        {PRINT_TIMEOUT}")
    print(f"  Watchdog:             {'enabled' if args.watchdog else 'disabled'}")
    print(f"  Watchdog interval:    {args.watchdog_interval}s")
    print(f"  Sandbox root:         {SANDBOX_ROOT}")
    print("=" * 70)
    print()

    # Token pool
    token_pool = TokenPool()
    pool_stats = token_pool.stats()
    print(f"  Token pool: {pool_stats['pool_size']} token(s) [{', '.join(pool_stats['tokens'])}]")
    if pool_stats["pool_size"] == 1:
        print(f"  ℹ️  Single-account mode: target concurrency = 3 (1-account ceiling)")
        print(f"     (Google-account-level cap, NOT a hardware limit. Event-driven swap-in"
              f" handles the ~1 timeout/cycle by retrying via watchdog.)")
    print()

    # Initialize supervisor
    supervisor = EventDrivenSupervisor(
        max_concurrent=args.max_concurrent,
        model=args.model,
        token_pool=token_pool,
        launch_jitter_range=launch_jitter_range,
        backoff_range=backoff_range,
        lane_mode=args.lane_mode,
        active_project=args.active_project,
        backlog_age_days=args.backlog_age_days,
        long_run=args.long_run,
        max_concurrent_research=args.max_concurrent_research,
    )

    # Self-register in the Prismatic service registry (GRO-3167, Jul 1 2026).
    # The watchdog reads the registry to detect liveness, and other services
    # (e.g., the gateway) can use it to discover the supervisor. Heartbeat
    # every 60s while running.
    try:
        supervisor.register_service()
    except Exception as e:
        print(f"  [registry] Self-registration failed: {e}", flush=True)

    # Push initial tasks BEFORE starting workers so lane-aware selection sees
    # the whole initial batch. If workers start first, a freshly-added backlog
    # task can be grabbed before later project/priority tasks are queued.
    for task in initial_tasks:
        supervisor.add_task(task)

    # Reconcile durable raw output before any worker can dispatch or publish.
    run_agy_raw_recovery_batch(lease_owner=f"startup-{os.getpid()}", limit=10)

    # Start workers after initial queue load.
    supervisor.start_workers()

    # Optionally start Linear watchdog
    watchdog_stop = threading.Event()
    watchdog_thread = None
    if args.watchdog:
        watchdog_thread = threading.Thread(
            target=linear_watchdog_loop,
            args=(supervisor, watchdog_stop, args.cron_mode),
            name="linear-watchdog",
            daemon=True,
        )
        watchdog_thread.start()

    # Event-driven bus subscriber (Jul 1 2026) — primary dispatch mechanism.
    # Replaces 120s polling with ~500ms event wake.
    bus_subscriber_thread = None
    if args.long_run:
        bus_subscriber_thread = threading.Thread(
            target=bus_event_subscriber_loop,
            args=(supervisor, watchdog_stop, DEFAULT_BUS_POLL_INTERVAL),
            name="bus-subscriber",
            daemon=True,
        )
        bus_subscriber_thread.start()
        print(f"  [event-driven] bus-subscriber thread started (canonical bus: {get_canonical_bus_path()})", flush=True)

    # Wait for all tasks to complete (or forever in --long-run mode)
    try:
        idle_timeout = 86400.0 if args.long_run else 120.0
        if args.long_run:
            print(f"  🔁 --long-run mode: supervisor will run until SIGTERM "
                  f"(idle timeout = {idle_timeout:.0f}s)", flush=True)

        # Install SIGTERM/SIGINT handlers (GRO-3166, Jul 1 2026):
        # Without these, SIGTERM kills the process mid-task → orphan AGY workers,
        # half-written RESULT.md, and inconsistent state. The handler sets
        # shutdown_event so the existing shutdown logic in wait_for_completion
        # and the worker loops can pick it up gracefully.
        import signal

        def _graceful_shutdown_signal_handler(signum, frame):
            signame = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
            print(
                f"\n  🛑 Received {signame} — initiating graceful shutdown (will "
                f"finish in-flight AGY tasks, terminate workers, save state)...",
                flush=True,
            )
            try:
                supervisor.shutdown()
            except Exception as e:
                print(f"  [shutdown] error during signal handler: {e}", flush=True)
            # Wake the main loop if it's idle-waiting
            try:
                supervisor.idle_event.set()
            except Exception:
                pass
            if watchdog_thread:
                try:
                    watchdog_stop.set()
                except Exception:
                    pass

        signal.signal(signal.SIGTERM, _graceful_shutdown_signal_handler)
        signal.signal(signal.SIGINT, _graceful_shutdown_signal_handler)
        print("  [signals] SIGTERM/SIGINT handlers installed for graceful shutdown", flush=True)

        supervisor.wait_for_completion(idle_timeout=idle_timeout, long_run=args.long_run)
    except KeyboardInterrupt:
        print("\n  🛑 Shutting down (Ctrl+C)...", flush=True)
        supervisor.shutdown()
        if watchdog_thread:
            watchdog_stop.set()
        return

    # Stop watchdog + bus subscriber
    if watchdog_thread:
        watchdog_stop.set()
    if bus_subscriber_thread:
        pass  # daemon thread, dies with the process
        watchdog_thread.join(timeout=5)

    # Summary
    # Note: a single run may match multiple flags (e.g. log contains both
    # "DONE:" and "timed out waiting for response"). DONE is a true positive;
    # the other buckets are mutually-exclusive failure modes that should not
    # double-count a Done session.
    print()
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    results = supervisor.results
    done = sum(1 for r in results if r.get("has_done"))
    non_done = [r for r in results if not r.get("has_done")]
    error = sum(1 for r in non_done if r.get("has_error"))
    inactivity_kill = sum(1 for r in non_done if r.get("has_inactivity_kill"))
    backend_timeout = sum(1 for r in non_done if r.get("has_backend_timeout"))
    partial_result = sum(1 for r in non_done if r.get("has_partial_result"))
    clarify = sum(1 for r in non_done if r.get("has_clarify"))
    missing_result = sum(1 for r in non_done if r.get("has_missing_result"))
    other = len(non_done) - error - inactivity_kill - backend_timeout - partial_result - clarify - missing_result
    print(f"  ✅ Done:                {done}/{len(results)}")
    print(f"  🔴 Error:               {error}/{len(results)}")
    print(f"  🛑 Inactivity kill:     {inactivity_kill}/{len(results)}")
    print(f"  ⏳ AGY backend timeout: {backend_timeout}/{len(results)}")
    print(f"  🟠 Partial result:      {partial_result}/{len(results)}")
    print(f"  ❓ Clarify:             {clarify}/{len(results)}")
    print(f"  🟡 MissingResult:       {missing_result}/{len(results)}")
    print(f"  🟠 Other:               {other}/{len(results)}")
    print(f"  Total time:   {sum(r.get('elapsed_sec', 0) for r in results)}s across {len(results)} session(s)")

    # Save status
    status_path = RESULTS_ROOT / f"event_supervisor_run_{int(time.time())}.json"
    status_path.write_text(json.dumps({
        "started_at": datetime.now().isoformat(),
        "model": args.model,
        "max_concurrent": args.max_concurrent,
        "launch_jitter_range": list(launch_jitter_range),
        "backoff_range": list(backoff_range),
        "token_pool": pool_stats,
        "watchdog_enabled": args.watchdog,
        "lane_mode": args.lane_mode,
        "active_project": args.active_project,
        "lane_caps": compute_lane_caps(args.max_concurrent, args.lane_mode),
        "circuit_tripped": supervisor.circuit_tripped,
        "consecutive_failures": supervisor.consecutive_failures,
        "cron_mode": args.cron_mode,
        "results": results,
    }, indent=2))
    print(f"\nStatus saved to: {status_path}")


if __name__ == "__main__":
    main()
