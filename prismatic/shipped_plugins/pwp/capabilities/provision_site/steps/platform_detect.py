"""step_platform_detect — identify the hosting platform for a domain.

For Phase 3, "platform" matters because:
  - CF Pages uses `cloudflare_zone` to wire analytics + has its own DNS
  - Vercel uses `vercel_project` + env vars for `NEXT_PUBLIC_*`
  - Cloudflare Tunnel means: the user owns a tunnel that fronts an
    external origin (home/office/VPS); the platform is "tunnel"
    and we leave the project/zone details to the tunnel config.
  - Other (Railway, custom, unknown) -> the platform-specific steps
    below no-op with a clear "platform not yet supported" message.

Detection uses Cloudflare's DNS-over-HTTPS endpoint to read the apex
CNAME (cheap, reliable, no Cloudflare account required). For apex
domains that don't have a CNAME (just an A record), we fall back to
HTTP header probing of the live site.

Signals (in priority order):

  1. CNAME -> <id>.cfargotunnel.com           -> `cf_tunnel`
  2. CNAME -> *.vercel-dns.com                -> `vercel`
  3. CNAME -> *.up.railway.app|*.up.railway.com -> `railway`
  4. CNAME -> *.pages.dev (CF Pages)          -> `cloudflare_pages`
  5. Cloudflare zone exists + status=active    -> `cloudflare_pages`
  6. response server header is `Vercel`        -> `vercel`
  7. response `x-vercel-id` header            -> `vercel`
  8. response `cf-ray` header                  -> `cloudflare_pages`
  9. nothing matched                          -> `unknown`

We do NOT mutate anything in this step. Detection is read-only so it's
always safe to run.

Output (on success):
  {
    "platform": "cloudflare_pages" | "vercel" | "cf_tunnel"
              | "railway" | "unknown",
    "evidence": "<human-readable reason>",
    "vercel_project_name": "<name>" | None,
    "cloudflare_zone_id": "<id>" | None,
    "apex_cname": "<target>" | None,
  }
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..types import StepResult

# CF DoH endpoint (free, no auth)
DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"

# CNAME target patterns that identify a hosting platform.
CF_TUNNEL_CNAME_SUFFIX = ".cfargotunnel.com"
VERCEL_CNAME_SUFFIXES = (
    ".vercel-dns.com",  # `cname.vercel-dns.com` for apex
)
CF_PAGES_CNAME_SUFFIXES = (
    ".pages.dev",
)
RAILWAY_CNAME_SUFFIXES = (
    ".up.railway.app",
    ".up.railway.com",
)


def _doh_cname(host: str, timeout: float = 5.0) -> str | None:
    """Return the apex CNAME for `host` via Cloudflare DoH, or None.

    Uses HTTPS GET to https://cloudflare-dns.com/dns-query?name=<host>&type=CNAME
    and parses the JSON response. If `host` has no CNAME (just A), returns None.
    """
    try:
        url = f"{DOH_ENDPOINT}?name={urllib.parse.quote(host)}&type=CNAME"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/dns-json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        answers = payload.get("Answer") or []
        for ans in answers:
            if ans.get("type") == 5:  # CNAME
                return ans.get("data", "").rstrip(".")
        return None
    except Exception:
        return None


def _http_probe(host: str, timeout: float = 5.0) -> dict[str, str]:
    """GET the apex and return response headers (no body)."""
    try:
        req = urllib.request.Request(
            f"https://{host}/",
            method="GET",
            headers={"User-Agent": "PWP-platform-detect/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        return {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
    except Exception:
        return {}


def _classify_cname(cname: str | None) -> str | None:
    """Return the platform name inferred from the CNAME target, or None."""
    if not cname:
        return None
    cname_lower = cname.lower()
    if cname_lower.endswith(CF_TUNNEL_CNAME_SUFFIX):
        return "cf_tunnel"
    for suffix in VERCEL_CNAME_SUFFIXES:
        if cname_lower.endswith(suffix):
            return "vercel"
    for suffix in CF_PAGES_CNAME_SUFFIXES:
        if cname_lower.endswith(suffix):
            return "cloudflare_pages"
    for suffix in RAILWAY_CNAME_SUFFIXES:
        if cname_lower.endswith(suffix):
            return "railway"
    return None


def _classify_headers(headers: dict[str, str]) -> str | None:
    """Return the platform inferred from HTTP response headers, or None."""
    if not headers:
        return None
    server = headers.get("server", "").lower()
    x_vercel_id = headers.get("x-vercel-id", "")
    x_railway = headers.get("x-railway-edge", "") or headers.get("x-railway-request-id", "")
    powered_by = headers.get("x-powered-by", "").lower()
    cf_ray = headers.get("cf-ray", "")

    if x_vercel_id or "vercel" in server:
        return "vercel"
    if x_railway or "railway" in server or "railway" in powered_by:
        return "railway"
    if cf_ray or "cloudflare" in server or "cloudflare" in powered_by:
        return "cloudflare_pages"
    return None


def step_platform_detect(
    *,
    domain: str,
    owner: str,
    run,
    publish_root,
    prior_outputs=None,
    **_: Any,
) -> StepResult:
    """Detect the hosting platform for the domain.

    Always succeeds (detection is read-only) but may return
    `platform: 'unknown'` for sites on platforms we don't support yet.
    """
    prior = prior_outputs or {}
    cf_zone_id = (prior.get("cloudflare_zone") or {}).get("zone_id")
    cf_zone_status = (prior.get("cloudflare_zone") or {}).get("status", "")

    # 1. If cloudflare_zone succeeded with status='active', that's our
    # strongest signal for cloudflare_pages — but only if we don't find
    # a stronger signal (CF Tunnel) in DNS first.
    apex_cname = _doh_cname(domain)
    cname_platform = _classify_cname(apex_cname)

    evidence_parts = []
    if apex_cname:
        evidence_parts.append(f"cname={apex_cname}")
    if cf_zone_id:
        evidence_parts.append(f"cf-zone={cf_zone_id}({cf_zone_status})")

    # Priority: explicit DNS CNAME > CF zone > HTTP probe > unknown.
    if cname_platform:
        # If cname is CF Tunnel and we ALSO have a CF zone, the tunnel
        # is bound to that zone — record both.
        return StepResult(
            name="platform_detect",
            status="complete",
            output={
                "platform": cname_platform,
                "evidence": "; ".join(evidence_parts),
                "cloudflare_zone_id": cf_zone_id,
                "vercel_project_name": (
                    domain.split(".")[0] if cname_platform == "vercel" else None
                ),
                "apex_cname": apex_cname,
            },
        )

    if cf_zone_status == "active":
        return StepResult(
            name="platform_detect",
            status="complete",
            output={
                "platform": "cloudflare_pages",
                "evidence": "; ".join(evidence_parts),
                "cloudflare_zone_id": cf_zone_id,
                "vercel_project_name": None,
                "apex_cname": apex_cname,
            },
        )

    # 2. HTTP probe fallback.
    headers = _http_probe(domain)
    header_platform = _classify_headers(headers)
    if header_platform:
        evidence_parts.append(f"http-header={sorted(headers.keys())[:5]}")
        return StepResult(
            name="platform_detect",
            status="complete",
            output={
                "platform": header_platform,
                "evidence": "; ".join(evidence_parts),
                "cloudflare_zone_id": cf_zone_id,
                "vercel_project_name": (
                    domain.split(".")[0] if header_platform == "vercel" else None
                ),
                "apex_cname": apex_cname,
            },
        )

    # 3. Nothing matched.
    return StepResult(
        name="platform_detect",
        status="complete",
        output={
            "platform": "unknown",
            "evidence": "; ".join(evidence_parts) + "; no platform signal",
            "cloudflare_zone_id": cf_zone_id,
            "vercel_project_name": None,
            "apex_cname": apex_cname,
        },
    )
