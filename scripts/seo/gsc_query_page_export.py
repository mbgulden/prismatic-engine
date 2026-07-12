#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

from seo_cron_common import date_window, gsc_search_analytics, gsc_sites, stamp, state_dir, write_json, write_text, AOT_GSC_PROPERTY

# Native cron contract: exports Search Console `searchAnalytics/query` rows for
# `sc-domain:activeoahutours.com`; URL encoding and HTTP calls live in
# seo_cron_common.gsc_search_analytics.


def summarize_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    by_query: dict[str, dict] = defaultdict(lambda: {"clicks": 0, "impressions": 0, "position_weight": 0.0})
    by_page: dict[str, dict] = defaultdict(lambda: {"clicks": 0, "impressions": 0, "position_weight": 0.0})
    for row in rows:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        query, page = keys[0], keys[1]
        clicks = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        position = float(row.get("position", 0.0))
        for bucket, key in ((by_query, query), (by_page, page)):
            bucket[key]["clicks"] += clicks
            bucket[key]["impressions"] += impressions
            bucket[key]["position_weight"] += position * max(impressions, 1)
    def finalize(bucket: dict[str, dict], key_name: str) -> list[dict]:
        out = []
        for key, stats in bucket.items():
            impressions = stats["impressions"]
            avg_position = stats["position_weight"] / max(impressions, 1)
            ctr = stats["clicks"] / impressions if impressions else 0.0
            out.append({key_name: key, "clicks": stats["clicks"], "impressions": impressions, "ctr": ctr, "position": avg_position})
        return sorted(out, key=lambda item: (-item["clicks"], -item["impressions"]))
    return finalize(by_query, "query"), finalize(by_page, "page")


def main() -> int:
    start, end = date_window(days=90, lag_days=3)
    outdir = state_dir("gsc-export")
    ts = stamp()
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "property": AOT_GSC_PROPERTY,
        "start_date": start,
        "end_date": end,
    }
    try:
        sites = gsc_sites()
        rows_payload = gsc_search_analytics(start, end, ["query", "page"], row_limit=25000)
    except Exception as exc:
        error = {**payload, "success": False, "error": str(exc)}
        write_json(outdir / f"{ts}_error.json", error)
        print(f"GSC export failed: {exc}", file=sys.stderr)
        return 2

    rows = rows_payload.get("rows", [])
    by_query, by_page = summarize_rows(rows)
    report = {
        **payload,
        "success": True,
        "row_count": len(rows),
        "sites": sites.get("siteEntry", []),
        "rows": rows,
        "top_queries": by_query[:250],
        "top_pages": by_page[:250],
    }
    write_json(outdir / "latest_gsc_query_page.json", report)
    write_json(outdir / f"{ts}_gsc_query_page.json", report)
    summary = [
        "# AOT GSC Query/Page Export",
        "",
        f"- Property: `{AOT_GSC_PROPERTY}`",
        f"- Window: {start} → {end}",
        f"- Rows: {len(rows)}",
        "",
        "## Top Queries",
    ]
    for item in by_query[:20]:
        summary.append(f"- {item['query']} — {item['clicks']} clicks, {item['impressions']} impressions, pos {item['position']:.1f}")
    summary.extend(["", "## Top Pages"])
    for item in by_page[:20]:
        summary.append(f"- {item['page']} — {item['clicks']} clicks, {item['impressions']} impressions, pos {item['position']:.1f}")
    write_text(outdir / "latest_gsc_query_page.md", "\n".join(summary))
    print(f"GSC export saved {len(rows)} rows to {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
