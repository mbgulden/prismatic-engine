"""github_client — minimal GitHub REST API v3 client for PWP provision_site Phase 4.

Mirrors the shape of cloudflare_client.py / vercel_client.py /
stripe_client.py so step implementations can swap between platforms
with a uniform idiom.

The GitHub REST API is documented at https://docs.github.com/en/rest.
Base URL: https://api.github.com. Authentication uses either:
  - token-in-header: Authorization: token <pat>
  - token-in-header: Authorization: Bearer <ghs_/ghu_/fine-grained-token>

We do not use Basic auth with username/password.

The minimal subset we need:
  - GET /repos/{owner}/{repo}                         → repo metadata
  - GET /repos/{owner}/{repo}/branches                → branch list
  - GET /repos/{owner}/{repo}/branches/{branch}       → branch HEAD
  - GET /repos/{owner}/{repo}/commits/{sha}           → commit lookup
  - GET /repos/{owner}/{repo}/contents/{path}         → read file
  - GET /repos/{owner}/{repo}/hooks                   → list webhooks
  - POST /repos/{owner}/{repo}/hooks                  → create webhook
  - DELETE /repos/{owner}/{repo}/hooks/{id}           → delete webhook
  - GET /repos/{owner}/{repo}/actions/secrets/public-key  → public key for secrets
  - PUT /repos/{owner}/{repo}/actions/secrets/{name}  → write secret
  - GET /search/repositories?q=user:{user}            → list user's repos
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

GITHUB_API_URL = "https://api.github.com"

# Default pagination — most endpoints cap at 30 results per page.
DEFAULT_PER_PAGE = 30
MAX_PER_PAGE = 100


class GitHubError(Exception):
    """Raised when the GitHub API returns a non-2xx response.

    Captures the HTTP status, the response body (truncated), and the
    request path so failures are diagnosable.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: str = "",
        path: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.path = path


@dataclass
class GitHubRepo:
    """Snapshot of repo metadata that the provision_site cares about."""

    full_name: str            # "owner/name"
    default_branch: str
    private: bool
    description: str
    permissions_push: bool
    permissions_admin: bool
    permissions_maintain: bool
    html_url: str
    clone_url: str            # git clone URL (HTTPS with token)
    ssh_url: str
    topics: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class GitHubBranch:
    name: str
    sha: str                 # HEAD commit SHA
    protected: bool


@dataclass
class GitHubCommit:
    sha: str
    message: str
    author_name: str
    author_email: str
    date: str                # ISO-8601 from GitHub
    parents: List[str]


