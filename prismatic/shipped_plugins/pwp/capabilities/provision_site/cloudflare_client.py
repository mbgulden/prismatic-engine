"""cloudflare_client — minimal Cloudflare API v4 client for PWP provision_site.

The provisioner needs to:
  1. Create a zone for a new domain (or look up an existing one)
  2. Manage DNS records (TXT for verification, CNAME for Pages, etc.)
  3. Read zone settings (nameservers, status, plan)
  4. Trigger Cloudflare Pages deployments (later)

This client is intentionally minimal: it covers what the provisioner
needs without bringing in the full `cloudflare` Python SDK. It's a
thin `requests`-based wrapper with retries, rate-limit awareness, and
typed response shapes.

Authentication: a single shared API token via the `CF_API_TOKEN` env
var. The token must have at minimum:
  - Zone > Zone:Edit (for creating new zones — Phase 1)
  - Zone > DNS:Edit (for DNS records — Phase 1)
  - Zone > Zone:Read (for reading zone state)

For Phase 1 we deliberately do NOT need:
  - Account > Account Settings:Read (no account-level operations)
  - Zone > Page Rules (Pages API is separate, used in Phase 2)

The token is shared across all provisioning operations. Multi-tenant
auth is a Phase 3 concern.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


CF_API_BASE = "https://api.cloudflare.com/client/v4"


@dataclass
class CloudflareError(Exception):
    """Raised when the Cloudflare API returns a non-2xx response."""
    status_code: int
    code: int  # Cloudflare's error code (10000-range)
    message: str
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:
        return f"Cloudflare API error {self.status_code}/{self.code}: {self.message}"


@dataclass
class Zone:
    """A Cloudflare zone (a domain)."""
    id: str
    name: str
    status: str  # "active", "pending", "initializing", "moved", "deleted", "deactivated"
    nameservers: List[str] = field(default_factory=list)
    plan: str = "free"
    paused: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "Zone":
        return cls(
            id=payload["id"],
            name=payload["name"],
            status=payload.get("status", "unknown"),
            nameservers=payload.get("name_servers", []),
            plan=(payload.get("plan") or {}).get("name", "free"),
            paused=payload.get("paused", False),
            raw=payload,
        )


@dataclass
class DNSRecord:
    """A DNS record on a Cloudflare zone."""
    id: str
    type: str  # A, AAAA, CNAME, TXT, MX, etc.
    name: str  # e.g. "_dmarc.example.com" or "example.com"
    content: str
    ttl: int = 1  # 1 = automatic
    proxied: bool = False
    comment: str = ""

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "DNSRecord":
        return cls(
            id=payload["id"],
            type=payload["type"],
            name=payload["name"],
            content=payload["content"],
            ttl=payload.get("ttl", 1),
            proxied=payload.get("proxied", False),
            comment=payload.get("comment", ""),
        )


class CloudflareClient:
    """Thin Cloudflare API v4 client.

    Construction:
      cf = CloudflareClient.from_env()  # reads CF_API_TOKEN
      # OR
      cf = CloudflareClient(token="...")

    Usage:
      zone = cf.zone_create("example.com")
      cf.dns_record_create(zone.id, type="TXT", name="_pwp-verify",
                          content="pwp-verify=abc123")
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff: float = 1.5,
    ) -> None:
        if not token:
            raise ValueError(
                "Cloudflare token is empty. Set CF_API_TOKEN env var or pass "
                "token=... explicitly."
            )
        self._token = token
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    @classmethod
    def from_env(cls) -> "CloudflareClient":
        """Construct from `CF_API_TOKEN` env var. Raises ValueError if unset."""
        token = os.environ.get("CF_API_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "CF_API_TOKEN env var is not set. Set it to a Cloudflare API "
                "token with Zone:Edit + DNS:Edit + Zone:Read scopes."
            )
        return cls(token=token)

    # -- HTTP helpers -------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a Cloudflare API request with retries.

        Cloudflare rate-limits aggressively (especially for free plans).
        A 429 response is honored with a Retry-After header. Other 5xx
        responses are retried with exponential backoff. 4xx errors are
        surfaced immediately (no retry — they signal a client bug).
        """
        url = f"{CF_API_BASE}{path}"
        last_exc: Optional[CloudflareError] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                last_exc = CloudflareError(
                    status_code=0, code=0, message=f"network error: {exc}"
                )
                if attempt >= self._max_retries:
                    raise last_exc from exc
                time.sleep(self._retry_backoff ** attempt)
                continue

            if resp.status_code == 429:
                # Rate-limited. Honor Retry-After if present.
                retry_after = float(resp.headers.get("Retry-After", "1"))
                if attempt >= self._max_retries:
                    raise CloudflareError(
                        status_code=429, code=0,
                        message=f"rate-limited after {self._max_retries} retries",
                    )
                time.sleep(retry_after)
                continue

            if 500 <= resp.status_code < 600:
                last_exc = CloudflareError(
                    status_code=resp.status_code, code=0,
                    message=resp.text[:500],
                )
                if attempt >= self._max_retries:
                    raise last_exc
                time.sleep(self._retry_backoff ** attempt)
                continue

            payload = resp.json() if resp.text else {}
            if not payload.get("success", False):
                errs = payload.get("errors", []) or []
                raise CloudflareError(
                    status_code=resp.status_code,
                    code=(errs[0].get("code", 0) if errs else 0),
                    message=(errs[0].get("message", resp.text[:500]) if errs else resp.text[:500]),
                    errors=errs,
                )
            return payload

        # Unreachable — last_exc is always set if we exit the loop.
        assert last_exc is not None
        raise last_exc

    # -- Zone operations ----------------------------------------------

    def zone_list(self, name: Optional[str] = None) -> List[Zone]:
        """List zones, optionally filtered by exact domain name.

        Cloudflare's zone list endpoint paginates at ~50 zones per
        page. For Phase 1 we expect < 100 zones, so we walk pages.
        """
        zones: List[Zone] = []
        page = 1
        while True:
            params: Dict[str, Any] = {"page": page, "per_page": 50}
            if name is not None:
                params["name"] = name
            payload = self._request("GET", "/zones", params=params)
            batch = [Zone.from_api(z) for z in payload.get("result", [])]
            zones.extend(batch)
            total_pages = payload.get("result_info", {}).get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        return zones

    def zone_lookup(self, name: str) -> Optional[Zone]:
        """Find a zone by exact domain name. Returns None if not present."""
        for z in self.zone_list(name=name):
            if z.name == name:
                return z
        return None

    def zone_create(
        self,
        name: str,
        *,
        account_id: Optional[str] = None,
        jump_start: bool = True,
        type_: str = "full",
    ) -> Zone:
        """Create a new zone (domain) on Cloudflare.

        `jump_start=True` lets Cloudflare pre-scan for existing DNS
        records (recommended). `type_` is "full" (Cloudflare manages
        DNS) or "partial" (CNAME setup).
        """
        body: Dict[str, Any] = {"name": name, "type": type_, "jump_start": jump_start}
        if account_id:
            body["account"] = {"id": account_id}
        payload = self._request("POST", "/zones", json_body=body)
        return Zone.from_api(payload["result"])

    def zone_get(self, zone_id: str) -> Zone:
        """Fetch a zone by ID."""
        payload = self._request("GET", f"/zones/{zone_id}")
        return Zone.from_api(payload["result"])

    # -- DNS record operations ----------------------------------------

    def dns_list(
        self,
        zone_id: str,
        *,
        type_: Optional[str] = None,
        name: Optional[str] = None,
    ) -> List[DNSRecord]:
        """List DNS records for a zone, optionally filtered."""
        records: List[DNSRecord] = []
        page = 1
        while True:
            params: Dict[str, Any] = {"page": page, "per_page": 100}
            if type_:
                params["type"] = type_
            if name:
                params["name"] = name
            payload = self._request("GET", f"/zones/{zone_id}/dns_records", params=params)
            records.extend(DNSRecord.from_api(r) for r in payload.get("result", []))
            total_pages = payload.get("result_info", {}).get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        return records

    def dns_create(
        self,
        zone_id: str,
        *,
        type_: str,
        name: str,
        content: str,
        ttl: int = 1,
        proxied: bool = False,
        comment: str = "",
    ) -> DNSRecord:
        """Create a DNS record on a zone."""
        body: Dict[str, Any] = {
            "type": type_,
            "name": name,
            "content": content,
            "ttl": ttl,
            "proxied": proxied,
        }
        if comment:
            body["comment"] = comment
        payload = self._request("POST", f"/zones/{zone_id}/dns_records", json_body=body)
        return DNSRecord.from_api(payload["result"])

    def dns_update(
        self,
        zone_id: str,
        record_id: str,
        *,
        type_: Optional[str] = None,
        name: Optional[str] = None,
        content: Optional[str] = None,
        ttl: Optional[int] = None,
        proxied: Optional[bool] = None,
        comment: Optional[str] = None,
    ) -> DNSRecord:
        """Update an existing DNS record."""
        body: Dict[str, Any] = {}
        if type_ is not None:
            body["type"] = type_
        if name is not None:
            body["name"] = name
        if content is not None:
            body["content"] = content
        if ttl is not None:
            body["ttl"] = ttl
        if proxied is not None:
            body["proxied"] = proxied
        if comment is not None:
            body["comment"] = comment
        payload = self._request(
            "PUT", f"/zones/{zone_id}/dns_records/{record_id}", json_body=body
        )
        return DNSRecord.from_api(payload["result"])

    def dns_delete(self, zone_id: str, record_id: str) -> None:
        """Delete a DNS record."""
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
