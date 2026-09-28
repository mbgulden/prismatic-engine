"""ADR-0002 merge-judge receipt validation.

Normative policy (``docs/decisions/ADR-0002-provider-neutral-verification-receipts.md``):

    "the merge judge independently validates the receipt at decision time."

:class:`ReceiptJudge` is the merge authority's independent check on the
candidate's verification receipt *before* it authorizes a merge. It is
deliberately separate from the executor's ``_build_ci_checks`` (which trusts
the manifest's claimed checks): the judge re-validates the stored receipt
itself, fail-closed, using the :mod:`prismatic.verification.receipt_validator`
primitives:

- missing receipt id / unknown receipt id -> refuse (fail closed on missing)
- receipt not bound to the exact head SHA -> refuse (ADR-0002 exact-head rule)
- producer/verifier separation violated -> refuse (fail closed on
  producer-only evidence)
- receipt decision not ``pass`` / not merge-eligible -> refuse
- stale (freshness) or expired -> refuse
- revoked -> refuse
- malformed receipt -> refuse (the validator primitives are non-raising and
  fail-closed)

``validate`` never raises: any internal error becomes a refusal reason, so a
judge failure can never flip into an authorization.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from prismatic.verification.receipt_store import VerificationReceiptStore
from prismatic.verification.receipt_validator import (
    check_revocation,
    validate_receipt_freshness,
)

logger = logging.getLogger(__name__)

#: Default freshness bound for the judge's independent validation.
DEFAULT_MAX_AGE_SECONDS = 3600


class ReceiptJudge:
    """Independent receipt validation for the merge judge (ADR-0002)."""

    def __init__(
        self,
        store: Optional[VerificationReceiptStore] = None,
        *,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        revocation_store: Path | str | None = None,
    ):
        self.store = store or VerificationReceiptStore()
        self.max_age_seconds = max_age_seconds
        self.revocation_store = revocation_store

    def validate(
        self,
        *,
        receipt_id: str,
        expected_candidate_sha: str,
        expected_tree_sha: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Independently validate a stored verification receipt.

        Returns ``(True, None)`` when the receipt authorizes the merge
        decision, else ``(False, reason)``. Never raises.
        """
        try:
            if not receipt_id or not str(receipt_id).strip():
                return False, "receipt_missing"
            try:
                stored = self.store.get(str(receipt_id))
            except KeyError:
                return False, "receipt_not_found"
            except Exception as exc:
                return False, f"receipt_store_error: {exc}"

            receipt = stored.receipt
            if not isinstance(receipt, dict):
                return False, "malformed_receipt"

            # Exact-head binding (ADR-0002): a head change invalidates the receipt.
            candidate_sha = receipt.get("candidate_sha")
            if not candidate_sha or candidate_sha != expected_candidate_sha:
                return False, "candidate_sha_mismatch"
            if expected_tree_sha is not None:
                tree_sha = receipt.get("tree_sha")
                if not tree_sha or tree_sha != expected_tree_sha:
                    return False, "tree_sha_mismatch"

            # Producer/verifier separation: producer-only evidence fails closed.
            producer_id = receipt.get("producer_id")
            verifier_id = receipt.get("verifier_id")
            if not producer_id or not verifier_id:
                return False, "producer_or_verifier_identity_missing"
            if producer_id == verifier_id:
                return False, "producer_verifier_separation_failed"

            # The receipt's own decision must be a passing, merge-eligible one.
            decision = receipt.get("decision")
            if not isinstance(decision, dict):
                return False, "invalid_receipt_decision"
            if decision.get("status") != "pass":
                return False, f"decision_status_{decision.get('status')}"
            if decision.get("merge_eligible") is not True:
                return False, "decision_not_merge_eligible"

            # Freshness and revocation are the time-dependent checks — the
            # reason the judge re-validates at decision time instead of
            # trusting the persist-time verdict.
            fresh, freshness_reason = validate_receipt_freshness(
                receipt, max_age_seconds=self.max_age_seconds
            )
            if not fresh:
                return False, f"freshness_failed: {freshness_reason}"
            not_revoked, revocation_reason = check_revocation(
                receipt, revocation_store=self.revocation_store
            )
            if not not_revoked:
                return False, f"revocation_failed: {revocation_reason}"

            return True, None
        except Exception as exc:  # fail-closed: a judge error is a refusal
            logger.warning("receipt judge validation error", exc_info=True)
            return False, f"receipt_validation_error: {exc}"


__all__ = ["ReceiptJudge", "DEFAULT_MAX_AGE_SECONDS"]