class GitHubClient:
    """Minimal GitHub REST API client."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = GITHUB_API_URL,
        max_retries: int = 3,
        retry_backoff: float = 1.5,
        token_source: str = "explicit",
        user_agent: str = "pwp-provision-site",
    ) -> None:
        if not token:
            raise ValueError("GitHub token is required (got empty string)")
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._token_source = token_source
        self._user_agent = user_agent

    @property
    def token_source(self) -> str:
        return self._token_source

    # -- factory --------------------------------------------------------
    @classmethod
    def from_env(cls) -> "GitHubClient":
        """Construct from environment variables.

        Precedence: GITHUB_TOKEN > GH_TOKEN > GITHUB_PAT.

        Falls back to auth_loader.get_secret('github_token') which
        discovers the credential in the active Hermes profile's .env
        or any project .env file.
        """
        token = (
            os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_PAT")
        )
        source = (
            "GITHUB_TOKEN" if os.environ.get("GITHUB_TOKEN")
            else "GH_TOKEN" if os.environ.get("GH_TOKEN")
            else "GITHUB_PAT" if os.environ.get("GITHUB_PAT")
            else None
        )
        if not token:
            from . import auth_loader
            result = auth_loader.get_secret("github_token")
            if result.found:
                token = result.value
                source = f"auth_loader:{result.source}"
                result.export_to_env()
        if not token:
            raise ValueError(
                "No GitHub token configured. Set GITHUB_TOKEN env var, "
                "or run `gh auth login`, or register via: "
                "pwp-kpi-tracker auth register --type github_token "
                "--value <ghp_...>"
            )
        return cls(token, token_source=source or "explicit")

    # -- core HTTP ------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any], str]:
        """Issue an HTTP request to the GitHub API.

        Returns (status_code, parsed_json_body, raw_text_body).
        Handles 429 rate-limit retries with exponential backoff.
        """
        url = self._api_url + path
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None},
                doseq=True,
            )
            url = f"{url}?{qs}"
        data = json.dumps(json_body).encode("utf-8") if json_body else None
        for attempt in range(self._max_retries + 1):
            req = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={
                    "Authorization": f"token {self._token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": self._user_agent,
                    "X-GitHub-Api-Version": "2022-11-28",
                    **(
                        {"Content-Type": "application/json"} if json_body else {}
                    ),
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    body: Dict[str, Any] = {}
                    if raw:
                        try:
                            body = json.loads(raw)
                        except json.JSONDecodeError:
                            # Some endpoints return non-JSON (rare for GitHub)
                            body = {}
                    return resp.status, body, raw.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                raw = e.read() if e.fp else b""
                text = raw.decode("utf-8", errors="replace")
                # 401 = bad token, don't retry
                if e.code == 401:
                    raise GitHubError(
                        f"GitHub auth failed (HTTP 401): token is invalid or "
                        f"missing required scopes. Path: {path}",
                        status_code=401,
                        body=text[:500],
                        path=path,
                    ) from e
                # 404 is a real "not found" — don't retry
                if e.code == 404:
                    raise GitHubError(
                        f"GitHub resource not found (HTTP 404): {path}",
                        status_code=404,
                        body=text[:500],
                        path=path,
                    ) from e
                # 429 = explicit rate-limit response; retry
                if e.code == 429:
                    if attempt < self._max_retries:
                        time.sleep(self._retry_backoff ** attempt)
                        continue
                # 403 with rate-limit — retry
                if e.code == 403 and b"rate limit" in raw.lower():
                    if attempt < self._max_retries:
                        time.sleep(self._retry_backoff ** attempt)
                        continue
                # 5xx — retry
                if e.code >= 500 and attempt < self._max_retries:
                    time.sleep(self._retry_backoff ** attempt)
                    continue
                # 4xx (other than 401/404/429) — don't retry
                if 400 <= e.code < 500:
                    raise GitHubError(
                        f"GitHub API error: HTTP {e.code} on {method} {path}: "
                        f"{text[:300]}",
                        status_code=e.code,
                        body=text[:500],
                        path=path,
                    ) from e
                raise GitHubError(
                    f"GitHub API error: HTTP {e.code} on {method} {path}: "
                    f"{text[:300]}",
                    status_code=e.code,
                    body=text[:500],
                    path=path,
                ) from e
            except urllib.error.URLError as e:
                if attempt < self._max_retries:
                    time.sleep(self._retry_backoff ** attempt)
                    continue
                raise GitHubError(
                    f"GitHub API unreachable: {e.reason}",
                    path=path,
                ) from e
        # Loop exhausted without success (e.g. all retries 429'd)
        raise GitHubError(
            f"GitHub API exhausted retries on {method} {path}",
            path=path,
        )

    # -- auth / identity -------------------------------------------------
    def get_authenticated_user(self) -> Dict[str, Any]:
        """GET /user — return the authenticated user's profile.

        Useful for confirming the token works and discovering the
        default login for downstream operations.
        """
        _status, body, _text = self._request("GET", "/user")
        return body

    def validate(self) -> Dict[str, Any]:
        """Validate the token; return user info on success."""
        return self.get_authenticated_user()

    # -- repo operations -------------------------------------------------
    def get_repo(self, full_name: str) -> GitHubRepo:
        """GET /repos/{owner}/{repo} — fetch metadata.

        Raises GitHubError(404) if the repo is missing or the token
        doesn't have access.
        """
        _status, body, _text = self._request("GET", f"/repos/{full_name}")
        perms = body.get("permissions", {})
        return GitHubRepo(
            full_name=body.get("full_name", full_name),
            default_branch=body.get("default_branch", "main"),
            private=body.get("private", False),
            description=body.get("description", "") or "",
            permissions_push=perms.get("push", False),
            permissions_admin=perms.get("admin", False),
            permissions_maintain=perms.get("maintain", False),
            html_url=body.get("html_url", ""),
            clone_url=body.get("clone_url", ""),
            ssh_url=body.get("ssh_url", ""),
            topics=body.get("topics", []) or [],
            raw=body,
        )

    def repo_exists(self, full_name: str) -> bool:
        """Check repo existence without raising — returns False on 404."""
        try:
            self.get_repo(full_name)
            return True
        except GitHubError as e:
            if e.status_code == 404:
                return False
            raise

    def list_branches(self, full_name: str) -> List[GitHubBranch]:
        """GET /repos/{owner}/{repo}/branches — return all branches."""
        branches: List[GitHubBranch] = []
        page = 1
        while True:
            _status, body, _text = self._request(
                "GET",
                f"/repos/{full_name}/branches",
                params={"per_page": MAX_PER_PAGE, "page": page},
            )
            if not isinstance(body, list):
                break
            for b in body:
                branches.append(GitHubBranch(
                    name=b["name"],
                    sha=b["commit"]["sha"],
                    protected=b.get("protected", False),
                ))
            if len(body) < MAX_PER_PAGE:
                break
            page += 1
        return branches

    def get_default_branch(
        self, full_name: str
    ) -> Tuple[str, str]:
        """Resolve (default_branch_name, HEAD_sha)."""
        repo = self.get_repo(full_name)
        branches = self.list_branches(full_name)
        # Find the branch whose name == default_branch
        for b in branches:
            if b.name == repo.default_branch:
                return b.name, b.sha
        # Fallback: return first branch + its sha
        if branches:
            return branches[0].name, branches[0].sha
        return repo.default_branch, ""

    def get_commit(self, full_name: str, sha: str) -> GitHubCommit:
        """GET /repos/{owner}/{repo}/commits/{sha}."""
        _status, body, _text = self._request(
            "GET", f"/repos/{full_name}/commits/{sha}"
        )
        author = body.get("commit", {}).get("author", {}) or {}
        return GitHubCommit(
            sha=body.get("sha", sha),
            message=body.get("commit", {}).get("message", "") or "",
            author_name=author.get("name", "") or "",
            author_email=author.get("email", "") or "",
            date=author.get("date", "") or "",
            parents=[p["sha"] for p in body.get("parents", [])],
        )

    def get_file(
        self, full_name: str, path: str, *, ref: Optional[str] = None
    ) -> Tuple[str, str]:
        """GET /repos/{owner}/{repo}/contents/{path}.

        Returns (decoded_content, sha). Useful for reading vercel.json,
        wrangler.toml, etc.
        """
        params = {"ref": ref} if ref else None
        _status, body, _text = self._request(
            "GET",
            f"/repos/{full_name}/contents/{urllib.parse.quote(path, safe='/')}",
            params=params,
        )
        import base64
        content_b64 = body.get("content", "")
        # GitHub returns base64 with embedded newlines; strip them.
        content = base64.b64decode(
            content_b64.replace("\n", "")
        ).decode("utf-8", errors="replace")
        return content, body.get("sha", "")

    def search_user_repos(
        self, user: str, *, limit: int = 50
    ) -> List[GitHubRepo]:
        """Search for repos owned by `user`.

        Uses /search/repositories?q=user:<user> — this is rate-limited
        separately and capped by GitHub at 1000 results total.
        """
        repos: List[GitHubRepo] = []
        page = 1
        while len(repos) < limit:
            _status, body, _text = self._request(
                "GET",
                "/search/repositories",
                params={
                    "q": f"user:{user}",
                    "per_page": min(MAX_PER_PAGE, limit - len(repos)),
                    "page": page,
                },
            )
            items = body.get("items", []) or []
            for item in items:
                perms = item.get("permissions", {}) or {}
                repos.append(GitHubRepo(
                    full_name=item.get("full_name", ""),
                    default_branch=item.get("default_branch", "main"),
                    private=item.get("private", False),
                    description=item.get("description", "") or "",
                    permissions_push=perms.get("push", False),
                    permissions_admin=perms.get("admin", False),
                    permissions_maintain=perms.get("maintain", False),
                    html_url=item.get("html_url", ""),
                    clone_url=item.get("clone_url", ""),
                    ssh_url=item.get("ssh_url", ""),
                    topics=item.get("topics", []) or [],
                    raw=item,
                ))
            total = body.get("total_count", 0)
            if not items or len(repos) >= total:
                break
            page += 1
        return repos

    def list_webhooks(self, full_name: str) -> List[Dict[str, Any]]:
        """GET /repos/{owner}/{repo}/hooks — list webhooks."""
        _status, body, _text = self._request(
            "GET", f"/repos/{full_name}/hooks"
        )
        return body if isinstance(body, list) else []

    def create_webhook(
        self,
        full_name: str,
        *,
        url: str,
        events: List[str],
        content_type: str = "json",
        active: bool = True,
        secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /repos/{owner}/{repo}/hooks — create a webhook.

        Useful for wiring Stripe / Linear / Zapier callbacks into
        GitHub events like `push` or `release`.
        """
        config: Dict[str, Any] = {
            "url": url,
            "content_type": content_type,
        }
        if secret:
            config["secret"] = secret
        _status, body, _text = self._request(
            "POST",
            f"/repos/{full_name}/hooks",
            json_body={
                "name": "web",
                "active": active,
                "events": events,
                "config": config,
            },
        )
        return body

    def delete_webhook(self, full_name: str, hook_id: int) -> None:
        """DELETE /repos/{owner}/{repo}/hooks/{id}."""
        _status, body, _text = self._request(
            "DELETE", f"/repos/{full_name}/hooks/{hook_id}"
        )

    # -- GitHub Actions secrets -----------------------------------------
    def get_actions_public_key(
        self, full_name: str
    ) -> Tuple[str, str]:
        """GET /repos/{owner}/{repo}/actions/secrets/public-key.

        Returns (key_id, key_base64). Required to encrypt secrets
        before writing them with put_secret().
        """
        _status, body, _text = self._request(
            "GET", f"/repos/{full_name}/actions/secrets/public-key"
        )
        return body.get("key_id", ""), body.get("key", "")

    def put_secret(
        self,
        full_name: str,
        name: str,
        encrypted_value: str,
        key_id: str,
    ) -> None:
        """PUT /repos/{owner}/{repo}/actions/secrets/{name}.

        `encrypted_value` must be encrypted with the repo's public key
        using libsodium sealed-box — callers should use a dedicated
        crypto helper. We don't do the encryption in this client.
        """
        _status, body, _text = self._request(
            "PUT",
            f"/repos/{full_name}/actions/secrets/{name}",
            json_body={
                "encrypted_value": encrypted_value,
                "key_id": key_id,
            },
        )
