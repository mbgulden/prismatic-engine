"""RF-2: Deterministic Verification Worker — Deep PE Integration.

Produces ``VerificationEvidence`` objects (from ``merge_candidate_manifest``)
and advances the manifest from ``CANDIDATE`` → ``REVIEW_REQUIRED`` via
``manifest.request_review(evidence_list)``.

Three integrity invariants (baked in, not bolt-on):
    1. **Integration Import Invariant** — grep for expected PE imports.
       If the module is supposed to integrate with a PE surface, the
       import MUST be present. A missing import means stub code.
    2. **Circular Proof Detection** — verify tests import external PE
       modules. If tests only import from the module under test, they
       validate the stub against itself (hollow green).
    3. **Call-site Check** — verify the imported function is actually
       called. Import-but-don't-use is a subtler hollow pattern.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    VerificationEvidence,
)
from prismatic.review_factory.models import (
    ReviewJob,
    VerificationReceipt,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Proof class requirements per risk tier
# ─────────────────────────────────────────────────────────────────────

_PROOF_REQUIREMENTS: dict[str, set[str]] = {
    "A": {"focused"},
    "B": {"focused", "canonical", "package"},
    "C": {"focused", "canonical", "package", "failure", "recovery", "rollback"},
}
_DASHBOARD_EXTRA = {"browser", "real_data"}


# ─────────────────────────────────────────────────────────────────────
# Integration Import Contracts (Invariant 1)
# ─────────────────────────────────────────────────────────────────────

INTEGRATION_IMPORT_CONTRACTS: dict[str, list[str]] = {
    "prismatic/review_factory/verifier.py": [
        "from prismatic.merge_candidate_manifest import",
    ],
    "prismatic/review_factory/reviewer.py": [
        "from prismatic.review import",
        "from prismatic.merge_candidate_manifest import",
    ],
    "prismatic/review_factory/merge_executor.py": [
        "from prismatic.integrate import",
        "from prismatic.merge_candidate_manifest import",
        "from prismatic.state_machine import",
    ],
    "prismatic/review_factory/backlog_importer.py": [
        "from prismatic.agy_completed_work import",
    ],
    "prismatic/review_factory/routes.py": [
        "from prismatic.review_factory.db import",
        "from prismatic.review_factory.queue import",
    ],
}

# ─────────────────────────────────────────────────────────────────────
# Integration Call-Site Contracts (Invariant 3)
# ─────────────────────────────────────────────────────────────────────

INTEGRATION_CALLSITE_CONTRACTS: dict[str, list[str]] = {
    "prismatic/review_factory/verifier.py": [
        "request_review",
    ],
    "prismatic/review_factory/reviewer.py": [
        "review_pr",
        "record_review",
    ],
    "prismatic/review_factory/merge_executor.py": [
        "integrate_pipeline_run",
        "mark_merged",
    ],
    "prismatic/review_factory/backlog_importer.py": [
        "AgyCompletedWorkStore",
    ],
}


@dataclass
class CheckResult:
    """Outcome of a single verification check."""

    name: str
    proof_class: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    log_path: str = ""
    log_sha256: str = ""
    passed: bool = True


class VerificationWorker:
    """RF-2: Run deterministic verification and produce VerificationEvidence."""

    def __init__(
        self,
        repo_path: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        test_mode: bool = False,
    ):
        self.repo_path = repo_path or Path(".")
        self.log_dir = log_dir or Path(tempfile.mkdtemp(prefix="rf-verify-"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.test_mode = test_mode

    def verify(
        self,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
    ) -> tuple[VerificationReceipt, MergeCandidateManifest]:
        """Run verification and advance the manifest."""
        tier_str = (
            manifest.risk_tier.value
            if hasattr(manifest.risk_tier, "value")
            else str(manifest.risk_tier)
        )
        required = set(_PROOF_REQUIREMENTS.get(tier_str, {"focused"}))
        if manifest.dashboard_change:
            required |= _DASHBOARD_EXTRA

        results: list[CheckResult] = []
        for proof_class in sorted(required):
            result = self._run_proof_class(proof_class, job, manifest)
            results.append(result)

        integrity_results = self._run_integrity_invariants(job)
        results.extend(integrity_results)

        non_claims = self._compute_non_claims(job, results)

        by_proof_class: dict[str, list[CheckResult]] = {}
        for r in results:
            by_proof_class.setdefault(r.proof_class, []).append(r)

        evidence_list: list[VerificationEvidence] = []
        for pclass, p_results in by_proof_class.items():
            if all(r.passed for r in p_results):
                primary = p_results[0]
                summary = (
                    self._build_summary(primary)
                    if len(p_results) == 1
                    else f"{pclass}: {len(p_results)} checks passed"
                )
                evidence = VerificationEvidence(
                    proof_class=pclass,
                    command=primary.command or f"rf-verify-{pclass}",
                    summary=summary,
                    result="PASS",
                    log_path=primary.log_path or str(self.log_dir / f"{pclass}.log"),
                    log_sha256=primary.log_sha256 or self._compute_log_hash(primary),
                )
                evidence_list.append(evidence)

        updated_manifest = manifest.request_review(evidence_list)

        invariance_proof = self._compute_invariance_proof(list(manifest.changed_paths))
        receipt = VerificationReceipt(
            review_job_id=job.review_job_id,
            candidate_commit=job.candidate_commit,
            candidate_tree=job.candidate_tree or job.candidate_commit,
            immutable_archive_id=f"archive-tree-{job.candidate_tree or job.candidate_commit}",
            commands=json.dumps([r.command for r in results]),
            exit_codes=json.dumps({r.name: r.exit_code for r in results}),
            log_paths=json.dumps({r.name: r.log_path for r in results}),
            log_sha256=json.dumps({r.name: r.log_sha256 for r in results}),
            classification=self._classify_verification(results),
            changed_path_invariance_proof=invariance_proof,
            explicit_non_claims=json.dumps(non_claims),
            baseline_failures=json.dumps(self._compute_baseline_failures(results)),
        )

        return receipt, updated_manifest

    def _run_integrity_invariants(self, job: ReviewJob) -> list[CheckResult]:
        results: list[CheckResult] = []
        changed = json.loads(job.changed_paths_json) if job.changed_paths_json else []

        for path in changed:
            if not path.endswith(".py"):
                continue

            import_contract = INTEGRATION_IMPORT_CONTRACTS.get(path, [])
            if import_contract:
                result = self._check_integration_imports(path, import_contract)
                results.append(result)

            if "/test_" in path or path.startswith("test_"):
                result = self._check_circular_proof(path)
                results.append(result)
            else:
                test_path = self._derive_test_path(path)
                if test_path and (self.repo_path / test_path).exists():
                    result = self._check_circular_proof(test_path)
                    results.append(result)

            callsite_contract = INTEGRATION_CALLSITE_CONTRACTS.get(path, [])
            if callsite_contract:
                result = self._check_callsites(path, callsite_contract)
                results.append(result)

        return results

    def _check_integration_imports(
        self, path: str, expected_imports: list[str]
    ) -> CheckResult:
        full_path = self.repo_path / path
        if not full_path.exists():
            return CheckResult(
                name=f"integration-import:{Path(path).stem}",
                proof_class="focused",
                command=f"test -f {path}",
                exit_code=1,
                stderr=f"File {path} not found in repo_path — check failed",
                passed=False,
            )

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult(
                name=f"integration-import:{Path(path).stem}",
                proof_class="focused",
                command=f"cat {path}",
                exit_code=-2,
                stderr=str(exc),
                passed=False,
            )

        missing = [imp for imp in expected_imports if imp not in content]

        if missing:
            return CheckResult(
                name=f"integration-import:{Path(path).stem}",
                proof_class="focused",
                command=f"grep '{missing[0]}' {path}",
                exit_code=1,
                stderr=(
                    f"Integration contract violation: missing expected import '{missing[0]}' in {path}."
                ),
                passed=False,
            )

        return CheckResult(
            name=f"integration-import:{Path(path).stem}",
            proof_class="focused",
            command=f"grep '{expected_imports[0]}' {path}",
            exit_code=0,
            stdout=f"All {len(expected_imports)} expected integration imports present",
            passed=True,
        )

    def _check_circular_proof(self, test_path: str) -> CheckResult:
        full_path = self.repo_path / test_path
        if not full_path.exists():
            return CheckResult(
                name=f"circular-proof:{Path(test_path).stem}",
                proof_class="focused",
                command=f"test -f {test_path}",
                exit_code=1,
                stderr=f"Test file {test_path} not found in repo_path — check failed",
                passed=False,
            )

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult(
                name=f"circular-proof:{Path(test_path).stem}",
                proof_class="focused",
                command=f"cat {test_path}",
                exit_code=-2,
                stderr=str(exc),
                passed=False,
            )

        external_pe_patterns = [
            r"from prismatic\.merge_candidate_manifest\b",
            r"from prismatic\.review\b",
            r"from prismatic\.integrate\b",
            r"from prismatic\.state_machine\b",
            r"from prismatic\.agy_completed_work\b",
        ]

        has_external = any(re.search(pat, content) for pat in external_pe_patterns)

        if not has_external:
            return CheckResult(
                name=f"circular-proof:{Path(test_path).stem}",
                proof_class="focused",
                command=f"grep PE-surface-import {test_path}",
                exit_code=1,
                stderr=(
                    f"Circular proof detected in {test_path}: tests only import local module."
                ),
                passed=False,
            )

        return CheckResult(
            name=f"circular-proof:{Path(test_path).stem}",
            proof_class="focused",
            command=f"grep PE-surface-import {test_path}",
            exit_code=0,
            stdout="Test imports external PE modules — not circular",
            passed=True,
        )

    def _check_callsites(self, path: str, expected_calls: list[str]) -> CheckResult:
        full_path = self.repo_path / path
        if not full_path.exists():
            return CheckResult(
                name=f"callsite:{Path(path).stem}",
                proof_class="focused",
                command=f"test -f {path}",
                exit_code=1,
                stderr=f"File {path} not found in repo_path — check failed",
                passed=False,
            )

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult(
                name=f"callsite:{Path(path).stem}",
                proof_class="focused",
                command=f"cat {path}",
                exit_code=-2,
                stderr=str(exc),
                passed=False,
            )

        missing = [fn for fn in expected_calls if fn not in content]

        if missing:
            return CheckResult(
                name=f"callsite:{Path(path).stem}",
                proof_class="focused",
                command=f"grep '{missing[0]}' {path}",
                exit_code=1,
                stderr=(
                    f"Integration contract violation: imports PE surface but never calls: {', '.join(missing)}."
                ),
                passed=False,
            )

        return CheckResult(
            name=f"callsite:{Path(path).stem}",
            proof_class="focused",
            command=f"grep '{expected_calls[0]}' {path}",
            exit_code=0,
            stdout=f"All {len(expected_calls)} expected call sites found",
            passed=True,
        )

    def _run_proof_class(
        self,
        proof_class: str,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
    ) -> CheckResult:
        if self.test_mode:
            return CheckResult(
                name=proof_class,
                proof_class=proof_class,
                command=f"test-stub-{proof_class}",
                exit_code=0,
                stdout=f"test_mode: {proof_class} passed",
                passed=True,
            )

        if proof_class == "focused":
            return self._run_focused_check(job)
        elif proof_class == "canonical":
            return self._run_canonical_check()
        elif proof_class == "package":
            return self._run_package_check()
        else:
            # Manual or unsupported proof class -> fail closed
            return CheckResult(
                name=proof_class,
                proof_class=proof_class,
                command=f"rf-verify-{proof_class}",
                exit_code=1,
                stderr=f"Proof class '{proof_class}' requires explicit automated verification runner — fail closed",
                passed=False,
            )

    def _run_focused_check(self, job: ReviewJob) -> CheckResult:
        changed = json.loads(job.changed_paths_json) if job.changed_paths_json else []
        if not changed:
            return CheckResult(
                name="focused",
                proof_class="focused",
                command="rf-verify-focused",
                exit_code=1,
                stderr="No changed paths provided for focused check",
                passed=False,
            )

        test_patterns = []
        for path in changed:
            if "/test_" in path or path.startswith("test_"):
                test_patterns.append(path)
            else:
                base = Path(path).stem
                test_patterns.append(f"**/test_{base}.py")

        if not test_patterns:
            return CheckResult(
                name="focused",
                proof_class="focused",
                command="rf-verify-focused",
                exit_code=1,
                stderr="No test patterns derived from changed paths",
                passed=False,
            )

        args = [
            sys.executable,
            "-m",
            "pytest",
            *test_patterns[:5],
            "-x",
            "-q",
            "--no-header",
        ]
        return self._execute_subproc("focused", "focused", args)

    def _run_canonical_check(self) -> CheckResult:
        tests_dir = self.repo_path / "tests"
        if not tests_dir.exists():
            return CheckResult(
                name="canonical",
                proof_class="canonical",
                command="rf-verify-canonical",
                exit_code=1,
                stderr="Canonical tests directory tests/ not found in repo_path",
                passed=False,
            )

        args = [sys.executable, "-m", "pytest", "tests/", "-x", "-q", "--no-header"]
        return self._execute_subproc("canonical", "canonical", args)

    def _run_package_check(self) -> CheckResult:
        init_file = self.repo_path / "prismatic" / "__init__.py"
        if not init_file.exists():
            return CheckResult(
                name="package",
                proof_class="package",
                command="rf-verify-package",
                exit_code=1,
                stderr="Package entrypoint prismatic/__init__.py not found",
                passed=False,
            )

        args = [sys.executable, "-m", "py_compile", "prismatic/__init__.py"]
        return self._execute_subproc("package", "package", args)

    def _execute_subproc(
        self, name: str, proof_class: str, cmd_args: list[str]
    ) -> CheckResult:
        command_str = " ".join(cmd_args)
        log_path = str(self.log_dir / f"{name}.log")

        try:
            proc = subprocess.run(
                cmd_args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.repo_path),
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
            passed = exit_code == 0
        except subprocess.TimeoutExpired:
            exit_code = -1
            stdout = ""
            stderr = "Timeout: check exceeded 300s"
            passed = False
        except Exception as exc:
            exit_code = -2
            stdout = ""
            stderr = str(exc)
            passed = False

        log_content = f"=== {name} ({proof_class}) ===\n"
        log_content += f"Command: {command_str}\n"
        log_content += f"Exit code: {exit_code}\n"
        log_content += f"--- stdout ---\n{stdout}\n"
        log_content += f"--- stderr ---\n{stderr}\n"

        try:
            Path(log_path).write_text(log_content, encoding="utf-8")
        except OSError:
            pass

        log_sha = hashlib.sha256(log_content.encode("utf-8")).hexdigest()

        return CheckResult(
            name=name,
            proof_class=proof_class,
            command=command_str,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            log_path=log_path,
            log_sha256=log_sha,
            passed=passed,
        )

    @staticmethod
    def _derive_test_path(source_path: str) -> Optional[str]:
        p = Path(source_path)
        test_dir = p.parent / "tests"
        test_file = f"test_{p.stem}.py"
        return str(test_dir / test_file)

    @staticmethod
    def _build_summary(result: CheckResult) -> str:
        if result.passed:
            return f"{result.proof_class}: passed (exit {result.exit_code})"
        return f"{result.proof_class}: FAILED (exit {result.exit_code}) — {result.stderr[:200]}"

    @staticmethod
    def _compute_log_hash(result: CheckResult) -> str:
        content = f"{result.command}\n{result.stdout}\n{result.stderr}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_invariance_proof(changed_paths: list[str]) -> str:
        canonical = json.dumps(sorted(changed_paths), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _classify_verification(results: list[CheckResult]) -> str:
        classes = {r.proof_class for r in results}
        if "canonical" in classes:
            return "canonical_suite"
        if "focused" in classes:
            return "targeted"
        return "bounded_regression"

    @staticmethod
    def _compute_non_claims(job: ReviewJob, results: list[CheckResult]) -> list[str]:
        non_claims = []
        for r in results:
            if not r.passed and r.name.startswith(
                ("integration-import:", "circular-proof:", "callsite:")
            ):
                non_claims.append(r.stderr)

        failed = [
            r
            for r in results
            if not r.passed
            and not r.name.startswith(
                ("integration-import:", "circular-proof:", "callsite:")
            )
        ]
        if failed:
            non_claims.append(
                f"The following checks failed: {', '.join(r.name for r in failed)}"
            )
        non_claims.append("Integration and end-to-end tests were not executed")
        non_claims.append("Manual UI verification was not performed")
        return non_claims

    @staticmethod
    def _compute_baseline_failures(results: list[CheckResult]) -> list[str]:
        failures = []
        for r in results:
            if not r.passed:
                failures.append(
                    f"{r.proof_class}: exit {r.exit_code} — {r.stderr[:100]}"
                )
        return failures
