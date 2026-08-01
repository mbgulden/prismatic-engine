#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
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


def scan_static_tag_layer(site: ManagedSite, site_dir: Path | None) -> dict:
    expected_events = list(site.expected_data_layer_events)
    result = {
        "site_dir": str(site_dir) if site_dir else None,
        "html_files_scanned": 0,
        "gtm_container_id": site.effective_gtm_container_id,
        "ga4_measurement_id": site.effective_ga4_measurement_id,
        "gtm_configured": bool(site.effective_gtm_container_id),
        "ga4_measurement_configured": bool(site.effective_ga4_measurement_id),
        "gtm_script_present": False,
        "gtm_noscript_present": False,
        "ga4_measurement_present": False,
        "data_layer_present": False,
        "expected_data_layer_events": expected_events,
        "observed_data_layer_events": [],
        "missing_data_layer_events": expected_events,
        "sample_files": [],
    }
    if not site_dir:
        result["error"] = "No static site directory resolved; cannot inspect GTM/dataLayer installation."
        return result
    event_hits: set[str] = set()
    sample_files: set[str] = set()
    gtm_id = site.effective_gtm_container_id
    ga4_id = site.effective_ga4_measurement_id
    html_files = sorted(site_dir.rglob("*.html"))
    for path in html_files:
        rel = str(path.relative_to(site_dir))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        result["html_files_scanned"] += 1
        if gtm_id and gtm_id in text:
            result["gtm_script_present"] = True
            sample_files.add(rel)
        if gtm_id and "googletagmanager.com/ns.html" in text and gtm_id in text:
            result["gtm_noscript_present"] = True
            sample_files.add(rel)
        if ga4_id and ga4_id in text:
            result["ga4_measurement_present"] = True
            sample_files.add(rel)
        if "dataLayer" in text:
            result["data_layer_present"] = True
            sample_files.add(rel)
        for event in expected_events:
            if re.search(rf"['\"]{re.escape(event)}['\"]", text):
                event_hits.add(event)
                sample_files.add(rel)
    result["observed_data_layer_events"] = sorted(event_hits)
    result["missing_data_layer_events"] = [event for event in expected_events if event not in event_hits]
    result["sample_files"] = sorted(sample_files)[:25]
    return result


def audit_site(site: ManagedSite, gsc_entries: list[dict] | None, gsc_error: str | None) -> dict:
    gsc_site_urls = {entry.get("siteUrl") for entry in gsc_entries or []}
    expected = expected_gsc_properties(site)
    present = [prop for prop in expected if prop in gsc_site_urls]
    sitemap = fetch_url_status(site.effective_sitemap_url)
    site_dir = site.resolve_site_dir()
    tag_layer = scan_static_tag_layer(site, site_dir)
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
    if not site.effective_gtm_container_id:
        setup_needed.append({
            "system": "Google Tag Manager",
            "severity": "blocking_for_tag_management",
            "action": "Create/select a GTM container for this site, then set gtm_container_id or the configured env var.",
            "env_var": site.gtm_container_env,
            "ui_url": "https://tagmanager.google.com/",
            "notes": "Golden path: site installs GTM once, site pushes business events to dataLayer, GTM maps those events to GA4/Ads/etc.",
        })
    elif not tag_layer.get("gtm_script_present"):
        setup_needed.append({
            "system": "Google Tag Manager",
            "severity": "blocking_for_tag_management",
            "action": "Install the configured GTM container snippet on the site.",
            "gtm_container_id": site.effective_gtm_container_id,
        })
    if site.effective_gtm_container_id and not tag_layer.get("gtm_noscript_present"):
        setup_needed.append({
            "system": "Google Tag Manager noscript",
            "severity": "warning",
            "action": "Add the GTM <noscript> fallback immediately after the opening <body> tag.",
            "gtm_container_id": site.effective_gtm_container_id,
        })
    if not site.effective_ga4_measurement_id:
        setup_needed.append({
            "system": "GA4 web stream",
            "severity": "blocking_for_tag_management",
            "action": "Set ga4_measurement_id or the configured env var so GTM can map dataLayer events to the correct GA4 stream.",
            "env_var": site.ga4_measurement_env,
        })
    elif not tag_layer.get("data_layer_present"):
        setup_needed.append({
            "system": "dataLayer",
            "severity": "blocking_for_tag_management",
            "action": "Add dataLayer initialization and business-event pushes to the site so GTM can route events.",
            "expected_events": site.expected_data_layer_events,
        })
    missing_events = tag_layer.get("missing_data_layer_events") or []
    if site.expected_data_layer_events and missing_events:
        setup_needed.append({
            "system": "dataLayer events",
            "severity": "warning",
            "action": "Emit the expected portable business events so GTM can map them to GA4/Ads destinations.",
            "missing_events": missing_events,
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
        "gtm_container_id": site.effective_gtm_container_id,
        "gtm_container_configured": bool(site.effective_gtm_container_id),
        "ga4_measurement_id": site.effective_ga4_measurement_id,
        "ga4_measurement_configured": bool(site.effective_ga4_measurement_id),
        "tag_layer": tag_layer,
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
        lines.append(f"- GTM container ID: `{site.get('gtm_container_id') or 'not configured'}`")
        lines.append(f"- GA4 measurement ID: `{site.get('ga4_measurement_id') or 'not configured'}`")
        tag = site.get("tag_layer") or {}
        lines.append(f"- Tag layer: GTM script={'yes' if tag.get('gtm_script_present') else 'no'}, noscript={'yes' if tag.get('gtm_noscript_present') else 'no'}, dataLayer={'yes' if tag.get('data_layer_present') else 'no'}")
        if tag.get("expected_data_layer_events"):
            observed = ", ".join(tag.get("observed_data_layer_events") or []) or "none"
            missing = ", ".join(tag.get("missing_data_layer_events") or []) or "none"
            lines.append(f"- dataLayer events: observed `{observed}`; missing `{missing}`")
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
