#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from seo_cron_common import AOT_ORIGIN, parse_html_file, resolve_site_dir, route_for_file, stamp, state_dir, write_json, write_text

URLS = [
    f"{AOT_ORIGIN}/",
]
THRESHOLDS = {"accessibility": 0.85, "seo": 0.90, "best-practices": 0.80}


def run_lighthouse(url: str, out_path: Path) -> dict:
    executable = shutil.which("lighthouse")
    if executable:
        command = [executable]
    elif shutil.which("npx"):
        command = ["npx", "--yes", "lighthouse"]
    else:
        raise FileNotFoundError("Neither lighthouse nor npx is available")
    command.extend([
        url,
        "--quiet",
        "--chrome-flags=--headless=new --no-sandbox",
        "--output=json",
        f"--output-path={out_path}",
        "--only-categories=accessibility,best-practices,seo",
    ])
    completed = subprocess.run(command, text=True, capture_output=True, timeout=240, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:] or completed.stdout[-1200:] or f"lighthouse exited {completed.returncode}")
    data = json.loads(out_path.read_text(encoding="utf-8"))
    categories = data.get("categories", {})
    return {
        "url": url,
        "scores": {key: categories.get(key, {}).get("score") for key in THRESHOLDS},
        "report": str(out_path),
    }


def static_fallback() -> dict:
    site_dir = resolve_site_dir()
    pages = []
    html_files = sorted([p for p in site_dir.glob("**/*.html") if p.is_file()])
    for path in html_files:
        route = route_for_file(path, site_dir)
        parsed = parse_html_file(path)
        pages.append({
            "route": route,
            "title": parsed.title,
            "has_h1": bool(parsed.h1),
            "has_meta_description": bool(parsed.meta_description),
            "jsonld_count": len(parsed.jsonld_blocks),
        })
    issues = []
    for page in pages:
        if not page["has_h1"]:
            issues.append({"route": page["route"], "issue": "missing_h1"})
        if not page["has_meta_description"]:
            issues.append({"route": page["route"], "issue": "missing_meta_description"})
        if page["jsonld_count"] == 0:
            issues.append({"route": page["route"], "issue": "no_jsonld"})
    return {"mode": "static_fallback", "site_dir": str(site_dir), "pages": pages, "issues": issues}


def main() -> int:
    outdir = state_dir("lighthouse-monitor")
    ts = stamp()
    reports_dir = outdir / ts
    reports_dir.mkdir(parents=True, exist_ok=True)
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "mode": "lighthouse", "results": [], "failures": []}
    try:
        for idx, url in enumerate(URLS, start=1):
            result["results"].append(run_lighthouse(url, reports_dir / f"lighthouse_{idx}.json"))
    except Exception as exc:
        result = {"timestamp": datetime.now(timezone.utc).isoformat(), "mode": "static_fallback", "lighthouse_error": str(exc), **static_fallback()}

    if result.get("mode") == "lighthouse":
        for item in result["results"]:
            for key, threshold in THRESHOLDS.items():
                score = item["scores"].get(key)
                if score is None or score < threshold:
                    result["failures"].append({"url": item["url"], "category": key, "score": score, "threshold": threshold})
    write_json(outdir / "latest_lighthouse_monitor.json", result)
    write_json(outdir / f"{ts}_lighthouse_monitor.json", result)
    md = ["# Lighthouse SEO/A11y Monitor", "", f"- Mode: {result.get('mode')}"]
    if result.get("mode") == "lighthouse":
        md.append(f"- Failures: {len(result['failures'])}")
        for item in result["results"]:
            md.append(f"- {item['url']}: " + ", ".join(f"{k}={v}" for k, v in item["scores"].items()))
    else:
        md.append(f"- Lighthouse unavailable/error: {result.get('lighthouse_error')}")
        md.append(f"- Static issues: {len(result.get('issues', []))}")
        for issue in result.get("issues", [])[:50]:
            md.append(f"- {issue['route']}: {issue['issue']}")
    write_text(outdir / "latest_lighthouse_monitor.md", "\n".join(md))
    failures = result.get("failures") or result.get("issues") or []
    print(f"Lighthouse monitor mode={result.get('mode')} failures_or_issues={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
