#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from seo_cron_common import (
    date_window,
    get_adc_token,
    stamp,
    state_dir,
    write_json,
    write_text,
)
from site_registry import ManagedSite, load_managed_sites

GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GA_BASE = "https://analyticsdata.googleapis.com/v1beta"

PAGE_DIMENSIONS = ["landingPagePlusQueryString"]
PAGE_METRICS = ["sessions", "activeUsers", "screenPageViews", "conversions", "totalRevenue", "purchaseRevenue", "ecommercePurchases"]
SITE_METRICS = ["sessions", "activeUsers", "screenPageViews", "conversions", "totalRevenue", "purchaseRevenue", "ecommercePurchases", "engagementRate", "averageSessionDuration"]
CHANNEL_DIMENSIONS = ["sessionDefaultChannelGroup"]


def ga_request(path: str, body: dict) -> dict:
    token, quota_project = get_adc_token([GA_SCOPE])
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if quota_project:
        headers["x-goog-user-project"] = quota_project
    req = Request(f"{GA_BASE}{path}", data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urlopen(req, timeout=60) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def run_report(property_id: str, start_date: str, end_date: str, dimensions: list[str], metrics: list[str], limit: int = 250) -> dict:
    body = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": d} for d in dimensions],
        "metrics": [{"name": m} for m in metrics],
        "limit": limit,
        "orderBys": [{"metric": {"metricName": metrics[0]}, "desc": True}] if metrics else [],
    }
    return ga_request(f"/properties/{property_id}:runReport", body)


def parse_report(report: dict, dimensions: list[str], metrics: list[str]) -> list[dict]:
    out = []
    for row in report.get("rows", []):
        item = {}
        for idx, dim in enumerate(dimensions):
            item[dim] = row.get("dimensionValues", [{}])[idx].get("value") if idx < len(row.get("dimensionValues", [])) else None
        for idx, metric in enumerate(metrics):
            raw = row.get("metricValues", [{}])[idx].get("value") if idx < len(row.get("metricValues", [])) else "0"
            try:
                value = float(raw) if "." in str(raw) else int(raw)
            except Exception:
                value = raw
            item[metric] = value
        sessions = float(item.get("sessions") or 0)
        conversions = float(item.get("conversions") or 0)
        revenue = float(item.get("totalRevenue") or 0)
        item["conversionRate"] = conversions / sessions if sessions else 0.0
        item["revenuePerSession"] = revenue / sessions if sessions else 0.0
        out.append(item)
    return out


def site_insights(site: ManagedSite, start: str, end: str) -> dict:
    prop = site.effective_ga4_property_id
    base = {"slug": site.slug, "name": site.name, "domain": site.domain, "ga4_property_id": prop, "success": False}
    if not prop:
        return {**base, "error": "GA4 property ID is not configured", "setup_action": f"Set {site.ga4_property_env or 'ga4_property_id'} for this site."}
    try:
        site_report = run_report(prop, start, end, [], SITE_METRICS, limit=1)
        page_report = run_report(prop, start, end, PAGE_DIMENSIONS, PAGE_METRICS, limit=250)
        channel_report = run_report(prop, start, end, CHANNEL_DIMENSIONS, ["sessions", "conversions", "totalRevenue"], limit=50)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        return {**base, "error": f"GA4 API HTTP {exc.code}: {detail[:1000]}"}
    except Exception as exc:
        return {**base, "error": str(exc)}
    totals = parse_report(site_report, [], SITE_METRICS)
    pages = parse_report(page_report, PAGE_DIMENSIONS, PAGE_METRICS)
    channels = parse_report(channel_report, CHANNEL_DIMENSIONS, ["sessions", "conversions", "totalRevenue"])
    high_value_pages = sorted(pages, key=lambda row: (float(row.get("totalRevenue") or 0), float(row.get("conversions") or 0), float(row.get("sessions") or 0)), reverse=True)
    leaking_pages = sorted(
        [row for row in pages if float(row.get("sessions") or 0) >= 10],
        key=lambda row: (float(row.get("sessions") or 0), -float(row.get("conversionRate") or 0)),
        reverse=True,
    )
    return {
        **base,
        "success": True,
        "start_date": start,
        "end_date": end,
        "site_totals": totals[0] if totals else {},
        "top_landing_pages": pages[:250],
        "high_value_pages": high_value_pages[:50],
        "conversion_leak_pages": leaking_pages[:50],
        "channels": channels,
    }


def render_markdown(report: dict) -> str:
    lines = ["# Managed SEO GA4 Insights", "", f"- Timestamp: {report['timestamp']}", f"- Window: {report['start_date']} → {report['end_date']}", ""]
    lines.extend([
        "## Why GA4 matters beyond GSC",
        "",
        "GSC tells us search visibility and Google organic clicks. GA4 tells us what visitors do after they land: conversions, revenue, booking-funnel behavior, channel mix, engagement, and page economics.",
        "",
    ])
    for site in report["sites"]:
        lines.extend([f"## {site['name']} (`{site['slug']}`)", ""])
        if not site.get("success"):
            lines.append(f"- GA4 unavailable: {site.get('error')}")
            if site.get("setup_action"):
                lines.append(f"- Setup action: {site['setup_action']}")
            lines.append("")
            continue
        totals = site.get("site_totals", {})
        lines.append(f"- Sessions: {totals.get('sessions', 0)}")
        lines.append(f"- Conversions: {totals.get('conversions', 0)}")
        lines.append(f"- Total revenue: {totals.get('totalRevenue', 0)}")
        lines.append(f"- Conversion rate: {float(totals.get('conversionRate') or 0):.2%}")
        lines.append("")
        lines.append("### Highest value landing pages")
        for page in site.get("high_value_pages", [])[:10]:
            lines.append(f"- `{page.get('landingPagePlusQueryString')}` — sessions {page.get('sessions', 0)}, conversions {page.get('conversions', 0)}, revenue {page.get('totalRevenue', 0)}, CVR {float(page.get('conversionRate') or 0):.2%}")
        lines.append("")
        lines.append("### High-traffic low-conversion pages")
        for page in site.get("conversion_leak_pages", [])[:10]:
            lines.append(f"- `{page.get('landingPagePlusQueryString')}` — sessions {page.get('sessions', 0)}, conversions {page.get('conversions', 0)}, revenue {page.get('totalRevenue', 0)}, CVR {float(page.get('conversionRate') or 0):.2%}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    start, end = date_window(days=30, lag_days=1)
    outdir = state_dir("ga4-insights")
    ts = stamp()
    sites = [site_insights(site, start, end) for site in load_managed_sites()]
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "start_date": start,
        "end_date": end,
        "sites": sites,
        "success": any(site.get("success") for site in sites),
    }
    write_json(outdir / "latest_ga4_insights.json", report)
    write_json(outdir / f"{ts}_ga4_insights.json", report)
    write_text(outdir / "latest_ga4_insights.md", render_markdown(report))
    ok = sum(1 for site in sites if site.get("success"))
    configured = sum(1 for site in sites if site.get("ga4_property_id"))
    hard_failures = [site for site in sites if site.get("ga4_property_id") and not site.get("success")]
    print(f"GA4 insights: {ok}/{len(sites)} sites succeeded; {configured}/{len(sites)} sites configured")
    return 2 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
