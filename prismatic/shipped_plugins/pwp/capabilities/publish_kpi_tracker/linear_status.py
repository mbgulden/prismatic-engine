"""linear_status — PWP publish-kpi Linear status cache (Phase 4.4).

Renders the current Linear task status (state, assignee, age) on each
per-site row in the dashboard. The polling layer is:

  1. `scan_status(force=False)` reads all `SUBMISSION_LOG_DIR/<slug>.json`
     files, picks the latest `linear_issue_id` for each site, and queries
     Linear once per site (subject to a TTL cache).
  2. Successful Linear responses are cached on disk at
     `os.environ.get('PWP_LINEAR_STATUS_CACHE', '<SUBMISSION_LOG_DIR>/linear-status-cache.json')`.
     The cache is keyed by `linear_issue_id`. The TTL default is 5 minutes,
     controlled by the `PWP_LINEAR_STATUS_TTL` env var (seconds).
  3. `LinearError` and transient errors are cached too (with a shorter
     TTL of 60s) so we don't hammer the API when something's wrong.
  4. `force=True` bypasses the cache and refetches every issue.

Return shape (per slug)::

    {
        "slug": "ezshare",
        "linear_issue_id": "...",
        "linear_issue_identifier": "GRO-4367",
        "linear_issue_url": "https://linear.app/.../GRO-4367",
        "state": "In Progress",
        "state_type": "started",
        "assignee_name": "Ned",
        "age_days": 2.3,
        "cached_at": "2026-07-30T...",
        "fetched_at": "2026-07-30T...",
        "error": None,  # or "rate_limited", "auth_failed", "not_found", "unknown"
    }

This module is intentionally framework-free. The render layer (`render_index`)
injects the status text into the per-site `<p class="pwp-kpi-site-actions">`
block we already ship in Phase 4.2.

Multi-tenant: each submission log may carry a `tenant_id`. The cache key
includes the issue_id only; tenant_id is propagated into the returned
shape so the renderer can show it next to the status.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Cache files live next to the submission logs by default.
SUBMISSION_LOG_DIR = Path(
    os.environ.get("PWP_FUNNEL_CONFIG_DIR", "/tmp/pwp-provisioning/funnel-config")
)
CACHE_PATH = Path(
    os.environ.get(
        "PWP_LINEAR_STATUS_CACHE",
        str(SUBMISSION_LOG_DIR / "linear-status-cache.json"),
    )
)

# Default TTLs (seconds). The "fresh" TTL is the happy path; the "error"
# TTL is shorter so we retry transient failures quickly without hammering.
DEFAULT_OK_TTL_SECONDS = int(os.environ.get("PWP_LINEAR_STATUS_TTL_OK", "300"))
DEFAULT_ERROR_TTL_SECONDS = int(os.environ.get("PWP_LINEAR_STATUS_TTL_ERR", "60"))


# ── Dataclass ─────────────────────────────────────────────────────────────
@dataclass
class LinearStatus:
    """A snapshot of a Linear issue's status for one site.

    The `error` field is None on success; otherwise it's a short slug
    (`rate_limited`, `auth_failed`, `not_found`, `unknown`) so the
    renderer can pick a friendly fallback label.
    """

    slug: str
    linear_issue_id: str = ""
    linear_issue_identifier: str = ""
    linear_issue_url: str = ""
    state: str = ""
    state_type: str = ""
    assignee_name: str = ""
    age_days: float = 0.0
    tenant_id: str = ""
    cached_at: str = ""
    fetched_at: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "linear_issue_id": self.linear_issue_id,
            "linear_issue_identifier": self.linear_issue_identifier,
            "linear_issue_url": self.linear_issue_url,
            "state": self.state,
            "state_type": self.state_type,
            "assignee_name": self.assignee_name,
            "age_days": self.age_days,
            "tenant_id": self.tenant_id,
            "cached_at": self.cached_at,
            "fetched_at": self.fetched_at,
            "error": self.error,
        }

    def __repr__(self) -> str:
        # Redact all sensitive fields for log safety. The full dataclass
        # is required for the renderer; __repr__ is for logs only.
        return (
            f"LinearStatus(slug={self.slug!r}, "
            f"identifier={self.linear_issue_identifier!r}, "
            f"state={self.state!r}, state_type={self.state_type!r}, "
            f"assignee_name={self.assignee_name!r}, "
            f"age_days={self.age_days!r}, "
            f"error={self.error!r})"
        )


# ── Cache I/O ────────────────────────────────────────────────────────────
def _read_cache(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Read the on-disk cache. Returns {} if missing or corrupt."""
    path = path if path is not None else CACHE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(cache: Dict[str, Dict[str, Any]], path: Optional[Path] = None) -> None:
    """Persist the cache atomically. Best-effort: never raises."""
    path = path if path is not None else CACHE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


