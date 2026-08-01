"""Linear Issue Transitioner for Deploy Hook (WB-4).

Corresponds to §5.3 and §16.8 of okf-docs-workspace-deploy-v1.md.
Extracts Linear issue IDs (GRO-XXXX) from commit/PR metadata and transitions them to 'Done' post-deploy.
Batched at max 10/minute to prevent Linear API rate limiting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GRO_ISSUE_REGEX = re.compile(r"\b(GRO-\d+)\b", re.IGNORECASE)
MAX_TRANSITIONS_PER_MINUTE = 10


def default_linear_transitions_db_path() -> Path:
    """Resolve JSON storage path for Linear transitions (~/.prismatic/db/linear_transitions.json)."""
    env_path = os.environ.get("PRISMATIC_LINEAR_TRANSITIONS_DB")
    if env_path:
        return Path(env_path).expanduser()
    p = Path("~/.prismatic/db/linear_transitions.json").expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class LinearTransitionsStore:
    """Durable file-backed persistence for Linear transitions idempotency and queues."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_linear_transitions_db_path()
        self._seen: set[str] = set()
        self._queued: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        if not self.db_path.exists():
            return
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._seen = set(data.get("seen_transitions", []))
                self._queued = data.get("queued_transitions", [])
        except Exception as exc:
            logger.warning("Failed to load Linear transitions DB from %s: %s", self.db_path, exc)

    def _save(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "seen_transitions": list(self._seen),
                        "queued_transitions": self._queued,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("Failed to save Linear transitions DB to %s: %s", self.db_path, exc)

    def is_seen(self, idempotency_key: str) -> bool:
        return idempotency_key in self._seen

    def mark_seen(self, idempotency_key: str) -> None:
        self._seen.add(idempotency_key)
        self._save()

    def get_queued(self) -> list[dict[str, str]]:
        return list(self._queued)

    def set_queued(self, queue: list[dict[str, str]]) -> None:
        self._queued = list(queue)
        self._save()


@dataclass
class LinearTransitionReceipt:
    """Canonical receipt for a Linear state transition."""

    issue_id: str  # e.g., "GRO-4188"
    from_state: str  # e.g., "In Review"
    to_state: str  # e.g., "Done"
    transition_at: str  # ISO8601
    deploy_id: str  # FK to DeployRecord
    idempotency_key: str  # sha256(issue_id + pr_sha + transition_at)
    linear_response: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LinearDeployTransitioner:
    """Extracts issue IDs from commit messages and transitions them to Done."""

    def __init__(self, dry_run: bool = False, store: LinearTransitionsStore | None = None):
        self.dry_run = dry_run
        self.store = store or LinearTransitionsStore()

    @classmethod
    def extract_issue_ids(cls, text: str) -> list[str]:
        """Extract unique GRO-XXXX issue identifiers from text."""
        if not text:
            return []
        matches = GRO_ISSUE_REGEX.findall(text)
        seen = set()
        result = []
        for m in matches:
            upper = m.upper()
            if upper not in seen:
                seen.add(upper)
                result.append(upper)
        return result

    def transition_issues_for_deploy(
        self,
        deploy_id: str,
        pr_sha: str,
        pr_title: str,
        commit_messages: list[str] | None = None,
    ) -> list[LinearTransitionReceipt]:
        """Extract and transition all associated Linear issues to Done.

        Enforces rate limiting (max 10 transitions per minute) and queues remainder for next deploy.
        Durable persistence via LinearTransitionsStore (~/.prismatic/db/linear_transitions.json).
        """
        combined_text = f"{pr_title}\n" + "\n".join(commit_messages or [])
        issue_ids = self.extract_issue_ids(combined_text)

        # Include previously queued transitions from durable store
        queued_to_process = self.store.get_queued()
        self.store.set_queued([])

        for q in queued_to_process:
            if q["issue_id"] not in issue_ids:
                issue_ids.append(q["issue_id"])

        receipts: list[LinearTransitionReceipt] = []
        if not issue_ids:
            return receipts

        # Batch at max 10 per minute
        batch = issue_ids[:MAX_TRANSITIONS_PER_MINUTE]
        remainder = issue_ids[MAX_TRANSITIONS_PER_MINUTE:]

        # Queue remaining issues in durable store for next deploy (Mitigation R4)
        if remainder:
            new_queue = [{"issue_id": issue_id, "deploy_id": deploy_id, "pr_sha": pr_sha} for issue_id in remainder]
            self.store.set_queued(new_queue)
            for issue_id in remainder:
                logger.info("Queued transition for %s to next deploy (rate limit cap 10/min)", issue_id)

        for issue_id in batch:
            receipt = self._transition_single_issue(issue_id, deploy_id, pr_sha)
            receipts.append(receipt)

        return receipts

    def _transition_single_issue(
        self,
        issue_id: str,
        deploy_id: str,
        pr_sha: str,
    ) -> LinearTransitionReceipt:
        """Transition a single issue to Done with durable idempotency check."""
        now_iso = datetime.now(timezone.utc).isoformat()[:10]  # Date component for canonical idempotency
        raw_key = f"{issue_id}:{pr_sha}:{now_iso}"
        idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

        # Idempotency check: dedupe repeated transitions for same issue + pr_sha using durable store
        if self.store.is_seen(idempotency_key):
            logger.info("Idempotent skip: %s already transitioned for %s (durable key check)", issue_id, pr_sha)
            return LinearTransitionReceipt(
                issue_id=issue_id,
                from_state="Done",
                to_state="Done",
                transition_at=datetime.now(timezone.utc).isoformat(),
                deploy_id=deploy_id,
                idempotency_key=idempotency_key,
                linear_response={"status": "idempotent_dedupe"},
                success=True,
            )

        self.store.mark_seen(idempotency_key)

        if self.dry_run:
            logger.info("DRY RUN: Linear transition %s → Done", issue_id)
            return LinearTransitionReceipt(
                issue_id=issue_id,
                from_state="In Review",
                to_state="Done",
                transition_at=datetime.now(timezone.utc).isoformat(),
                deploy_id=deploy_id,
                idempotency_key=idempotency_key,
                linear_response={"status": "dry_run"},
                success=True,
            )

        # Real Linear transition via linear_helpers if available
        try:
            from linear_helpers import update_issue_state  # type: ignore
            resp = update_issue_state(issue_id, state_name="Done")
            success = True
        except Exception as exc:
            resp = {"error": str(exc)}
            success = False

        return LinearTransitionReceipt(
            issue_id=issue_id,
            from_state="In Review",
            to_state="Done",
            transition_at=now_iso,
            deploy_id=deploy_id,
            idempotency_key=idempotency_key,
            linear_response=resp if isinstance(resp, dict) else {"result": str(resp)},
            success=success,
        )
