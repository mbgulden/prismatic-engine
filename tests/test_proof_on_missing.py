"""Fail-closed proof policy for missing file: targets (decision item 5).

Previously the kernel only ran SwarmProof when the file: target existed; a
missing or deleted target skipped verification entirely and the transaction
could commit — a real bypass. Now the barrier is fail closed by default
(proof_on_missing="deny"): a missing target fails verification and blocks
the commit, with the attempt recorded in the ledger as PROOF_TARGET_MISSING.
The "allow" override preserves the old skip for legitimate create-flows,
and the skip is still recorded so it stays auditable.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from prismatic.hypervisor import PrismaticHypervisor


def _make_hypervisor(tmpdir, **kwargs):
    kwargs.setdefault("mirror_to_gateway", False)
    return PrismaticHypervisor(
        journal_db_path=str(Path(tmpdir) / "journal.db"),
        ledger_db_path=str(Path(tmpdir) / "ledger.db"),
        **kwargs,
    )


def _span_payloads(hypervisor, span_id):
    return [n.payload for n in hypervisor.ledger.get_span_nodes(span_id)]


def test_missing_target_denies_commit_by_default():
    """A missing file: target fails the proof barrier; no commit is recorded."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            missing = Path(tmpdir) / "never_created.py"
            assert not missing.exists()

            with pytest.raises(RuntimeError, match="SwarmProof Invariant Failure"):
                async with hypervisor.transaction(
                    resource=f"file:{missing}", agent_id="proof_bot"
                ) as tx:
                    tx.register_step(
                        name="step_1",
                        forward_fn=lambda ctx: ("done", {"undo": "ok"}),
                        compensate_fn=lambda p: None,
                    )

            saga_record = hypervisor.journal.get_saga(tx.tx_id)
            assert saga_record["state"] == "ABORTED"

            payloads = _span_payloads(hypervisor, tx.span_id)
            # The denied bypass attempt is named in the audit trail.
            assert any(
                p.get("status") == "PROOF_TARGET_MISSING"
                and p.get("policy") == "deny"
                and p.get("target") == str(missing)
                for p in payloads
            )
            # No commit was ever recorded.
            assert not any(p.get("status") == "COMMITTED" for p in payloads)

    asyncio.run(_run())


def test_allow_override_permits_commit_and_records_skip():
    """proof_on_missing='allow' preserves the skip for create-flows, audited.

    The target is still missing at barrier time (the create happens outside
    the kernel's steps), so verification is skipped — explicitly and on the
    record — instead of failing closed.
    """

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir, proof_on_missing="allow")
            target = Path(tmpdir) / "to_be_created.py"
            assert not target.exists()

            async with hypervisor.transaction(
                resource=f"file:{target}", agent_id="create_bot"
            ) as tx:
                tx.register_step(
                    name="noop",
                    forward_fn=lambda ctx: ("done", {"undo": "ok"}),
                    compensate_fn=lambda p: None,
                )

            saga_record = hypervisor.journal.get_saga(tx.tx_id)
            assert saga_record["state"] == "COMMITTED"

            payloads = _span_payloads(hypervisor, tx.span_id)
            assert any(
                p.get("status") == "PROOF_TARGET_MISSING"
                and p.get("policy") == "allow"
                and p.get("target") == str(target)
                for p in payloads
            )
            assert any(p.get("status") == "COMMITTED" for p in payloads)

    asyncio.run(_run())


def test_deleted_before_verification_is_caught():
    """TOCTOU: a file deleted mid-transaction (before the barrier) is denied."""

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            hypervisor = _make_hypervisor(tmpdir)
            target = Path(tmpdir) / "doomed.py"
            target.write_text("def run(): return 1\n", encoding="utf-8")

            with pytest.raises(RuntimeError, match="SwarmProof Invariant Failure"):
                async with hypervisor.transaction(
                    resource=f"file:{target}", agent_id="toctou_bot"
                ) as tx:
                    tx.register_step(
                        name="delete_target",
                        forward_fn=lambda ctx: (target.unlink(), {"deleted": True}),
                        compensate_fn=lambda p: None,
                    )

            saga_record = hypervisor.journal.get_saga(tx.tx_id)
            assert saga_record["state"] == "ABORTED"

            payloads = _span_payloads(hypervisor, tx.span_id)
            assert any(
                p.get("status") == "PROOF_TARGET_MISSING"
                and p.get("policy") == "deny"
                for p in payloads
            )
            assert not any(p.get("status") == "COMMITTED" for p in payloads)

    asyncio.run(_run())


def test_invalid_policy_value_rejected():
    """Only 'deny' and 'allow' are accepted for proof_on_missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match="proof_on_missing"):
            _make_hypervisor(tmpdir, proof_on_missing="bogus")