# ── Submission log → issue_id mapping ────────────────────────────────────
def _latest_issue_id_for_site(slug: str) -> Optional[Dict[str, str]]:
    """Return the most recent submission's `linear_issue_id` + `tenant_id`.

    Reads `SUBMISSION_LOG_DIR/<slug>.json`. Each site's log holds the
    full submission history; the top-level `linear_issue_id` is the
    LATEST init-or-refinement dispatch. The site may have additional
    refinements appended in `refinements[]`, but for the dashboard we
    show the latest one only.
    """
    path = SUBMISSION_LOG_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    issue_id = data.get("linear_issue_id") or ""
    if not issue_id:
        return None
    return {
        "linear_issue_id": issue_id,
        "linear_issue_identifier": data.get("linear_issue_identifier", ""),
        "linear_issue_url": data.get("linear_issue_url", ""),
        "tenant_id": data.get("tenant_id", ""),
    }


def _age_days_from_iso(iso: str) -> float:
    """Compute the age in days from an ISO timestamp. Returns 0.0 on failure."""
    if not iso:
        return 0.0
    try:
        # Linear returns "...Z" or "+00:00" style timestamps.
        from datetime import datetime, timezone

        # Python 3.10's fromisoformat doesn't accept 'Z'; normalize.
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.0


# ── Linear API call ──────────────────────────────────────────────────────
def _call_linear_status(issue_id: str) -> Dict[str, Any]:
    """Fetch the issue via LinearClient. Returns a result dict.

    Always returns a dict, never raises. The dict has keys:
        `ok` (bool), `state`, `state_type`, `assignee_name`, `url`,
        `identifier`, `title`, `ts`, `error`.
    """
    try:
        # Lazy import so the publish_kpi_tracker module loads even when
        # the provision_site clients aren't available.
        from prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client import (
            LinearClient,
            LinearError,
        )

        client = LinearClient.from_env()
        issue = client.get_issue_status(issue_id)
        return {
            "ok": True,
            "state": issue.state,
            "state_type": issue.state_type,
            "assignee_name": issue.assignee_name or "",
            "url": issue.url,
            "identifier": issue.identifier,
            "title": issue.title,
            "ts": time.time(),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — best-effort, surface classification
        from prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client import LinearError

        if isinstance(exc, LinearError):
            status = getattr(exc, "status", 0) or 0
            if status == 401:
                err = "auth_failed"
            elif status == 404:
                err = "not_found"
            elif status == 429:
                err = "rate_limited"
            else:
                err = "unknown"
        else:
            err = "unknown"
        return {
            "ok": False,
            "state": "",
            "state_type": "",
            "assignee_name": "",
            "url": "",
            "identifier": "",
            "title": "",
            "ts": time.time(),
            "error": err,
        }


# ── Public API ────────────────────────────────────────────────────────────
def _cache_is_fresh(entry: Dict[str, Any], *, ttl_seconds: float) -> bool:
    """True if the cache entry was fetched within TTL seconds."""
    ts = entry.get("ts", 0)
    if not isinstance(ts, (int, float)) or ts <= 0:
        return False
    return (time.time() - ts) < ttl_seconds


def scan_status(
    *,
    force: bool = False,
    ok_ttl_seconds: int = DEFAULT_OK_TTL_SECONDS,
    error_ttl_seconds: int = DEFAULT_ERROR_TTL_SECONDS,
    cache_path: Optional[Path] = None,
) -> List[LinearStatus]:
    """Scan submission logs and return the current Linear status for each site.

    Honors the on-disk cache except when `force=True`. Errors are cached
    for the shorter `error_ttl_seconds` so we retry transient failures
    quickly without hammering the API.
    """
    if not SUBMISSION_LOG_DIR.exists():
        return []
    cache = _read_cache(cache_path)
    results: List[LinearStatus] = []
    wrote_cache = False

    for log_path in sorted(SUBMISSION_LOG_DIR.glob("*.json")):
        slug = log_path.stem
        meta = _latest_issue_id_for_site(slug)
        if not meta:
            continue
        issue_id = meta["linear_issue_id"]

        # Cache lookup.
        cached = cache.get(issue_id) if isinstance(cache.get(issue_id), dict) else None
        if cached and not force:
            ttl = error_ttl_seconds if cached.get("error") else ok_ttl_seconds
            if _cache_is_fresh(cached, ttl_seconds=ttl):
                age_days = _age_days_from_iso(cached.get("fetched_at_iso", ""))
                ls = LinearStatus(
                    slug=slug,
                    linear_issue_id=issue_id,
                    linear_issue_identifier=cached.get("linear_issue_identifier")
                    or meta["linear_issue_identifier"],
                    linear_issue_url=cached.get("linear_issue_url")
                    or meta["linear_issue_url"],
                    state=cached.get("state", ""),
                    state_type=cached.get("state_type", ""),
                    assignee_name=cached.get("assignee_name", ""),
                    age_days=age_days,
                    tenant_id=meta["tenant_id"],
                    cached_at=time.strftime(
                        "%Y-%m-%dT%H:%M:%S%z", time.localtime(cached.get("ts", 0))
                    ),
                    fetched_at=cached.get("fetched_at_iso", ""),
                    error=cached.get("error"),
                )
                results.append(ls)
                continue

        # Cache miss or stale — call Linear.
        result = _call_linear_status(issue_id)
        fetched_at_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.gmtime(result["ts"]))

        # Update cache.
        cache_entry: Dict[str, Any] = {
            "ts": result["ts"],
            "fetched_at_iso": fetched_at_iso,
            "state": result["state"],
            "state_type": result["state_type"],
            "assignee_name": result["assignee_name"],
            "linear_issue_identifier": result["identifier"],
            "linear_issue_url": result["url"],
            "error": result["error"],
        }
        cache[issue_id] = cache_entry
        wrote_cache = True

        # Linear's `get_issue_status` doesn't return `createdAt`; we
        # approximate age from the submission log's `submitted_at` if
        # present, otherwise 0.0.
        try:
            submission = json.loads(log_path.read_text(encoding="utf-8"))
            age_days = _age_days_from_iso(submission.get("submitted_at", ""))
        except Exception:
            age_days = 0.0

        ls = LinearStatus(
            slug=slug,
            linear_issue_id=issue_id,
            linear_issue_identifier=result["identifier"]
            or meta["linear_issue_identifier"],
            linear_issue_url=result["url"] or meta["linear_issue_url"],
            state=result["state"],
            state_type=result["state_type"],
            assignee_name=result["assignee_name"],
            age_days=age_days,
            tenant_id=meta["tenant_id"],
            cached_at=fetched_at_iso,
            fetched_at=fetched_at_iso,
            error=result["error"],
        )
        results.append(ls)

    if wrote_cache:
        _write_cache(cache, cache_path)
    return results


