"""ADR-0002 merge-judge receipt validation.

Normative policy (``docs/decisions/ADR-0002-provider-neutral-verification-receipts.md``):

    "the merge judge independently validates the receipt at decision time."

:class:`ReceiptJudge` is the merge authority's independent check on the
candidate's verification receipt *before* it authorizes a merge. It is
deliberately separate from the executor's ``_build_ci_checks`` (which trusts
the manifest's claimed checks): the judge re-validates the stored receipt
itself, fail-closed, using the :mod:`prismatic.verification.receipt_validator`
and :mod:`prismatic.verification.attestation` primitives.

Exactly what ``validate`` checks, in order:

1. receipt id present and known to the store (fail closed on missing)
2. receipt is a dict (fail closed on malformed)
3. ``candidate_sha`` equals the expected head SHA — exact-head binding
   (ADR-0002: a head change invalidates the receipt); ``tree_sha`` when
   the caller supplies an expected tree
4. producer and verifier identities both present and different
   (fail closed on producer-only evidence)
5. receipt decision is a passing, merge-eligible decision
6. the receipt carries a non-empty attestation, cryptographically verified
   against the stored policy's verifier key records (fail closed on
   unsigned; any post-persist field tampering breaks the signature)
7. freshness against the stored policy's ``freshness.max_age_seconds``
   bound (constructor default is only the backstop for a malformed policy)
8. revocation status and revocation store

``validate`` never raises: any internal error becomes a refusal reason, so a
judge failure can never flip into an authorization.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from prismatic.verification.attestation import verify_receipt_attestation
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

            # Attestation: the receipt must carry a verifiable signature.
            # Unsigned receipts fail closed here even when the proof policy
            # does not require attestation — the merge path requires signed
            # evidence. Cryptographic verification against the stored
            # policy's verifier key records also closes post-persist DB
            # tampering: any field change breaks the signature.
            sig = receipt.get("signature_or_attestation")
            if not isinstance(sig, dict) or not sig.get("value"):
                return False, "missing_attestation"
            att_ok, att_reason = verify_receipt_attestation(receipt, stored.policy)
            if not att_ok:
                return False, f"attestation_failed: {att_reason}"

            # Freshness and revocation are the time-dependent checks — the
            # reason the judge re-validates at decision time instead of
            # trusting the persist-time verdict. The bound comes from the
            # stored proof policy (the constructor default is only the
            # backstop for a malformed policy).
            policy = stored.policy if isinstance(stored.policy, dict) else {}
            fresh_cfg = policy.get("freshness")
            bound = (
                fresh_cfg.get("max_age_seconds")
                if isinstance(fresh_cfg, dict)
                else None
            )
            if not (
                isinstance(bound, int)
                and not isinstance(bound, bool)
                and 0 < bound <= 31536000
            ):
                bound = self.max_age_seconds
            fresh, freshness_reason = validate_receipt_freshness(
                receipt, max_age_seconds=bound
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
