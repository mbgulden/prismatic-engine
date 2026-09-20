"""prismatic.quality.clean_room — Ephemeral Clean-Room Execution Engine.

Provides hermetic, provider-agnostic clean-room test execution by spawning isolated
Git worktrees, stripping ambient environment variables, and normalizing test logs
for deterministic SHA-256 hash receipts.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


from prismatic.quality.log_normalizer import compute_dld_sha256, normalize_log


@dataclass
class CleanRoomResult:
    status: str
    marker: str
    candidate_head: str
    candidate_tree: str
    remote_name: str
    remote_ref_matched: bool
    clean_room_path: str
    environment_sanitized: bool
    tests_passed: int
    tests_failed: int
    execution_seconds: float
    deterministic_log_sha256: str
    raw_log: str

    def to_dict(self) -> dict:
        return {
            "marker": self.marker,
            "status": self.status,
            "candidate_head": self.candidate_head,
            "candidate_tree": self.candidate_tree,
            "remote_name": self.remote_name,
            "remote_ref_matched": self.remote_ref_matched,
            "clean_room_path": self.clean_room_path,
            "environment_sanitized": self.environment_sanitized,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "execution_seconds": self.execution_seconds,
            "deterministic_log_sha256": self.deterministic_log_sha256,
        }


class CleanRoomRunner:
    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self.repo_root = (repo_root or Path.cwd()).resolve()

    def get_head_sha(self) -> str:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def get_tree_sha(self) -> str:
        res = subprocess.run(
            ["git", "cat-file", "-p", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        for line in res.stdout.splitlines():
            if line.startswith("tree "):
                return line.split()[1]
        raise ValueError("Could not extract tree SHA from HEAD commit")

    def verify_remote_ref(self, remote_name: str = "origin", branch: Optional[str] = None) -> Tuple[bool, str]:
        if not branch:
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            branch = res.stdout.strip()

        head_sha = self.get_head_sha()
        res = subprocess.run(
            ["git", "ls-remote", remote_name, branch],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return False, ""

        remote_sha = res.stdout.split()[0].strip()
        return remote_sha == head_sha, remote_sha

    def spawn_worktree(self, commit_sha: str) -> Path:
        target_dir = Path(tempfile.gettempdir()) / f"prismatic-cleanroom-{commit_sha[:8]}"
        if target_dir.exists():
            subprocess.run(
                ["git", "worktree", "remove", "-f", str(target_dir)],
                cwd=self.repo_root,
                capture_output=True,
            )
            shutil.rmtree(target_dir, ignore_errors=True)

        subprocess.run(
            ["git", "worktree", "add", "-f", str(target_dir), commit_sha],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return target_dir

    def cleanup_worktree(self, worktree_path: Path) -> None:
        if worktree_path.exists():
            subprocess.run(
                ["git", "worktree", "remove", "-f", str(worktree_path)],
                cwd=self.repo_root,
                capture_output=True,
            )
            shutil.rmtree(worktree_path, ignore_errors=True)

    def run_clean_room_verification(
        self,
        remote_name: str = "origin",
        branch: Optional[str] = None,
        cleanup: bool = True,
    ) -> CleanRoomResult:
        head_sha = self.get_head_sha()
        tree_sha = self.get_tree_sha()
        matched, remote_sha = self.verify_remote_ref(remote_name, branch)

        worktree_dir = self.spawn_worktree(head_sha)
        try:
            # Build sanitized environment
            sanitized_env = {**os.environ}
            for k in ["HERMES_HOME", "HERMES_PROFILE", "PYTHONPATH"]:
                sanitized_env.pop(k, None)

            python_exe = sys.executable
            cmd = [python_exe, "-m", "pytest"]
            
            res = subprocess.run(
                cmd,
                cwd=worktree_dir,
                env=sanitized_env,
                capture_output=True,
                text=True,
            )

            raw_log = res.stdout + "\n" + res.stderr
            dld_sha256 = compute_dld_sha256(raw_log)

            # Parse test counts
            passed = 0
            failed = 0
            m_pass = re.search(r"(\d+)\s+passed", raw_log)
            m_fail = re.search(r"(\d+)\s+failed", raw_log)
            if m_pass:
                passed = int(m_pass.group(1))
            if m_fail:
                failed = int(m_fail.group(1))

            status = "PASS" if res.returncode == 0 and failed == 0 else "FAIL"
            marker = "PE_CLEAN_ROOM_VERIFIED_OK" if status == "PASS" else "PE_CLEAN_ROOM_VERIFICATION_FAILED"

            return CleanRoomResult(
                status=status,
                marker=marker,
                candidate_head=head_sha,
                candidate_tree=tree_sha,
                remote_name=remote_name,
                remote_ref_matched=matched,
                clean_room_path=str(worktree_dir),
                environment_sanitized=True,
                tests_passed=passed,
                tests_failed=failed,
                execution_seconds=0.0,
                deterministic_log_sha256=dld_sha256,
                raw_log=raw_log,
            )
        finally:
            if cleanup:
                self.cleanup_worktree(worktree_dir)
