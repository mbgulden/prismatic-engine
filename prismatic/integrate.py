"""
prismatic/integrate.py — Integrate Phase (Step 7 of 7-Step Iterative Loop)
===========================================================================

The Integrate phase is the final step in the 7-step pipeline. It:
1. **Merges refined outputs** — Merges the agent's feature branch into the target
2. **Assembles final artifacts** — Collects all work products (commits, PRs, reviews)
3. **Runs integration tests** — Executes the full test suite post-merge
4. **Produces the completion manifest** — Generates a JSON + markdown summary

Usage
-----
    from prismatic.integrate import IntegratePhase, IntegrationManifest

    phase = IntegratePhase(issue_id="GRO-1234", branch="ned/gro-1234-fix",
                           target_branch="main")
    manifest = phase.run()
    print(manifest.summary())
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════
# Integration Status
# ═══════════════════════════════════════════════════════════════

class IntegrationStatus(Enum):
    """Outcome of the integration phase."""

    PENDING = "pending"
    MERGING = "merging"
    TESTING = "testing"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value

    def is_terminal(self) -> bool:
        """True if this status represents a terminal state."""
        return self in (IntegrationStatus.COMPLETED, IntegrationStatus.FAILED,
                        IntegrationStatus.SKIPPED)


# ═══════════════════════════════════════════════════════════════
# Integration Artifact
# ═══════════════════════════════════════════════════════════════

@dataclass
class IntegrationArtifact:
    """A single work product collected during integration."""

    artifact_type: str          # "commit", "pull_request", "test_report", "review", "file"
    identifier: str             # e.g., commit SHA, PR number, file path
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
# Integration Manifest
# ═══════════════════════════════════════════════════════════════

@dataclass
class IntegrationManifest:
    """The final deliverable of the Integrate phase.

    Captures everything that happened during a pipeline run: commits,
    test results, review feedback, and metadata. Can be serialized
    to JSON and rendered as markdown.
    """

    issue_id: str
    branch: str
    target_branch: str
    status: IntegrationStatus = IntegrationStatus.PENDING
    artifacts: list[IntegrationArtifact] = field(default_factory=list)
    test_results: dict[str, Any] = field(default_factory=dict)
    merge_sha: str = ""
    error_message: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "branch": self.branch,
            "target_branch": self.target_branch,
            "status": self.status.value,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "test_results": self.test_results,
            "merge_sha": self.merge_sha,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize the manifest to indented JSON."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        """Render the manifest as a human-readable markdown report."""
        lines = [
            f"# Integration Manifest — {self.issue_id}",
            "",
            f"**Status:** `{self.status.value.upper()}`",
            f"**Branch:** `{self.branch}` → `{self.target_branch}`",
            f"**Started:** {self.started_at}",
        ]
        if self.completed_at:
            lines.append(f"**Completed:** {self.completed_at}")
        if self.merge_sha:
            lines.append(f"**Merge SHA:** `{self.merge_sha}`")

        lines.append("")

        # Artifacts
        if self.artifacts:
            lines.append("## Artifacts")
            lines.append("")
            lines.append("| Type | Identifier | Description |")
            lines.append("|------|------------|-------------|")
            for a in self.artifacts:
                lines.append(f"| {a.artifact_type} | `{a.identifier}` | {a.description} |")
            lines.append("")

        # Test Results
        if self.test_results:
            lines.append("## Test Results")
            lines.append("")
            lines.append("```")
            for key, value in self.test_results.items():
                lines.append(f"{key}: {value}")
            lines.append("```")
            lines.append("")

        # Error
        if self.error_message:
            lines.append("## Error")
            lines.append("")
            lines.append(f"```\n{self.error_message}\n```")
            lines.append("")

        # Metadata
        if self.metadata:
            lines.append("## Metadata")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(self.metadata, indent=2))
            lines.append("```")

        return "\n".join(lines)

    def summary(self) -> str:
        """Return a concise one-line status summary."""
        if self.status == IntegrationStatus.COMPLETED:
            return (f"✅ {self.issue_id}: Integrated {self.branch} → "
                    f"{self.target_branch} ({len(self.artifacts)} artifacts, "
                    f"{self.merge_sha[:8] if self.merge_sha else 'no merge'})")
        elif self.status == IntegrationStatus.FAILED:
            return f"❌ {self.issue_id}: Integration failed — {self.error_message[:80]}"
        elif self.status == IntegrationStatus.SKIPPED:
            return f"⏭️ {self.issue_id}: Integration skipped"
        else:
            return f"🔄 {self.issue_id}: Integration in progress ({self.status.value})"

    def is_success(self) -> bool:
        return self.status == IntegrationStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════
# Integrate Phase Executor
# ═══════════════════════════════════════════════════════════════

class IntegratePhase:
    """Execute the Integrate phase (Step 7) of the 7-step iterative loop.

    Handles merging, artifact assembly, integration testing, and
    manifest generation for a completed pipeline run.

    Usage::

        phase = IntegratePhase(issue_id="GRO-1234", branch="ned/gro-1234-fix")
        manifest = phase.run()
        if manifest.is_success():
            print(manifest.to_markdown())

    Attributes:
        issue_id: The Linear issue identifier.
        branch: The source (agent) branch to merge.
        target_branch: The destination branch (default: ``"main"``).
        repo_path: Path to the git repository (default: cwd).
        test_command: Shell command to run integration tests.
        skip_merge: If True, skip the git merge step (for dry-run/testing).
        skip_tests: If True, skip integration test execution.
    """

    def __init__(
        self,
        issue_id: str,
        branch: str,
        target_branch: str = "main",
        repo_path: str | Path | None = None,
        test_command: str = "pytest tests/ -x -q",
        skip_merge: bool = False,
        skip_tests: bool = False,
        manifest_dir: str | Path | None = None,
    ):
        self.issue_id = issue_id
        self.branch = branch
        self.target_branch = target_branch
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.test_command = test_command
        self.skip_merge = skip_merge
        self.skip_tests = skip_tests

        # Manifest storage
        if manifest_dir:
            self._manifest_dir = Path(manifest_dir)
        else:
            self._manifest_dir = (
                self.repo_path / "prismatic_state" / "manifests"
            )
        self._manifest_dir.mkdir(parents=True, exist_ok=True)

        self._artifacts: list[IntegrationArtifact] = []
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────

    def run(self) -> IntegrationManifest:
        """Execute the full Integrate phase and return the manifest.

        Steps:
            1. Merge the branch into target
            2. Run integration tests
            3. Assemble artifacts
            4. Produce the manifest
            5. Persist manifest to disk
        """
        manifest = IntegrationManifest(
            issue_id=self.issue_id,
            branch=self.branch,
            target_branch=self.target_branch,
        )

        try:
            # Step 1: Merge
            self._merge_branch(manifest)
            if manifest.status == IntegrationStatus.FAILED:
                return self._finish(manifest)

            # Step 2: Run integration tests
            self._run_tests(manifest)
            if manifest.status == IntegrationStatus.FAILED:
                # Merge succeeded but tests failed — still record
                pass

            # Step 3: Assemble artifacts
            self._assemble_artifacts(manifest)

            # If we got here without failure, mark complete
            if manifest.status != IntegrationStatus.FAILED:
                manifest.status = IntegrationStatus.COMPLETED

        except Exception as exc:
            manifest.status = IntegrationStatus.FAILED
            manifest.error_message = f"{type(exc).__name__}: {exc}"

        return self._finish(manifest)

    def collect_artifacts(self) -> list[IntegrationArtifact]:
        """Collect artifacts without executing merge or tests.

        Useful for assembling a manifest from an already-merged branch.
        """
        self._collect_commits()
        self._collect_pr_info()
        return list(self._artifacts)

    def load_manifest(self) -> IntegrationManifest | None:
        """Load a previously saved manifest from disk."""
        path = self._manifest_path()
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
        return self._dict_to_manifest(data)

    # ── Internal: Merge ──────────────────────────────────────

    def _merge_branch(self, manifest: IntegrationManifest) -> None:
        """Merge the feature branch into the target branch."""
        if self.skip_merge:
            manifest.status = IntegrationStatus.MERGING
            manifest.metadata["merge_skipped"] = True
            return

        manifest.status = IntegrationStatus.MERGING

        # Verify we're on the target branch
        try:
            self._git("checkout", self.target_branch)
        except subprocess.CalledProcessError as exc:
            manifest.status = IntegrationStatus.FAILED
            manifest.error_message = f"Failed to checkout {self.target_branch}: {exc}"
            return

        # Pull latest
        try:
            self._git("pull", "origin", self.target_branch)
        except subprocess.CalledProcessError:
            # Non-fatal: might already be up to date
            pass

        # Merge
        try:
            result = self._git("merge", self.branch, "--no-ff", "-m",
                               f"Integrate {self.issue_id}: {self.branch} → {self.target_branch}")
            # Extract merge SHA
            sha_result = self._git("rev-parse", "HEAD")
            manifest.merge_sha = sha_result.strip()
            self._add_artifact(
                "merge_commit", manifest.merge_sha,
                f"Merged {self.branch} into {self.target_branch}"
            )
        except subprocess.CalledProcessError as exc:
            manifest.status = IntegrationStatus.FAILED
            manifest.error_message = f"Merge failed: {exc}"
            # Try to abort merge
            try:
                self._git("merge", "--abort")
            except subprocess.CalledProcessError:
                pass
            return

    # ── Internal: Tests ──────────────────────────────────────

    def _run_tests(self, manifest: IntegrationManifest) -> None:
        """Execute integration tests post-merge."""
        if self.skip_tests:
            manifest.test_results = {"status": "skipped"}
            manifest.status = IntegrationStatus.TESTING
            return

        manifest.status = IntegrationStatus.TESTING

        try:
            # Resolve venv
            venv_python = self._find_venv_python()
            if venv_python:
                cmd = f"{venv_python} -m {self.test_command}"
            else:
                cmd = self.test_command

            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.repo_path),
                timeout=300,
            )

            manifest.test_results = {
                "exit_code": result.returncode,
                "stdout_lines": len(result.stdout.splitlines()) if result.stdout else 0,
                "stderr_lines": len(result.stderr.splitlines()) if result.stderr else 0,
                "passed": result.returncode == 0,
                "output_tail": "\n".join(
                    (result.stdout + result.stderr).splitlines()[-20:]
                ),
            }

            if result.returncode != 0:
                manifest.status = IntegrationStatus.FAILED
                manifest.error_message = (
                    f"Integration tests failed (exit code {result.returncode})"
                )
                self._add_artifact(
                    "test_report", "integration_tests",
                    f"FAILED (exit {result.returncode})"
                )
            else:
                self._add_artifact(
                    "test_report", "integration_tests",
                    "PASSED"
                )
        except subprocess.TimeoutExpired:
            manifest.status = IntegrationStatus.FAILED
            manifest.error_message = "Integration tests timed out (300s)"
            manifest.test_results = {"status": "timeout"}
        except Exception as exc:
            manifest.status = IntegrationStatus.FAILED
            manifest.error_message = f"Test execution error: {exc}"
            manifest.test_results = {"status": "error", "error": str(exc)}

    # ── Internal: Artifact Assembly ──────────────────────────

    def _assemble_artifacts(self, manifest: IntegrationManifest) -> None:
        """Collect all artifacts from the pipeline run."""
        manifest.status = IntegrationStatus.ASSEMBLING
        self._collect_commits()
        self._collect_pr_info()
        manifest.artifacts = list(self._artifacts)

    def _collect_commits(self) -> None:
        """Collect commit information from the branch."""
        try:
            # Get commits on the branch that aren't on target
            log = self._git(
                "log", f"{self.target_branch}..{self.branch}",
                "--oneline", "--no-merges"
            )
            for line in log.strip().splitlines():
                if line:
                    sha, _, msg = line.partition(" ")
                    self._add_artifact("commit", sha, msg.strip())
        except subprocess.CalledProcessError:
            pass

    def _collect_pr_info(self) -> None:
        """Collect PR information if available."""
        # Attempt to find a PR associated with this branch via gh CLI
        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--head", self.branch,
                 "--json", "number,title,url", "--limit", "1"],
                capture_output=True, text=True, timeout=15,
                cwd=str(self.repo_path),
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                if data:
                    pr = data[0]
                    self._add_artifact(
                        "pull_request",
                        f"#{pr['number']}",
                        pr.get("title", ""),
                        metadata={"url": pr.get("url", "")},
                    )
        except (subprocess.CalledProcessError, json.JSONDecodeError,
                FileNotFoundError):
            pass

    # ── Internal: Manifest Persistence ───────────────────────

    def _finish(self, manifest: IntegrationManifest) -> IntegrationManifest:
        """Finalize the manifest: set timestamp, persist, return."""
        manifest.completed_at = datetime.now(timezone.utc).isoformat()
        self._persist_manifest(manifest)
        return manifest

    def _persist_manifest(self, manifest: IntegrationManifest) -> None:
        """Write the manifest to disk."""
        path = self._manifest_path()
        try:
            with open(path, "w") as f:
                f.write(manifest.to_json())
        except OSError as exc:
            print(f"[integrate] Failed to persist manifest {self.issue_id}: {exc}")

    def _manifest_path(self) -> Path:
        return self._manifest_dir / f"{self.issue_id.replace('/', '_')}.json"

    # ── Internal: Helpers ────────────────────────────────────

    def _git(self, *args: str) -> str:
        """Run a git command in the repo directory, returning stdout."""
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, check=True,
            cwd=str(self.repo_path),
        )
        return result.stdout

    def _add_artifact(
        self,
        artifact_type: str,
        identifier: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._artifacts.append(
                IntegrationArtifact(
                    artifact_type=artifact_type,
                    identifier=identifier,
                    description=description,
                    metadata=metadata or {},
                )
            )

    def _find_venv_python(self) -> str | None:
        """Find a usable Python in a virtual environment."""
        candidates = [
            self.repo_path / ".venv_dev" / "bin" / "python3",
            self.repo_path / ".venv" / "bin" / "python3",
            Path.home() / ".prismatic" / "venv_stable" / "bin" / "python3",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    # ── Internal: Deserialization ────────────────────────────

    @staticmethod
    def _dict_to_manifest(data: dict[str, Any]) -> IntegrationManifest:
        """Reconstruct a manifest from a dictionary."""
        artifacts = []
        for a in data.get("artifacts", []):
            artifacts.append(IntegrationArtifact(**a))
        return IntegrationManifest(
            issue_id=data["issue_id"],
            branch=data["branch"],
            target_branch=data.get("target_branch", "main"),
            status=IntegrationStatus(data.get("status", "pending")),
            artifacts=artifacts,
            test_results=data.get("test_results", {}),
            merge_sha=data.get("merge_sha", ""),
            error_message=data.get("error_message", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            metadata=data.get("metadata", {}),
        )


# ═══════════════════════════════════════════════════════════════
# Pipeline Integration — State Machine Factory
# ═══════════════════════════════════════════════════════════════

def integrate_pipeline_run(
    issue_id: str,
    branch: str,
    target_branch: str = "main",
    repo_path: str | Path | None = None,
    skip_merge: bool = False,
    skip_tests: bool = False,
) -> IntegrationManifest:
    """Convenience function to integrate a completed pipeline run.

    This is the primary entry point called by the state machine when
    transitioning to Step.INTEGRATE.

    Args:
        issue_id: Linear issue identifier.
        branch: Source (agent) branch.
        target_branch: Destination branch.
        repo_path: Path to the git repository.
        skip_merge: If True, skip actual git merge.
        skip_tests: If True, skip test execution.

    Returns:
        The completed IntegrationManifest.
    """
    phase = IntegratePhase(
        issue_id=issue_id,
        branch=branch,
        target_branch=target_branch,
        repo_path=repo_path,
        skip_merge=skip_merge,
        skip_tests=skip_tests,
    )
    return phase.run()


def assemble_dry_run_manifest(
    issue_id: str,
    branch: str,
    target_branch: str = "main",
    repo_path: str | Path | None = None,
) -> IntegrationManifest:
    """Collect artifacts and produce a manifest WITHOUT merging or testing.

    Useful for pipeline runs where the merge was already done manually,
    or for generating manifest documentation post-hoc.
    """
    phase = IntegratePhase(
        issue_id=issue_id,
        branch=branch,
        target_branch=target_branch,
        repo_path=repo_path,
        skip_merge=True,
        skip_tests=True,
    )
    return phase.run()
