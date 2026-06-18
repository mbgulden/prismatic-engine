"""
Prismatic Engine — GitHub Provider & Capability Contract
=========================================================

Handles GitHub API connectivity, branch/PR management, status checks,
reviews, and webhook signature verification. Integrates with the `gh`
CLI adapter when present, or falls back to direct REST/GraphQL calls via
standard `urllib.request`.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
import urllib.error
import hmac
import hashlib
from typing import Any, Optional
from pathlib import Path

# Default paths (overridable via env)
PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME", os.path.expanduser("~")))
CONFIG_DIR = PRISMATIC_HOME / ".prismatic"
USER_CONFIG_PATH = CONFIG_DIR / "config.yaml"


class GitHubProvider:
    """
    GitHub connection and API layer provider.
    Supports token discovery from env, config, and `gh` CLI credentials.
    """

    def __init__(self, token: Optional[str] = None, repo: Optional[str] = None):
        """
        Initialize the GitHub provider.
        
        Args:
            token: Optional GITHUB_TOKEN or PAT. If not provided, it will be discovered.
            repo: Optional default repository target in format 'owner/repo'.
        """
        self._token = token if token is not None else self._discover_token()
        self._repo = repo if repo is not None else self._discover_repo()

    def _discover_token(self) -> str:
        # 1. Environment variables
        for env_var in ["GITHUB_TOKEN", "GH_TOKEN", "PRISMATIC_GITHUB_TOKEN"]:
            val = os.environ.get(env_var, "").strip()
            if val:
                return val

        # 2. config.yaml
        if USER_CONFIG_PATH.exists():
            try:
                import yaml
                with open(USER_CONFIG_PATH) as f:
                    config = yaml.safe_load(f) or {}
                    token = config.get("github", {}).get("token", "").strip()
                    if token:
                        return token
            except Exception:
                pass

        # 3. Fallback: run `gh auth token` if gh CLI exists
        try:
            res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

        # 4. Check ~/.config/gh/hosts.yml
        gh_hosts_path = Path.home() / ".config" / "gh" / "hosts.yml"
        if gh_hosts_path.exists():
            try:
                import yaml
                with open(gh_hosts_path) as f:
                    hosts = yaml.safe_load(f) or {}
                    token = hosts.get("github.com", {}).get("oauth_token", "").strip()
                    if token:
                        return token
            except Exception:
                pass

        return ""

    def _discover_repo(self) -> str:
        # Check env or git remote
        env_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
        if env_repo:
            return env_repo

        # Check config
        if USER_CONFIG_PATH.exists():
            try:
                import yaml
                with open(USER_CONFIG_PATH) as f:
                    config = yaml.safe_load(f) or {}
                    repo = config.get("github", {}).get("repository", "").strip()
                    if repo:
                        return repo
            except Exception:
                pass

        # Find git remote origin url
        try:
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True, text=True, check=False
            )
            if res.returncode == 0 and res.stdout.strip():
                url = res.stdout.strip()
                # Parse owner/repo from URL
                if "github.com" in url:
                    part = url.split("github.com")[-1]
                    part = part.lstrip(":/")
                    if part.endswith(".git"):
                        part = part[:-4]
                    return part
        except Exception:
            pass

        return ""

    @property
    def token(self) -> str:
        return self._token

    @property
    def repo(self) -> str:
        return self._repo

    def has_credentials(self) -> bool:
        return bool(self._token)

    def verify_auth(self) -> tuple[bool, dict[str, Any], list[str]]:
        """
        Verify credentials, retrieve authenticated user info and list of scopes.
        
        Returns:
            A tuple of (success, user_metadata_dict, scope_list)
        """
        if not self._token:
            return False, {}, []

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Prismatic-Engine"
        }

        req = urllib.request.Request(
            "https://api.github.com/user",
            headers=headers,
            method="GET"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                user_data = json.loads(body)
                scopes_header = resp.info().get("X-OAuth-Scopes", "")
                scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
                return True, user_data, scopes
        except urllib.error.HTTPError as exc:
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                err_body = {"message": exc.reason}
            return False, {"error": f"HTTP {exc.code}", "detail": err_body}, []
        except Exception as exc:
            return False, {"error": str(exc)}, []

    def verify_repo_access(self, repo: Optional[str] = None) -> tuple[bool, dict[str, Any]]:
        """
        Verify access to a specific repository.
        
        Returns:
            A tuple of (success, repo_metadata_dict)
        """
        target_repo = repo or self._repo
        if not target_repo:
            return False, {"error": "No repository specified"}

        if not self._token:
            return False, {"error": "No GITHUB_TOKEN configured"}

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Prismatic-Engine"
        }

        req = urllib.request.Request(
            f"https://api.github.com/repos/{target_repo}",
            headers=headers,
            method="GET"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                repo_data = json.loads(body)
                return True, repo_data
        except urllib.error.HTTPError as exc:
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                err_body = {"message": exc.reason}
            return False, {"error": f"HTTP {exc.code}", "detail": err_body}
        except Exception as exc:
            return False, {"error": str(exc)}

    def get_branch_ref(self, branch: str, repo: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Get the Git SHA for a branch."""
        target_repo = repo or self._repo
        if not target_repo or not self._token:
            return None

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Prismatic-Engine"
        }

        req = urllib.request.Request(
            f"https://api.github.com/repos/{target_repo}/git/ref/heads/{branch}",
            headers=headers,
            method="GET"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def create_branch(self, branch: str, source_branch: str = "main", repo: Optional[str] = None) -> bool:
        """Create a new branch from a source branch."""
        target_repo = repo or self._repo
        if not target_repo or not self._token:
            return False

        # Get the source SHA
        source_ref = self.get_branch_ref(source_branch, target_repo)
        if not source_ref:
            return False
        
        sha = source_ref.get("object", {}).get("sha")
        if not sha:
            return False

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Prismatic-Engine"
        }

        payload = {
            "ref": f"refs/heads/{branch}",
            "sha": sha
        }

        req = urllib.request.Request(
            f"https://api.github.com/repos/{target_repo}/git/refs",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except Exception:
            return False

    def open_pull_request(self, title: str, body: str, head: str, base: str = "main", repo: Optional[str] = None) -> Optional[dict[str, Any]]:
        """
        Create a pull request.
        
        Returns:
            The created PR metadata or None.
        """
        target_repo = repo or self._repo
        if not target_repo or not self._token:
            return None

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Prismatic-Engine"
        }

        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base
        }

        req = urllib.request.Request(
            f"https://api.github.com/repos/{target_repo}/pulls",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"[GitHubProvider] Failed to open PR via API: {exc}")
            # Convenience adapter fallback using gh CLI
            try:
                cmd = ["gh", "pr", "create", "-R", target_repo, "-t", title, "-b", body, "-H", head, "-B", base]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    print(f"[GitHubProvider] Opened PR via gh CLI: {res.stdout.strip()}")
                    return {"html_url": res.stdout.strip(), "fallback": True}
            except Exception as cli_exc:
                print(f"[GitHubProvider] Failed fallback to gh CLI: {cli_exc}")
            return None

    def get_pull_request_diff(self, pr_number: int, repo: Optional[str] = None) -> Optional[str]:
        """Fetch the diff content of a pull request."""
        target_repo = repo or self._repo
        if not target_repo or not self._token:
            # Fallback to gh CLI
            try:
                cmd = ["gh", "pr", "diff", str(pr_number), "-R", str(target_repo)]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    return res.stdout
            except Exception:
                pass
            return None

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github.v3.diff",
            "User-Agent": "Prismatic-Engine"
        }

        req = urllib.request.Request(
            f"https://api.github.com/repos/{target_repo}/pulls/{pr_number}",
            headers=headers,
            method="GET"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            print(f"[GitHubProvider] Failed to fetch PR diff via API: {exc}")
            # Try gh CLI fallback
            try:
                cmd = ["gh", "pr", "diff", str(pr_number), "-R", str(target_repo)]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    return res.stdout
            except Exception as cli_exc:
                print(f"[GitHubProvider] Fallback gh CLI diff failed: {cli_exc}")
            return None

    def get_pull_request_checks(self, ref: str, repo: Optional[str] = None) -> list[dict[str, Any]]:
        """Fetch the check runs and statuses for a specific commit/ref."""
        target_repo = repo or self._repo
        if not target_repo or not self._token:
            # Fallback to gh CLI
            try:
                cmd = ["gh", "api", f"repos/{target_repo}/commits/{ref}/check-runs"]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    runs = []
                    for run in data.get("check_runs", []):
                        runs.append({
                            "name": run.get("name"),
                            "status": run.get("status"),
                            "conclusion": run.get("conclusion"),
                            "url": run.get("html_url"),
                            "type": "check_run"
                        })
                    return runs
            except Exception:
                pass
            return []

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Prismatic-Engine"
        }

        check_runs_url = f"https://api.github.com/repos/{target_repo}/commits/{ref}/check-runs"
        statuses_url = f"https://api.github.com/repos/{target_repo}/commits/{ref}/status"

        results = []

        # 1. Fetch check runs
        try:
            req = urllib.request.Request(check_runs_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for run in data.get("check_runs", []):
                    results.append({
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "url": run.get("html_url"),
                        "type": "check_run"
                    })
        except Exception:
            pass

        # 2. Fetch commit status
        try:
            req = urllib.request.Request(statuses_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for status in data.get("statuses", []):
                    results.append({
                        "name": status.get("context"),
                        "status": "completed",
                        "conclusion": "success" if status.get("state") == "success" else "failure",
                        "url": status.get("target_url"),
                        "type": "status"
                    })
        except Exception:
            pass

        return results

    def add_pr_comment(self, pr_number: int, body: str, repo: Optional[str] = None) -> bool:
        """Add a general comment to a pull request (via issues API)."""
        target_repo = repo or self._repo
        if not target_repo or not self._token:
            # Fallback to gh CLI
            try:
                cmd = ["gh", "pr", "comment", str(pr_number), "-R", str(target_repo), "-b", body]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return res.returncode == 0
            except Exception:
                return False

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Prismatic-Engine"
        }

        payload = {"body": body}

        req = urllib.request.Request(
            f"https://api.github.com/repos/{target_repo}/issues/{pr_number}/comments",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except Exception as exc:
            print(f"[GitHubProvider] Failed to comment via API: {exc}")
            # Fallback to gh CLI
            try:
                cmd = ["gh", "pr", "comment", str(pr_number), "-R", str(target_repo), "-b", body]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return res.returncode == 0
            except Exception as cli_exc:
                print(f"[GitHubProvider] Fallback gh CLI comment failed: {cli_exc}")
            return False

    def post_pr_review(self, pr_number: int, event: str, body: str, comments: Optional[list[dict[str, Any]]] = None, repo: Optional[str] = None) -> bool:
        """
        Post a PR review outcome (APPROVE, REQUEST_CHANGES, COMMENT) with optional inline comments.
        
        Args:
            pr_number: PR number.
            event: The review action (APPROVE, REQUEST_CHANGES, COMMENT).
            body: The main review summary comment.
            comments: Optional list of dictionaries with format:
                      {"path": "filename", "line": line_number, "body": "comment text"}
        """
        target_repo = repo or self._repo
        if not target_repo or not self._token:
            # Fallback to gh CLI (limited review support, basic comment review)
            try:
                flag = "--comment"
                if event == "APPROVE":
                    flag = "--approve"
                elif event == "REQUEST_CHANGES":
                    flag = "--request-changes"
                cmd = ["gh", "pr", "review", str(pr_number), "-R", str(target_repo), flag, "-b", body]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return res.returncode == 0
            except Exception:
                return False

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Prismatic-Engine"
        }

        payload = {
            "event": event,
            "body": body
        }
        if comments:
            payload["comments"] = comments

        req = urllib.request.Request(
            f"https://api.github.com/repos/{target_repo}/pulls/{pr_number}/reviews",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status in (200, 201)
        except Exception as exc:
            print(f"[GitHubProvider] Failed to post PR review: {exc}")
            # Try gh CLI fallback
            try:
                flag = "--comment"
                if event == "APPROVE":
                    flag = "--approve"
                elif event == "REQUEST_CHANGES":
                    flag = "--request-changes"
                cmd = ["gh", "pr", "review", str(pr_number), "-R", str(target_repo), flag, "-b", body]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return res.returncode == 0
            except Exception as cli_exc:
                print(f"[GitHubProvider] Fallback gh CLI review failed: {cli_exc}")
            return False

    @staticmethod
    def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
        """
        Verify GitHub webhook payload HMAC signature.
        """
        if not signature or not secret:
            return False
        
        if signature.startswith("sha256="):
            signature = signature[7:]
            
        mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
        expected = mac.hexdigest()
        return hmac.compare_digest(expected, signature)
