"""
GitManager — Git lifecycle operations and governance enforcement.

Handles branch creation, commit attribution, and merge governance according
to the Prismatic Engine protocols.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("prismatic.core.git")


class GitError(Exception):
    """Base class for Git-related errors."""


class GitManager:
    """
    Manages Git operations with Prismatic governance.
    """

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        if not (self.repo_path / ".git").exists():
            raise GitError(f"Path '{repo_path}' is not a git repository.")

    def _run_git(self, args: list[str]) -> str:
        """Helper to run git commands."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            logger.error(f"Git command failed: git {' '.join(args)}\nError: {stderr}")
            raise GitError(f"Git command failed: {stderr}") from exc

    def get_current_branch(self) -> str:
        return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])

    def create_branch(self, branch_name: str, prefix: str = "", base: str = "main") -> None:
        """
        Create and checkout a new branch with a prefix.
        """
        full_name = f"{prefix}{branch_name}" if prefix else branch_name

        # Ensure we are on the base branch and it's up to date
        self._run_git(["checkout", base])
        self._run_git(["pull", "origin", base])

        # Create and checkout
        self._run_git(["checkout", "-b", full_name])
        logger.info(f"Created and checked out branch: {full_name} (from {base})")

    def commit(self, message: str, agent_name: str, issue_id: str | None = None) -> None:
        """
        Commit staged changes with agent attribution and optional issue reference.
        Format: [AGENT] description (#ISSUE)
        """
        issue_part = f" (#{issue_id})" if issue_id else ""
        formatted_message = f"[{agent_name}] {message}{issue_part}"

        # Set local config for attribution if needed
        # self._run_git(["config", "user.name", f"{agent_name} (Prismatic Agent)"])

        self._run_git(["commit", "-m", formatted_message])
        logger.info(f"Committed changes with message: {formatted_message}")

    def push(self, remote: str = "origin", branch: str | None = None) -> None:
        if not branch:
            branch = self.get_current_branch()
        self._run_git(["push", "-u", remote, branch])
        logger.info(f"Pushed branch {branch} to {remote}")

    def merge(self, source_branch: str, target_branch: str, agent_name: str, governor_agent: str = "fred") -> None:
        """
        Merge source into target branch, enforcing the Staging Governor rule.
        """
        if agent_name.lower() != governor_agent.lower():
            raise GitError(f"Security Violation: Agent '{agent_name}' is not authorized to merge. "
                           f"Only '{governor_agent}' (Governor) can merge to '{target_branch}'.")

        self._run_git(["checkout", target_branch])
        self._run_git(["pull", "origin", target_branch])
        self._run_git(["merge", source_branch])
        self._run_git(["push", "origin", target_branch])
        logger.info(f"Merged {source_branch} into {target_branch} by {agent_name}")

    def get_changed_files(self, base: str = "main", head: str = "HEAD") -> list[str]:
        """Get list of files changed between base and head."""
        output = self._run_git(["diff", "--name-only", f"{base}...{head}"])
        return [f.strip() for f in output.split("\n") if f.strip()]

    def get_diff(self, base: str = "main", head: str = "HEAD") -> str:
        """Get the unified diff between base and head."""
        return self._run_git(["diff", f"{base}...{head}"])
