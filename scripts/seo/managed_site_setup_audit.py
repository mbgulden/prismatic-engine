#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from seo_cron_common import gsc_sites, stamp, state_dir, write_json, write_text
from site_registry import ManagedSite, load_managed_sites


def fetch_url_status(url: str) -> dict:
    try:
        with urlopen(url, timeout=30) as response:
            body = response.read(4096)
            return {"ok": 200 <= response.status < 400, "status": response.status, "sample_bytes": len(body)}
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": str(exc)}
    except URLError as exc:
        return {"ok": False, "status": None, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": None, "error": str(exc)}


def expected_gsc_properties(site: ManagedSite) -> list[str]:
    props = []
    if site.gsc_property:
        props.append(site.gsc_property)
    props.extend([
        site.origin.rstrip('/') + '/',
        site.origin.rstrip('/'),
        f"https://www.{site.domain.removeprefix('www.')}/",
    ])
    seen = set()
    out = []
    for prop in props:
        if prop not in seen:
            seen.add(prop)
            out.append(prop)
    return out


def audit_site(site: ManagedSite, gsc_entries: list[dict] | None, gsc_error: str | None) -> dict:
    gsc_site_urls = {entry.get("siteUrl") for entry in gsc_entries or []}
    expected = expected_gsc_properties(site)
    present = [prop for prop in expected if prop in gsc_site_urls]
    sitemap = fetch_url_status(site.effective_sitemap_url)
    site_dir = site.resolve_site_dir()
    ga4_property = site.effective_ga4_property_id
    setup_needed: list[dict] = []
    if gsc_error:
        setup_needed.append({
            "system": "Google Search Console API",
            "severity": "blocking",
            "action": "Enable/reauth ADC with Search Console readonly scope and a quota project that has searchconsole.googleapis.com enabled.",
            "details": gsc_error,
        })
    elif not present:
        setup_needed.append({
            "system": "Google Search Console",
            "severity": "blocking",
            "action": "Add and verify the property in Search Console before relying on GSC query/page exports.",
            "recommended_property": site.gsc_property or f"sc-domain:{site.domain.removeprefix('www.')}",
            "ui_url": "https://search.google.com/search-console/welcome",
            "notes": "Domain properties usually require DNS verification. URL-prefix properties can also be added, but SEO crons default to sc-domain when available.",
        })
    if not sitemap.get("ok"):
        setup_needed.append({
            "system": "Sitemap",
            "severity": "warning",
            "action": "Publish or fix sitemap.xml before submitting it in GSC.",
            "sitemap_url": site.effective_sitemap_url,
            "status": sitemap,
        })
    if not ga4_property:
        setup_needed.append({
            "system": "Google Analytics 4",
            "severity": "blocking_for_ga_insights",
            "action": "Create/select a GA4 property, add the web data stream to the site, then set ga4_property_id or the configured env var.",
            "env_var": site.ga4_property_env,
            "ui_url": "https://analytics.google.com/analytics/web/",
            "notes": "Revenue requires ecommerce/purchase or booking-complete events. For off-site booking providers, configure cross-domain tracking and/or server-side booking imports.",
        })
    return {
        "slug": site.slug,
        "name": site.name,
        "domain": site.domain,
        "origin": site.origin,
        "gsc_property": site.gsc_property,
        "gsc_present": bool(present),
        "gsc_present_properties": present,
        "gsc_expected_properties": expected,
        "sitemap_url": site.effective_sitemap_url,
        "sitemap": sitemap,
        "site_dir": str(site_dir) if site_dir else None,
        "ga4_property_id": ga4_property,
        "ga4_configured": bool(ga4_property),
        "booking_provider": site.booking_provider,
        "setup_needed": setup_needed,
    }


def render_markdown(report: dict) -> str:
    lines = ["# Managed SEO Site Setup Audit", "", f"- Timestamp: {report['timestamp']}", f"- Sites audited: {len(report['sites'])}", ""]
    for site in report["sites"]:
        lines.extend([f"## {site['name']} (`{site['slug']}`)", ""])
        lines.append(f"- Domain: `{site['domain']}`")
        lines.append(f"- GSC property: `{site.get('gsc_property')}` — {'present' if site['gsc_present'] else 'missing'}")
        lines.append(f"- GA4 property ID: `{site.get('ga4_property_id') or 'not configured'}`")
        lines.append(f"- Sitemap: {site['sitemap'].get('status')} at {site['sitemap'].get('status') and site.get('sitemap_url', '')}")
        if site["setup_needed"]:
            lines.append("- Setup needed:")
            for item in site["setup_needed"]:
                lines.append(f"  - **{item['system']}** ({item['severity']}): {item['action']}")
        else:
            lines.append("- Setup needed: none detected")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    outdir = state_dir("site-setup")
    ts = stamp()
    try:
        gsc = gsc_sites()
        gsc_entries = gsc.get("siteEntry", [])
        gsc_error = None
    except Exception as exc:
        gsc_entries = []
        gsc_error = str(exc)
    sites = [audit_site(site, gsc_entries, gsc_error) for site in load_managed_sites()]
    blocking_count = sum(1 for site in sites for item in site["setup_needed"] if item["severity"].startswith("blocking"))
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": blocking_count == 0,
        "gsc_api_error": gsc_error,
        "sites": sites,
    }
    write_json(outdir / "latest_site_setup_audit.json", report)
    write_json(outdir / f"{ts}_site_setup_audit.json", report)
    write_text(outdir / "latest_site_setup_audit.md", render_markdown(report))
    blockers = blocking_count
    print(f"Managed SEO setup audit: {len(sites)} sites, {blockers} blockers")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
