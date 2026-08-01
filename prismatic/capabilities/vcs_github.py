"""
Prismatic Engine — GitHub VCS Capability
========================================

Implements the high-level vcs.github capability actions (branch, PR, comment, status)
and emits normalized events to the gateway event bus.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
import urllib.error
from typing import Any, Optional

from prismatic.providers.github import GitHubProvider

# Try importing event emitter, fallback to no-op
try:
    from prismatic.gateway.ipc_bridge import send_event_via_socket
    _HAS_IPC = True
except ImportError:
    _HAS_IPC = False


def _emit_event(event_type: str, payload: dict[str, Any]) -> None:
    """Emit a VCS event to the IPC bridge (best-effort)."""
    if not _HAS_IPC:
        return
    try:
        send_event_via_socket(
            event_type=event_type,
            source="vcs.github",
            payload=payload,
        )
    except Exception:
        pass


class GitHubCapability:
    """
    High-level GitHub VCS capability wrapper.
    Ensures idempotence and event emission for all VCS operations.
    """

    def __init__(self, provider: Optional[GitHubProvider] = None):
        self.provider = provider or GitHubProvider()

    def create_branch(self, branch: str, base: str = "main") -> bool:
        """
        Create a new branch from a base branch. Idempotent.
        """
        # Idempotency check: see if branch already exists
        ref = self.provider.get_branch_ref(branch)
        if ref:
            # Branch already exists, no need to recreate
            _emit_event(
                "vcs.branch_created",
                {"provider": "github", "branch": branch, "base": base}
            )
            return True

        success = self.provider.create_branch(branch, base)
        if success:
            _emit_event(
                "vcs.branch_created",
                {"provider": "github", "branch": branch, "base": base}
            )
        return success

    def find_existing_pull_request(self, head: str, base: str) -> Optional[dict[str, Any]]:
        """
        Search for an existing open pull request for the head branch.
        """
        # 1. Try API
        if self.provider.token and self.provider.repo:
            try:
                headers = {
                    "Authorization": f"Bearer {self.provider.token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Prismatic-Engine"
                }
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{self.provider.repo}/pulls?state=open",
                    headers=headers,
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    pulls = json.loads(resp.read().decode("utf-8"))
                    for pr in pulls:
                        if pr.get("head", {}).get("ref") == head and pr.get("base", {}).get("ref") == base:
                            return {
                                "number": pr.get("number"),
                                "html_url": pr.get("html_url"),
                                "head_sha": pr.get("head", {}).get("sha"),
                                "state": pr.get("state")
                            }
            except Exception:
                pass

        # 2. Fallback to gh CLI
        try:
            cmd = ["gh", "pr", "list", "-R", self.provider.repo, "--head", head, "--state", "open", "--json", "number,url,headRefName,baseRefName"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                pulls = json.loads(res.stdout)
                for pr in pulls:
                    if pr.get("headRefName") == head and pr.get("baseRefName") == base:
                        return {
                            "number": pr.get("number"),
                            "html_url": pr.get("url"),
                            "state": "open"
                        }
        except Exception:
            pass

        return None

    def open_pull_request(self, title: str, body: str, head: str, base: str = "main") -> Optional[dict[str, Any]]:
        """
        Open a new pull request. Idempotent.
        """
        # Idempotency check: see if PR already exists
        existing_pr = self.find_existing_pull_request(head, base)
        if existing_pr:
            _emit_event(
                "vcs.pr_opened",
                {
                    "provider": "github",
                    "pr_number": existing_pr["number"],
                    "url": existing_pr["html_url"]
                }
            )
            return existing_pr

        pr_info = self.provider.open_pull_request(title, body, head, base)
        if pr_info:
            pr_number = pr_info.get("number")
            # If fallback to gh CLI, get the number from URL if possible
            if not pr_number and pr_info.get("fallback"):
                url = pr_info.get("html_url", "")
                try:
                    pr_number = int(url.split("/")[-1])
                except Exception:
                    pr_number = 0
            
            _emit_event(
                "vcs.pr_opened",
                {
                    "provider": "github",
                    "pr_number": pr_number,
                    "url": pr_info.get("html_url")
                }
            )
            return {
                "number": pr_number,
                "html_url": pr_info.get("html_url"),
                "state": "open"
            }
        return None

    def check_comment_exists(self, pr_number: int, body: str) -> bool:
        """
        Check if a comment with the exact same body already exists on the PR.
        """
        # 1. Try API
        if self.provider.token and self.provider.repo:
            try:
                headers = {
                    "Authorization": f"Bearer {self.provider.token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Prismatic-Engine"
                }
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{self.provider.repo}/issues/{pr_number}/comments",
                    headers=headers,
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    comments = json.loads(resp.read().decode("utf-8"))
                    for comment in comments:
                        if comment.get("body", "").strip() == body.strip():
                            return True
            except Exception:
                pass

        # 2. Fallback to gh CLI
        try:
            cmd = ["gh", "pr", "view", str(pr_number), "-R", self.provider.repo, "--json", "comments"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                for comment in data.get("comments", []):
                    if comment.get("body", "").strip() == body.strip():
                        return True
        except Exception:
            pass

        return False

    def add_pr_comment(self, pr_number: int, body: str) -> bool:
        """
        Add a comment to the PR. Idempotent.
        """
        if self.check_comment_exists(pr_number, body):
            _emit_event(
                "vcs.pr_comment",
                {"provider": "github", "pr_number": pr_number, "body": body}
            )
            return True

        success = self.provider.add_pr_comment(pr_number, body)
        if success:
            _emit_event(
                "vcs.pr_comment",
                {"provider": "github", "pr_number": pr_number, "body": body}
            )
        return success

    def get_pr_details(self, pr_number: int) -> dict[str, Any]:
        """
        Retrieve PR state and head commit SHA.
        """
        state = "open"
        head_sha = None

        if self.provider.token and self.provider.repo:
            try:
                headers = {
                    "Authorization": f"Bearer {self.provider.token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Prismatic-Engine"
                }
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{self.provider.repo}/pulls/{pr_number}",
                    headers=headers,
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    state = data.get("state", "open")
                    head_sha = data.get("head", {}).get("sha")
            except Exception:
                pass

        if not head_sha:
            try:
                cmd = ["gh", "pr", "view", str(pr_number), "-R", self.provider.repo, "--json", "state,headRefOid"]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    state = data.get("state", "open").lower()
                    head_sha = data.get("headRefOid")
            except Exception:
                pass

        return {"state": state, "head_sha": head_sha}

    def get_pr_status(self, pr_number: int) -> dict[str, Any]:
        """
        Retrieve PR state and check runs status. Idempotent.
        """
        details = self.get_pr_details(pr_number)
        state = details["state"]
        head_sha = details["head_sha"] or f"pr-{pr_number}"

        checks = self.provider.get_pull_request_checks(head_sha)
        
        checks_summary = "success"
        if checks:
            has_failure = any(c.get("conclusion") in ("failure", "action_required", "cancelled", "timed_out") for c in checks)
            has_pending = any(c.get("status") != "completed" or c.get("conclusion") is None for c in checks)
            if has_failure:
                checks_summary = "failure"
            elif has_pending:
                checks_summary = "pending"
            else:
                checks_summary = "success"
        else:
            checks_summary = "pending"

        _emit_event(
            "vcs.pr_status",
            {
                "provider": "github",
                "pr_number": pr_number,
                "state": state,
                "checks": checks_summary
            }
        )

        return {
            "state": state,
            "checks": checks_summary,
            "raw_checks": checks
        }
