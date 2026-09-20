#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from seo_cron_common import AOT_GSC_PROPERTY, AOT_ORIGIN, gsc_sitemaps, stamp, state_dir, write_json, write_text


def fetch_sitemap_urls(sitemap_url: str) -> list[str]:
    with urlopen(sitemap_url, timeout=60) as response:
        raw = response.read()
    root = ET.fromstring(raw)
    urls = []
    for elem in root.iter():
        if elem.tag.endswith("loc") and elem.text:
            urls.append(elem.text.strip())
    return urls


def main() -> int:
    outdir = state_dir("sitemap-gsc")
    ts = stamp()
    sitemap_url = os.environ.get("PRISMATIC_SITEMAP_URL", f"{AOT_ORIGIN}/sitemap.xml")
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "property": AOT_GSC_PROPERTY,
        "sitemap_url": sitemap_url,
        "success": False,
    }
    try:
        local_urls = fetch_sitemap_urls(sitemap_url)
        gsc = gsc_sitemaps()
    except Exception as exc:
        report.update({"error": str(exc)})
        write_json(outdir / f"{ts}_sitemap_gsc_error.json", report)
        print(f"Sitemap/GSC verification failed: {exc}", file=sys.stderr)
        return 2

    sitemap_entries = gsc.get("sitemap", [])
    matching = [entry for entry in sitemap_entries if entry.get("path") == sitemap_url]
    target_netloc = urlparse(AOT_ORIGIN).netloc
    target_urls = [u for u in local_urls if urlparse(u).netloc == target_netloc or not urlparse(u).netloc]
    report.update({
        "success": True,
        "sitemap_url_count": len(local_urls),
        "target_url_count": len(target_urls),
        "gsc_sitemaps": sitemap_entries,
        "matching_gsc_sitemap": matching[0] if matching else None,
        "local_urls_sample": target_urls[:100],
    })
    write_json(outdir / "latest_sitemap_gsc.json", report)
    write_json(outdir / f"{ts}_sitemap_gsc.json", report)
    md = [
        "# Sitemap / GSC Verification",
        "",
        f"- Property: `{AOT_GSC_PROPERTY}`",
        f"- Sitemap: {sitemap_url}",
        f"- Sitemap URLs fetched: {len(local_urls)}",
        f"- Monitored Site URLs: {len(target_urls)}",
        f"- Found in GSC sitemap list: {'yes' if matching else 'no'}",
    ]
    if matching:
        entry = matching[0]
        md.extend([
            f"- Last submitted: {entry.get('lastSubmitted')}",
            f"- Last downloaded: {entry.get('lastDownloaded')}",
            f"- Errors: {entry.get('errors')}",
            f"- Warnings: {entry.get('warnings')}",
        ])
    write_text(outdir / "latest_sitemap_gsc.md", "\n".join(md))
    print(f"Sitemap/GSC verification: {len(local_urls)} sitemap URLs, GSC match={'yes' if matching else 'no'}")
    return 0 if matching else 1


if __name__ == "__main__":
    raise SystemExit(main())
