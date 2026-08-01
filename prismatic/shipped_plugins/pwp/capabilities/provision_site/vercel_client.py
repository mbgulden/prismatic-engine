"""vercel_client — minimal Vercel REST API v10 client for PWP provision_site Phase 3.

Mirrors the shape of cloudflare_client.py so step implementations can
swap between platforms with the same idioms. Supports the four operations
the PWP "add site" workflow needs:

  - project_lookup(name)   -> Optional[Project]
  - project_create(name, framework='other', git_repo=None)
                         -> Project
  - env_set(project_id, key, value, target=['production','preview'])
                         -> EnvVar
  - domain_add(project_id, domain) -> Domain

Plus two convenience helpers that are useful for analytics install:
  - list_env(project_id)  -> list[EnvVar]   (so step can check if a key
                                            already exists before POST)
  - find_env(project_id, key) -> Optional[EnvVar]

Authentication: Bearer token from env var `VERCEL_TOKEN` (or `VERCEL_API_TOKEN`).
The Vercel API uses `https://api.vercel.com` as the base URL. The team is
inferred from `VERCEL_TEAM_ID` (optional); if unset, requests hit the user's
personal account.

This module is INTENTIONALLY minimal — no SDK dependency, no async, just
plain requests. The PWP provisioner runs sequentially so we don't need
concurrency.

References:
  - https://vercel.com/docs/rest-api
  - https://vercel.com/docs/rest-api/projects
  - https://vercel.com/docs/rest-api/endpoints#projects
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

VERCEL_API_BASE = "https://api.vercel.com"


# -- Exceptions -----------------------------------------------------------

class VercelError(RuntimeError):
    """Non-2xx response from the Vercel REST API."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"Vercel API error {status}/{code}: {message}")


# -- Data classes ---------------------------------------------------------

@dataclass(frozen=True)
class Project:
    """A Vercel project (subset of API response)."""
    id: str
    name: str
    framework: str
    account_id: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Project:
        return cls(
            id=payload["id"],
            name=payload["name"],
            framework=payload.get("framework") or "other",
            account_id=payload.get("accountId", ""),
        )


@dataclass(frozen=True)
class EnvVar:
    """A Vercel project environment variable."""
    id: str
    key: str
    value: str
    target: list[str]
    type: str  # 'encrypted' | 'plain' | 'sensitive'

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> EnvVar:
        return cls(
            id=payload.get("id", ""),
            key=payload["key"],
            value=payload.get("value", ""),
            target=payload.get("target") or [],
            type=payload.get("type", "encrypted"),
        )


@dataclass(frozen=True)
class Domain:
    """A domain attached to a Vercel project."""
    name: str
    verified: bool

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Domain:
        return cls(
            name=payload["name"],
            verified=bool(payload.get("verified")),
        )


# -- Client ---------------------------------------------------------------

