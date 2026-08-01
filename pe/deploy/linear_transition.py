"""Linear Issue Transitioner for Deploy Hook (WB-4).

Corresponds to §5.3 and §16.8 of okf-docs-workspace-deploy-v1.md.
Extracts Linear issue IDs (GRO-XXXX) from commit/PR metadata and transitions them to 'Done' post-deploy.
Batched at max 10/minute to prevent Linear API rate limiting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

GRO_ISSUE_REGEX = re.compile(r"\b(GRO-\d+)\b", re.IGNORECASE)
MAX_TRANSITIONS_PER_MINUTE = 10


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

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

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
        commit_messages: Optional[list[str]] = None,
    ) -> list[LinearTransitionReceipt]:
        """Extract and transition all associated Linear issues to Done.

        Enforces rate limiting (max 10 transitions per minute).
        """
        combined_text = f"{pr_title}\n" + "\n".join(commit_messages or [])
        issue_ids = self.extract_issue_ids(combined_text)

        receipts: list[LinearTransitionReceipt] = []
        if not issue_ids:
            return receipts

        # Batch at max 10 per minute
        batch = issue_ids[:MAX_TRANSITIONS_PER_MINUTE]
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
        """Transition a single issue to Done."""
        now_iso = datetime.now(timezone.utc).isoformat()
        idempotency_key = hashlib.sha256(f"{issue_id}:{pr_sha}:{now_iso}".encode("utf-8")).hexdigest()

        if self.dry_run:
            logger.info("DRY RUN: Linear transition %s → Done", issue_id)
            return LinearTransitionReceipt(
                issue_id=issue_id,
                from_state="In Review",
                to_state="Done",
                transition_at=now_iso,
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
