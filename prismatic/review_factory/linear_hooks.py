"""RF Linear hooks — wire the review loop into Linear (loud, auditable).

Reuses the existing Linear integration instead of rebuilding it:

- ``prismatic.providers.tasks.linear.LinearTaskProvider`` (GraphQL) for
  comments and issue creation.
- ``linear_helpers.update_issue_state`` — the deploy pipeline's established
  transition path (``pe/deploy/linear_transition.py``) — for moving merged
  work to Done, when importable.

Contract:

- Every hook records its outcome as an immutable audit entry on the review
  job — success AND failure. Nothing about Linear is ever silent.
- Hooks never raise into the pipeline. A Linear outage, a missing API key,
  or a malformed task id must not break deterministic review flow; the
  audit entry is the loud signal.
- ``notify_repair_required`` creates the Linear issue when the job has no
  linked issue yet, otherwise comments on the linked one.
- ``notify_merged`` transitions the linked issue to Done and comments the
  merge SHA.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_LINEAR_ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")

_REPAIR_LABEL = "review-factory:repair-required"


def linear_issue_ref(task_id: str | None) -> Optional[str]:
    """Return the Linear issue identifier when ``task_id`` looks like one."""
    task_id = (task_id or "").strip()
    if _LINEAR_ISSUE_RE.fullmatch(task_id):
        return task_id
    return None


@dataclass
class LinearHookResult:
    """Outcome of a Linear hook call (never raises)."""

    ok: bool
    action: str
    issue_ref: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class LinearReviewHooks:
    """Review Factory → Linear visibility hooks.

    ``db`` is a ``ReviewFactoryDB`` (for audit entries). ``provider_factory``
    defaults to ``LinearTaskProvider`` and exists so tests can inject a fake.
    """

    def __init__(
        self,
        db: Any = None,
        provider_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.db = db
        if provider_factory is None:

            def _default_factory() -> Any:
                from prismatic.providers.tasks.linear import LinearTaskProvider

                return LinearTaskProvider()

            provider_factory = _default_factory
        self.provider_factory = provider_factory

    # ── public hooks ─────────────────────────────────────────────

    def notify_repair_required(
        self,
        job: Any,
        *,
        failure_reason: str = "",
        intake_event_id: str = "",
        attempt: int = 1,
    ) -> LinearHookResult:
        """A job needs repair: create or update its Linear issue, loudly."""
        job_id = getattr(job, "review_job_id", "")
        provider = self._provider()
        if provider is None:
            self._audit_once(
                job_id,
                "linear_unconfigured",
                {
                    "hook": "notify_repair_required",
                    "note": "LINEAR_API_KEY not set; Linear update skipped",
                },
            )
            return LinearHookResult(
                ok=False, action="unconfigured", detail="LINEAR_API_KEY not set"
            )
        try:
            ref = linear_issue_ref(getattr(job, "task_id", ""))
            body = self._repair_body(job, failure_reason, intake_event_id, attempt)
            if ref:
                if not provider.add_comment(ref, body):
                    raise RuntimeError(f"add_comment returned false for {ref}")
                self._best_effort_label(provider, ref, _REPAIR_LABEL)
                self._audit(
                    job_id,
                    "linear_repair_notified",
                    {
                        "issue": ref,
                        "attempt": attempt,
                        "intake_event_id": intake_event_id,
                    },
                )
                return LinearHookResult(ok=True, action="commented", issue_ref=ref)
            created = self._create_repair_issue(provider, job, body)
            if created is None:
                raise RuntimeError("issue creation returned no issue")
            self._audit(
                job_id,
                "linear_issue_created",
                {
                    "issue": created.identifier,
                    "attempt": attempt,
                    "intake_event_id": intake_event_id,
                    "reason": "job had no linked Linear issue",
                },
            )
            return LinearHookResult(
                ok=True, action="created", issue_ref=created.identifier
            )
        except Exception as exc:
            logger.warning("linear repair notify failed for %s: %s", job_id, exc)
            self._audit(
                job_id,
                "linear_notify_failed",
                {
                    "hook": "notify_repair_required",
                    "attempt": attempt,
                    "error": str(exc)[:300],
                },
            )
            return LinearHookResult(ok=False, action="failed", detail=str(exc)[:300])

    def notify_merged(self, job: Any, *, merge_sha: str = "") -> LinearHookResult:
        """A job merged: transition its Linear issue to Done, loudly."""
        job_id = getattr(job, "review_job_id", "")
        ref = linear_issue_ref(getattr(job, "task_id", ""))
        if not ref:
            self._audit(
                job_id,
                "linear_no_issue",
                {
                    "hook": "notify_merged",
                    "note": "job has no linked Linear issue; nothing to transition",
                },
            )
            return LinearHookResult(ok=False, action="no_issue")
        provider = self._provider()
        if provider is None:
            self._audit_once(
                job_id,
                "linear_unconfigured",
                {
                    "hook": "notify_merged",
                    "note": "LINEAR_API_KEY not set; Linear transition skipped",
                },
            )
            return LinearHookResult(
                ok=False, action="unconfigured", detail="LINEAR_API_KEY not set"
            )
        try:
            transitioned, transition_detail = self._transition_to_done(ref)
            body = (
                f"Review Factory merged this issue's candidate "
                f"(`{(merge_sha or '')[:12] or 'n/a'}`) via job `{job_id}`.\n\n"
                f"Transition to Done: "
                f"{'succeeded' if transitioned else 'NOT applied — ' + transition_detail}"
            )
            if not provider.add_comment(ref, body):
                raise RuntimeError(f"add_comment returned false for {ref}")
            self._audit(
                job_id,
                "linear_merged_notified",
                {
                    "issue": ref,
                    "merge_sha": (merge_sha or "")[:12],
                    "transitioned": transitioned,
                    "transition_detail": transition_detail,
                },
            )
            return LinearHookResult(
                ok=True,
                action="transitioned" if transitioned else "commented",
                issue_ref=ref,
                detail=transition_detail,
            )
        except Exception as exc:
            logger.warning("linear merged notify failed for %s: %s", job_id, exc)
            self._audit(
                job_id,
                "linear_notify_failed",
                {"hook": "notify_merged", "error": str(exc)[:300]},
            )
            return LinearHookResult(ok=False, action="failed", detail=str(exc)[:300])

    def notify_repair_exhausted(self, job: Any, *, attempts: int) -> LinearHookResult:
        """Repair redispatch exhausted: terminal loud signal on the issue."""
        job_id = getattr(job, "review_job_id", "")
        provider = self._provider()
        if provider is None:
            self._audit_once(
                job_id,
                "linear_unconfigured",
                {
                    "hook": "notify_repair_exhausted",
                    "note": "LINEAR_API_KEY not set; exhaustion notice skipped",
                },
            )
            return LinearHookResult(
                ok=False, action="unconfigured", detail="LINEAR_API_KEY not set"
            )
        try:
            ref = linear_issue_ref(getattr(job, "task_id", ""))
            body = (
                f"Review Factory repair for job `{job_id}` FAILED LOUDLY: "
                f"{attempts} repair dispatch attempts exhausted with no repaired "
                f"candidate re-queued. Operator action required — this job will "
                f"not be retried automatically."
            )
            if ref:
                if not provider.add_comment(ref, body):
                    raise RuntimeError(f"add_comment returned false for {ref}")
                self._audit(
                    job_id,
                    "linear_exhaustion_notified",
                    {"issue": ref, "attempts": attempts},
                )
                return LinearHookResult(ok=True, action="commented", issue_ref=ref)
            self._audit(
                job_id,
                "linear_no_issue",
                {
                    "hook": "notify_repair_exhausted",
                    "attempts": attempts,
                    "note": "no linked Linear issue; exhaustion recorded in audit log only",
                },
            )
            return LinearHookResult(ok=False, action="no_issue")
        except Exception as exc:
            logger.warning("linear exhaustion notify failed for %s: %s", job_id, exc)
            self._audit(
                job_id,
                "linear_notify_failed",
                {"hook": "notify_repair_exhausted", "error": str(exc)[:300]},
            )
            return LinearHookResult(ok=False, action="failed", detail=str(exc)[:300])

    # ── internals ────────────────────────────────────────────────

    def _provider(self) -> Any | None:
        try:
            provider = self.provider_factory()
        except Exception as exc:
            logger.warning("linear provider construction failed: %s", exc)
            return None
        if not getattr(provider, "_api_key", ""):
            return None
        return provider

    def _audit(self, job_id: str, action: str, details: dict[str, Any]) -> None:
        if self.db is None or not job_id:
            return
        try:
            self.db.insert_audit_entry(
                actor="review-factory:linear-hooks",
                action=action,
                review_job_id=job_id,
                details=details,
            )
        except Exception as exc:
            logger.warning("linear hook audit write failed: %s", exc)

    def _audit_once(self, job_id: str, action: str, details: dict[str, Any]) -> None:
        """Audit noisily but once per job+action (no per-loop spam)."""
        if self.db is None or not job_id:
            return
        try:
            if self.db.find_audit_entry(job_id, action) is not None:
                return
        except Exception:
            pass
        self._audit(job_id, action, details)

    @staticmethod
    def _repair_body(
        job: Any, failure_reason: str, intake_event_id: str, attempt: int
    ) -> str:
        candidate = (getattr(job, "candidate_commit", "") or "")[:12]
        return (
            f"Review Factory: candidate `{candidate}` for this issue needs repair "
            f"(attempt {attempt}).\n\n"
            f"Reason: {failure_reason or 'see review findings'}\n"
            f"Repair task: `{intake_event_id or 'n/a'}` (channel review-factory)\n"
            f"Review job: `{getattr(job, 'review_job_id', '')}`"
        )

    def _create_repair_issue(self, provider: Any, job: Any, body: str) -> Any | None:
        team_id = os.environ.get("LINEAR_TEAM_ID", "").strip()
        if not team_id:
            raise RuntimeError("LINEAR_TEAM_ID not set; cannot create issue")
        title = (
            f"RF repair: {(getattr(job, 'review_job_id', '') or '')[:8]} needs rework"
        )
        description = (
            f"{body}\n\nRepository: {getattr(job, 'repository', '') or 'n/a'}\n"
            f"Risk tier: {getattr(job, 'risk_tier', '')}"
        )
        return provider.create_issue(
            team_id=team_id,
            title=title,
            description=description,
            label_names=[_REPAIR_LABEL],
        )

    def _best_effort_label(
        self, provider: Any, issue_ref: str, label_name: str
    ) -> None:
        """Attach a label without ever failing the hook."""
        try:
            issue = provider.get_issue(issue_ref)
            if issue is None:
                return
            names = list(getattr(issue, "labels", []) or [])
            if label_name in names:
                return
            label_id = self._resolve_label_id(provider, label_name)
            if not label_id:
                return
            # set_labels replaces; preserve existing by re-resolving names.
            ids = [label_id]
            for name in names:
                resolved = self._resolve_label_id(provider, name)
                if resolved and resolved not in ids:
                    ids.append(resolved)
            provider.set_labels(issue_ref, ids)
        except Exception as exc:
            logger.debug("linear label attach skipped for %s: %s", issue_ref, exc)

    def _resolve_label_id(self, provider: Any, label_name: str) -> Optional[str]:
        try:
            get_labels = getattr(provider, "get_label_id", None)
            if callable(get_labels):
                return get_labels(label_name)
        except Exception:
            pass
        return None

    @staticmethod
    def _transition_to_done(issue_ref: str) -> tuple[bool, str]:
        """Transition an issue to Done via the deploy pipeline's path.

        Reuses ``linear_helpers.update_issue_state`` exactly the way
        ``pe/deploy/linear_transition.py`` does. Returns (ok, detail).
        """
        try:
            from linear_helpers import update_issue_state  # type: ignore
        except Exception as exc:
            return False, f"linear_helpers unavailable: {exc}"
        try:
            resp = update_issue_state(issue_ref, state_name="Done")
            if isinstance(resp, dict) and resp.get("error"):
                return False, str(resp["error"])[:200]
            return True, str(resp)[:200]
        except Exception as exc:
            return False, str(exc)[:200]
