"""
SwarmProof Truth Oracle v0.3.0 Engine for Prismatic Engine.

Enforces:
1. AST Anti-Weakening & Zero Test Function Deletion checks.
2. Anti-Deception Invariants (observable execution proof & non-trivial assertions).
3. Multi-viewport Playwright visual audit compliance.
4. Cryptographic Proof Receipt generation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from prismatic.verification.ast_guard import ASTValidationResult, validate_ast_change

logger = logging.getLogger("prismatic.verification.swarmproof_oracle")


@dataclass
class SwarmProofReceipt:
    proof_id: str
    task_id: str
    candidate_sha: str
    oracle_version: str = "v0.3.0"
    valid: bool = True
    ast_passed: bool = True
    test_integrity_passed: bool = True
    visual_audit_passed: bool = True
    violations: list[str] = field(default_factory=list)
    ast_results: list[dict[str, Any]] = field(default_factory=list)
    evidence_digest: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "task_id": self.task_id,
            "candidate_sha": self.candidate_sha,
            "oracle_version": self.oracle_version,
            "valid": self.valid,
            "ast_passed": self.ast_passed,
            "test_integrity_passed": self.test_integrity_passed,
            "visual_audit_passed": self.visual_audit_passed,
            "violations": self.violations,
            "ast_results": self.ast_results,
            "evidence_digest": self.evidence_digest,
            "timestamp": self.timestamp,
        }


class SwarmProofOracle:
    """The central Truth Oracle enforcing mathematical & empirical verification."""

    @classmethod
    def verify_candidate(
        cls,
        *,
        task_id: str,
        candidate_sha: str,
        file_diffs: dict[str, dict[str, str]], # filename -> {"old": content, "new": content}
        visual_audit_result: dict[str, Any] | None = None,
        allow_reduction: bool = False,
    ) -> SwarmProofReceipt:
        """
        Execute full SwarmProof oracle verification over candidate file changes.
        """
        violations: list[str] = []
        ast_results: list[dict[str, Any]] = []
        ast_passed = True
        test_integrity_passed = True

        # 1. AST Anti-Weakening and Test Integrity Checks
        for filename, contents in file_diffs.items():
            old_code = contents.get("old", "")
            new_code = contents.get("new", "")
            
            res = validate_ast_change(
                old_code=old_code,
                new_code=new_code,
                filename=filename,
                allow_reduction=allow_reduction,
            )
            ast_results.append(res.to_dict())

            if not res.valid:
                ast_passed = False
                for v in res.violations:
                    violations.append(f"[{filename}] {v}")

            # Specific check for test reduction
            if res.new_test_count < res.old_test_count and not allow_reduction:
                test_integrity_passed = False
                violations.append(
                    f"[{filename}] TEST REDUCTION: Test count decreased from {res.old_test_count} to {res.new_test_count}."
                )

        # 2. Visual Audit Verification
        visual_passed = True
        if visual_audit_result is not None:
            if not visual_audit_result.get("passed", True):
                visual_passed = False
                violations.append(
                    f"VISUAL AUDIT REGRESSION: {visual_audit_result.get('error', 'Viewport layout shift detected')}"
                )

        overall_valid = ast_passed and test_integrity_passed and visual_passed

        # Compute tamper-evident evidence digest
        raw_manifest = json.dumps({
            "task_id": task_id,
            "candidate_sha": candidate_sha,
            "ast_passed": ast_passed,
            "test_integrity_passed": test_integrity_passed,
            "visual_passed": visual_passed,
            "violations": violations,
        }, sort_keys=True)
        evidence_digest = hashlib.sha256(raw_manifest.encode("utf-8")).hexdigest()

        proof_id = f"proof_{hashlib.sha256(f'{task_id}:{candidate_sha}:{evidence_digest}'.encode()).hexdigest()[:16]}"

        receipt = SwarmProofReceipt(
            proof_id=proof_id,
            task_id=task_id,
            candidate_sha=candidate_sha,
            valid=overall_valid,
            ast_passed=ast_passed,
            test_integrity_passed=test_integrity_passed,
            visual_audit_passed=visual_passed,
            violations=violations,
            ast_results=ast_results,
            evidence_digest=evidence_digest,
        )

        return receipt
