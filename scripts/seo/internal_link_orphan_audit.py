#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from seo_cron_common import (
    list_html_files,
    normalize_internal_href,
    parse_html_file,
    resolve_site_dir,
    route_for_file,
    stamp,
    state_dir,
    write_json,
    write_text,
)


def main() -> int:
    site_dir = resolve_site_dir()
    outdir = state_dir("internal-links")
    ts = stamp()
    pages = {}
    inbound: dict[str, set[str]] = defaultdict(set)
    existing_routes = set()

    files = list_html_files(site_dir)
    for html in files:
        route = route_for_file(html, site_dir)
        existing_routes.add(route)
        parsed = parse_html_file(html)
        links = []
        for href in parsed.hrefs:
            normalized = normalize_internal_href(href, route)
            if normalized:
                links.append(normalized)
        pages[route] = {
            "route": route,
            "file": str(html),
            "title": parsed.title,
            "h1": parsed.h1,
            "has_h1": bool(parsed.h1),
            "has_meta_description": bool(parsed.meta_description),
            "meta_description": parsed.meta_description,
            "jsonld_count": len(parsed.jsonld_blocks),
            "file_size_bytes": html.stat().st_size,
            "outbound_internal_links": sorted(set(links)),
        }

    broken_internal_links = []
    for source, page in pages.items():
        for target in page["outbound_internal_links"]:
            if target in existing_routes:
                inbound[target].add(source)
            else:
                broken_internal_links.append({"source": source, "target": target})

    for route, page in pages.items():
        page["inbound_internal_links"] = sorted(inbound.get(route, set()))
        page["inbound_count"] = len(page["inbound_internal_links"])

    orphans = sorted(route for route, page in pages.items() if route != "/" and page["inbound_count"] == 0)
    missing_h1 = sorted(route for route, page in pages.items() if not page["has_h1"])
    missing_meta = sorted(route for route, page in pages.items() if not page["has_meta_description"])
    no_schema = sorted(route for route, page in pages.items() if page["jsonld_count"] == 0)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "site_dir": str(site_dir),
        "total_pages": len(pages),
        "orphans": orphans,
        "missing_h1": missing_h1,
        "missing_meta_description": missing_meta,
        "no_schema": no_schema,
        "broken_internal_links": broken_internal_links,
        "pages": pages,
    }
    write_json(outdir / "latest_internal_link_graph.json", report)
    write_json(outdir / f"{ts}_internal_link_graph.json", report)
    md = [
        "# AOT Internal Link Graph & Orphan Audit",
        "",
        f"- Site dir: `{site_dir}`",
        f"- Total pages: {len(pages)}",
        f"- Orphans: {len(orphans)}",
        f"- Missing H1: {len(missing_h1)}",
        f"- Missing meta description: {len(missing_meta)}",
        f"- No JSON-LD schema: {len(no_schema)}",
        f"- Broken internal links: {len(broken_internal_links)}",
        "",
        "## Orphan Pages",
    ]
    md.extend(f"- {route} — {pages[route]['title'] or pages[route]['h1']}" for route in orphans[:100])
    md.extend(["", "## Broken Internal Links"])
    md.extend(f"- {item['source']} → {item['target']}" for item in broken_internal_links[:100])
    write_text(outdir / "latest_internal_link_graph.md", "\n".join(md))
    print(f"Internal link audit: {len(pages)} pages, {len(orphans)} orphans, {len(broken_internal_links)} broken internal links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
