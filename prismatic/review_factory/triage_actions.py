"""Triage action executors — Phase 1b of the full-embedding plan.

The failure-triage classifier (``failure_triage.py``) decides; this module
ACTS. Each executor is additive, behind its own default-off env flag, and
fail-closed: any error becomes an audit row — never an exception, never a
partial action.

This chunk ships ONLY ``execute_retry`` (``PRISMATIC_TRIAGE_RETRY_ENABLED``).
``reject`` / ``escalate`` / ``repair`` follow as separate PRs after the
retry soak, each with its own flag.

``execute_retry`` re-runs the failed jobs of a workflow run via the GitHub
API (``rerun-failed-jobs``), exactly once per run:

- Deterministic verdicts only: it fires only when the triage result's
  ``deterministic_verdict`` is ``"retry"`` (known-transient rule or flake).
  Jev-advised retry stays shadow until Phase 2c — the executor refuses
  anything else.
- Idempotency is keyed on ``run_id``: a ``RetriedRunStore`` records every
  attempted retry, and the executor additionally refuses runs whose
  ``run_attempt`` is already > 1 (the rerun re-triggers the triage
  workflow on the same run id; without this guard it would loop forever).
- The workflow token needs ``actions: write`` for the rerun call; the
  shadow workflow keeps ``actions: read`` until Michael enables active
  mode, at which point the permission bump ships with the enablement.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ACTION_MARKER = "triage-action"
RETRY_ACTION = "retry"
RETRY_ENABLED_ENV = "PRISMATIC_TRIAGE_RETRY_ENABLED"
RETRY_STATE_ENV = "PRISMATIC_TRIAGE_STATE_DIR"
DEFAULT_RETRY_STATE_FILE = "triage-retry-state.json"

_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# Models (pure data)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetryRequest:
    """Everything ``execute_retry`` needs. Caller-supplied data only."""

    repo: str  # "owner/name"
    run_id: str  # GitHub Actions run id (digits)
    failure_id: str
    deterministic_verdict: Optional[str]  # must be "retry" to fire
    deterministic_evidence: str = ""


@dataclass(frozen=True)
class ActionResult:
    """What an executor did. ``executed=False`` is a skip, not a failure."""

    action: str
    executed: bool
    reason: str  # flag_off | not_deterministic_retry | already_retried |
    # run_attempt_gt_1 | invalid_run_id | gh_error | state_error | ok
    run_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ─────────────────────────────────────────────────────────────────────
# Retried-run store (idempotency, keyed on run_id)
# ─────────────────────────────────────────────────────────────────────


class RetryStateError(Exception):
    """The retry state file is unreadable — fail closed, retry nothing."""


class RetriedRunStore:
    """JSON file mapping run_id -> retry record.

    Fail-closed: a missing file is an empty store; a corrupt file raises
    ``RetryStateError`` so the executor refuses to act rather than risk a
    duplicate retry.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RetryStateError(f"unreadable retry state {self.path}: {exc}")
        if not isinstance(data, dict):
            raise RetryStateError(f"retry state {self.path} is not a JSON object")
        return data

    def has(self, run_id: str) -> bool:
        return str(run_id) in self._load()

    def record(self, run_id: str, entry: dict[str, Any]) -> None:
        data = self._load()
        data[str(run_id)] = entry
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            raise RetryStateError(f"cannot write retry state {self.path}: {exc}")


def default_retry_store() -> RetriedRunStore:
    base = Path(os.environ.get(RETRY_STATE_ENV, "."))
    return RetriedRunStore(base / DEFAULT_RETRY_STATE_FILE)


# ─────────────────────────────────────────────────────────────────────
# GitHub API (injectable for tests)
# ─────────────────────────────────────────────────────────────────────


def _default_gh(path: str, *, method: str = "GET") -> dict[str, Any]:
    """Call the GitHub API via the ``gh`` CLI. Raises RuntimeError on failure."""
    cmd = ["gh", "api", path] if method == "GET" else ["gh", "api", "-X", "POST", path]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=60
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"gh api {path} failed (exit {exc.returncode}): "
            f"{(exc.stderr or '').strip()[:500]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh api {path} timed out") from exc
    try:
        return json.loads(out) if out.strip() else {}
    except ValueError as exc:
        raise RuntimeError(f"gh api {path} returned non-JSON output") from exc


# ─────────────────────────────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────────────────────────────


def _emit_action_audit(audit_log: Path | str, row: dict[str, Any]) -> None:
    try:
        audit_log = Path(audit_log)
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        # Audit write failure must not change the action outcome.
        pass


def _action_row(
    *,
    action: str,
    outcome: str,
    request: RetryRequest,
    reason: str,
    details: Optional[dict[str, Any]] = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "ts": time.time(),
        "ts_iso": _utc_now_iso(),
        "component": "triage-actions",
        "action": f"{action}_{outcome}",  # retry_executed | retry_skipped | retry_failed
        "marker": ACTION_MARKER,
        "repo": request.repo,
        "run_id": request.run_id,
        "failure_id": request.failure_id,
        "deterministic_verdict": request.deterministic_verdict,
        "deterministic_evidence": request.deterministic_evidence,
        "reason": reason,
        "details": details or {},
        "error": error,
    }


