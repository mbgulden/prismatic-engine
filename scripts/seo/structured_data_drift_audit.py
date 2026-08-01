#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from seo_cron_common import (
    list_html_files,
    parse_html_file,
    resolve_site_dir,
    route_for_file,
    stamp,
    state_dir,
    write_json,
    write_text,
)

EXPECTED_TYPES = {
    "LocalBusiness",
    "Organization",
    "WebSite",
    "WebPage",
    "TouristTrip",
    "Product",
    "FAQPage",
    "BreadcrumbList",
    "Article",
    "HowTo",
}


def collect_types(obj):
    if isinstance(obj, dict):
        t = obj.get("@type")
        if isinstance(t, str):
            yield t
        elif isinstance(t, list):
            for item in t:
                if isinstance(item, str):
                    yield item
        for value in obj.values():
            yield from collect_types(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from collect_types(item)


def main() -> int:
    site_dir = resolve_site_dir()
    outdir = state_dir("schema-drift")
    ts = stamp()
    pages = []
    type_counts = Counter()
    parse_errors = []
    no_schema = []
    unknown_types = defaultdict(list)

    for html in list_html_files(site_dir):
        route = route_for_file(html, site_dir)
        parsed = parse_html_file(html)
        page_types = []
        for idx, block in enumerate(parsed.jsonld_blocks):
            if not block:
                continue
            try:
                data = json.loads(block)
            except Exception as exc:
                parse_errors.append({"route": route, "block": idx, "error": str(exc)})
                continue
            types = sorted(set(collect_types(data)))
            page_types.extend(types)
            for t in types:
                type_counts[t] += 1
                if t not in EXPECTED_TYPES and not t.startswith("http"):
                    unknown_types[t].append(route)
        if not parsed.jsonld_blocks:
            no_schema.append(route)
        pages.append({"route": route, "title": parsed.title, "jsonld_blocks": len(parsed.jsonld_blocks), "schema_types": sorted(set(page_types))})

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "site_dir": str(site_dir),
        "total_pages": len(pages),
        "pages_without_schema": no_schema,
        "jsonld_parse_errors": parse_errors,
        "schema_type_counts": dict(sorted(type_counts.items())),
        "unknown_schema_types": {k: v for k, v in sorted(unknown_types.items())},
        "pages": pages,
    }
    write_json(outdir / "latest_schema_drift.json", report)
    write_json(outdir / f"{ts}_schema_drift.json", report)
    md = [
        "# AOT Structured Data / Schema Drift Audit",
        "",
        f"- Site dir: `{site_dir}`",
        f"- Total pages: {len(pages)}",
        f"- Pages without JSON-LD: {len(no_schema)}",
        f"- JSON-LD parse errors: {len(parse_errors)}",
        "",
        "## Schema Type Counts",
    ]
    md.extend(f"- `{key}`: {value}" for key, value in sorted(type_counts.items()))
    md.extend(["", "## Parse Errors"])
    md.extend(f"- {item['route']} block {item['block']}: {item['error']}" for item in parse_errors[:100])
    md.extend(["", "## Pages Without Schema"])
    md.extend(f"- {route}" for route in no_schema[:100])
    write_text(outdir / "latest_schema_drift.md", "\n".join(md))
    print(f"Schema drift audit: {len(pages)} pages, {len(no_schema)} without schema, {len(parse_errors)} parse errors")
    return 1 if parse_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