def get_status_for_site(slug: str, **kwargs: Any) -> Optional[LinearStatus]:
    """Convenience: return the LinearStatus for one site, or None."""
    for ls in scan_status(**kwargs):
        if ls.slug == slug:
            return ls
    return None


# ── Renderer ─────────────────────────────────────────────────────────────
def render_status_html(status: LinearStatus) -> str:
    """Render a small status indicator for the per-site row.

    Returns an empty string when there's nothing to show (no error,
    no state). The output is wrapped in a `<span class="pwp-kpi-linear-status">`
    so the dashboard CSS can style it.
    """
    if not status.linear_issue_identifier:
        return ""
    if status.error:
        return (
            f'<span class="pwp-kpi-linear-status pwp-kpi-linear-status-error" '
            f'title="Linear API error: {status.error}">'
            f"Linear: {status.error}"
            f"</span>"
        )
    if not status.state:
        return ""
    parts = [
        f"Linear: <strong>{status.linear_issue_identifier}</strong>",
        f"· {status.state}",
    ]
    if status.assignee_name:
        parts.append(f"· {status.assignee_name}")
    if status.age_days > 0:
        # 2 dp, but trim trailing zeros.
        age = f"{status.age_days:.2f}".rstrip("0").rstrip(".")
        parts.append(f"· {age}d old")
    return (
        f'<span class="pwp-kpi-linear-status pwp-kpi-linear-status-{status.state_type or "unknown"}" '
        f'title="Linear {status.linear_issue_identifier} · {status.state}">'
        + " ".join(parts)
        + "</span>"
    )


__all__ = [
    "LinearStatus",
    "scan_status",
    "get_status_for_site",
    "render_status_html",
    "CACHE_PATH",
    "SUBMISSION_LOG_DIR",
    "DEFAULT_OK_TTL_SECONDS",
    "DEFAULT_ERROR_TTL_SECONDS",
]