class VercelClient:
    """Minimal synchronous client for the Vercel REST API."""

    def __init__(
        self,
        token: str,
        *,
        team_id: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff: float = 1.5,
        _token_source: str = "explicit",
    ) -> None:
        if not token or not token.strip():
            raise ValueError("VercelClient requires a non-empty token")
        self._token = token.strip()
        self._team_id = (team_id or "").strip() or None
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._token_source = _token_source

    @property
    def token_source(self) -> str:
        return self._token_source

    @property
    def team_id(self) -> str | None:
        return self._team_id

    # -- Construction helpers ----------------------------------------------

    @classmethod
    def from_env(cls) -> VercelClient:
        """Construct from a Vercel API token env var.

        Accepts (in order of precedence):
          - VERCEL_TOKEN         (the convention used by `vercel login`)
          - VERCEL_API_TOKEN     (alias)
        The team is read from VERCEL_TEAM_ID (optional).
        """
        candidates = ("VERCEL_TOKEN", "VERCEL_API_TOKEN")
        for var in candidates:
            val = os.environ.get(var, "").strip()
            if val:
                return cls(token=val, team_id=os.environ.get("VERCEL_TEAM_ID"),
                           _token_source=var)
        raise ValueError(
            "No Vercel API token configured. Set VERCEL_TOKEN (or "
            "VERCEL_API_TOKEN) to a personal access token from "
            "https://vercel.com/account/tokens"
        )

    # -- Internals ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> dict[str, Any]:
        url = VERCEL_API_BASE + path
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
        if self._team_id:
            sep = "&" if "?" in url else "?"
            url += sep + "teamId=" + urllib.parse.quote(self._team_id)

        body_bytes = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
        }
        if body_bytes is not None:
            headers["Content-Type"] = "application/json"

        last_err: Exception | None = None
        # We always do at least one attempt; we retry up to max_retries
        # additional times on 429/5xx or URLError.
        total_attempts = max(1, self._max_retries)
        for attempt in range(1, total_attempts + 1):
            try:
                req = urllib.request.Request(
                    url, data=body_bytes, headers=headers, method=method,
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    if not raw:
                        return {}
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                try:
                    err_payload = json.loads(err_body)
                except Exception:
                    err_payload = {"error": {"code": str(e.code), "message": err_body}}
                err = err_payload.get("error", err_payload)
                code = str(err.get("code", e.code))
                msg = str(err.get("message", err_body))
                # Retry on 429 + 5xx (with retries remaining)
                if e.code in (429, 500, 502, 503, 504) and attempt < total_attempts:
                    time.sleep(self._retry_backoff ** attempt)
                    last_err = VercelError(e.code, code, msg)
                    continue
                raise VercelError(e.code, code, msg) from None
            except urllib.error.URLError as e:
                # URLError covers connection refused, DNS failures, etc.
                last_err = e
                if attempt < total_attempts:
                    time.sleep(self._retry_backoff ** attempt)
                    continue
                raise VercelError(0, "url_error", str(e)) from None

        # Defensive: should never reach here because the URLError branch
        # above always raises on the final attempt. If we get here, last_err
        # is non-None (we've had at least one failure to set it).
        raise VercelError(
            0,
            "retry_exhausted",
            str(last_err) if last_err else "no attempts made",
        )

    # -- Projects ---------------------------------------------------------

    def project_lookup(self, name: str) -> Project | None:
        """Find a project by name; returns None if not found.

        Uses GET /v9/projects/{nameOrId}. 404 -> None, anything else -> VercelError.
        """
        try:
            payload = self._request("GET", f"/v9/projects/{urllib.parse.quote(name)}")
        except VercelError as e:
            if e.status == 404:
                return None
            raise
        return Project.from_api(payload)

    def project_create(
        self,
        name: str,
        *,
        framework: str = "other",
        git_repo: dict[str, Any] | None = None,
    ) -> Project:
        """Create a new project via POST /v9/projects.

        Args:
          name: project name (lowercase, alphanumeric + hyphens).
          framework: 'nextjs' | 'vite' | 'astro' | 'other' | etc.
          git_repo: optional {'type': 'github', 'repo': 'owner/repo'} to wire CI/CD.
        """
        body: dict[str, Any] = {"name": name, "framework": framework}
        if git_repo:
            body["gitRepository"] = git_repo
        payload = self._request("POST", "/v9/projects", json_body=body)
        return Project.from_api(payload)

    # -- Environment variables --------------------------------------------

    def list_env(self, project_id_or_name: str) -> list[EnvVar]:
        """Return all env vars for a project (decrypted values when readable)."""
        payload = self._request(
            "GET",
            f"/v9/projects/{urllib.parse.quote(project_id_or_name)}/env",
        )
        envs = payload.get("envs", payload if isinstance(payload, list) else [])
        return [EnvVar.from_api(e) for e in envs]

    def find_env(self, project_id_or_name: str, key: str) -> EnvVar | None:
        """Return the env var with this key, or None."""
        for e in self.list_env(project_id_or_name):
            if e.key == key:
                return e
        return None

    def env_set(
        self,
        project_id_or_name: str,
        key: str,
        value: str,
        *,
        target: list[str] | None = None,
        secret: bool = True,
    ) -> EnvVar:
        """Create or update an env var.

        Vercel's API does NOT support PATCH on env vars — you must POST
        to create a NEW env ID for each "update". Vercel's UI hides this
        by deleting+recreating transparently. We mimic that here:
        if the key exists, we delete it first, then POST.

        Args:
          project_id_or_name: project name or id
          key: env var name (e.g. 'NEXT_PUBLIC_GA_MEASUREMENT_ID')
          value: env var value
          target: list of environments — defaults to ['production', 'preview']
          secret: if True (default), the value is stored encrypted and
                  only visible to deployments. Set False for public
                  `NEXT_PUBLIC_*` vars that are bundled into client JS
                  (these must be plaintext in the API).
        """
        target = target or ["production", "preview"]
        # Mimic Vercel UI: delete-then-create for idempotency
        existing = self.find_env(project_id_or_name, key)
        if existing and existing.id:
            try:
                self._request(
                    "DELETE",
                    f"/v9/projects/{urllib.parse.quote(project_id_or_name)}/env/{urllib.parse.quote(existing.id)}",
                )
            except VercelError as e:
                # 404 means it was already gone — fine
                if e.status != 404:
                    raise
        body = {
            "key": key,
            "value": value,
            "target": target,
            "type": "sensitive" if secret else "plain",
        }
        payload = self._request(
            "POST",
            f"/v9/projects/{urllib.parse.quote(project_id_or_name)}/env",
            json_body=body,
        )
        # POST response is the created EnvVar
        return EnvVar.from_api(payload)

    # -- Domains -----------------------------------------------------------

    def domain_add(self, project_id_or_name: str, domain: str) -> Domain:
        """Add a domain to a project via POST /v9/projects/{id}/domains.

        The user must separately configure DNS (typically pointing a CNAME
        to cname.vercel-dns.com) before Vercel can issue the cert.
        """
        payload = self._request(
            "POST",
            f"/v9/projects/{urllib.parse.quote(project_id_or_name)}/domains",
            json_body={"name": domain},
        )
        return Domain.from_api(payload)

    # -- Deployments (skeleton) -------------------------------------------

    def deployment_list(self, project_id_or_name: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return the most recent deployments for a project."""
        payload = self._request(
            "GET",
            "/v6/deployments",
            query={"projectId": project_id_or_name, "limit": str(limit)},
        )
        return payload.get("deployments", [])