# ─────────────────────────────────────────────────────────────────────
# execute_retry
# ─────────────────────────────────────────────────────────────────────


def execute_retry(
    request: RetryRequest,
    *,
    gh: Optional[Callable[..., dict[str, Any]]] = None,
    store: Optional[RetriedRunStore] = None,
    audit_log: Optional[Path | str] = None,
) -> ActionResult:
    """Rerun a run's failed jobs, once, for deterministic-transient failures.

    Never raises: every failure path returns ``ActionResult(executed=False)``
    with an audit row. Fail-closed throughout.
    """
    gh = gh or _default_gh
    audit_log = Path(audit_log) if audit_log is not None else None

    def audit(
        outcome: str, reason: str, details: dict | None = None, error: str = ""
    ) -> None:
        if audit_log is not None:
            _emit_action_audit(
                audit_log,
                _action_row(
                    action=RETRY_ACTION,
                    outcome=outcome,
                    request=request,
                    reason=reason,
                    details=details,
                    error=error,
                ),
            )

    def skip(reason: str, details: dict | None = None) -> ActionResult:
        audit("skipped", reason, details)
        return ActionResult(
            action=RETRY_ACTION,
            executed=False,
            reason=reason,
            run_id=request.run_id,
            details=details or {},
        )

    # 1. Flag gate — default off. The executor enforces this itself so a
    #    caller bug can never enable retries.
    if not _env_truthy(RETRY_ENABLED_ENV):
        return skip("flag_off")

    # 2. Deterministic verdicts only. Jev-advised retry (deterministic None,
    #    final "retry") stays shadow until Phase 2c.
    if request.deterministic_verdict != "retry":
        return skip(
            "not_deterministic_retry",
            {"deterministic_verdict": request.deterministic_verdict},
        )

    # 3. run_id hygiene — the id is interpolated into an API path.
    run_id = request.run_id.strip()
    if not run_id or not run_id.isdigit():
        return skip("invalid_run_id", {"run_id": request.run_id})

    # 4. Idempotency, keyed on run_id.
    store = store or default_retry_store()
    try:
        if store.has(run_id):
            return skip("already_retried")
    except RetryStateError as exc:
        result = ActionResult(
            action=RETRY_ACTION,
            executed=False,
            reason="state_error",
            run_id=run_id,
            error=str(exc)[:200],
        )
        audit("failed", "state_error", error=result.error)
        return result

    # 5. Loop guard: a rerun re-triggers the triage workflow on the SAME
    #    run id with run_attempt incremented. Never retry attempt > 1.
    try:
        run = gh(f"repos/{request.repo}/actions/runs/{run_id}")
        attempt = int(run.get("run_attempt", 1) or 1)
    except Exception as exc:
        result = ActionResult(
            action=RETRY_ACTION,
            executed=False,
            reason="gh_error",
            run_id=run_id,
            error=str(exc)[:200],
        )
        audit("failed", "gh_error", {"phase": "describe_run"}, result.error)
        return result
    if attempt > 1:
        try:
            store.record(
                run_id,
                {
                    "ts_iso": _utc_now_iso(),
                    "outcome": "skipped_run_attempt_gt_1",
                    "run_attempt": attempt,
                },
            )
        except RetryStateError:
            pass
        return skip("run_attempt_gt_1", {"run_attempt": attempt})

    # 6. The rerun itself — failed jobs only, never the whole workflow.
    try:
        gh(
            f"repos/{request.repo}/actions/runs/{run_id}/rerun-failed-jobs",
            method="POST",
        )
    except Exception as exc:
        result = ActionResult(
            action=RETRY_ACTION,
            executed=False,
            reason="gh_error",
            run_id=run_id,
            error=str(exc)[:200],
        )
        audit("failed", "gh_error", {"phase": "rerun_failed_jobs"}, result.error)
        return result

    # 7. Record before returning — a crash after the POST must not cause a
    #    duplicate retry.
    entry = {
        "ts_iso": _utc_now_iso(),
        "outcome": "rerun_requested",
        "run_attempt": attempt,
    }
    try:
        store.record(run_id, entry)
    except RetryStateError as exc:
        # The rerun already happened; the missing store record only means a
        # future pass would consult the run_attempt guard instead. Audit it.
        audit("executed", "ok_store_write_failed", {"store_error": str(exc)[:200]})
        return ActionResult(
            action=RETRY_ACTION,
            executed=True,
            reason="ok_store_write_failed",
            run_id=run_id,
            details=entry,
        )

    audit("executed", "ok", {"new_run_attempt_expected": attempt + 1})
    return ActionResult(
        action=RETRY_ACTION,
        executed=True,
        reason="ok",
        run_id=run_id,
        details=entry,
    )
