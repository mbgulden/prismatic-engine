#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from seo_cron_common import stamp, state_dir, write_json, write_text

TERRITORY_TERMS = [
    "lanikai", "kailua", "mokulua", "mokolii", "mokoli", "chinaman", "sandbar",
    "kaneohe", "kāne", "sharks cove", "north shore", "snorkel", "kayak", "paddle",
    "beach chair", "umbrella", "beach gear", "rental", "tour",
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_first_json(paths: list[Path]) -> dict:
    for path in paths:
        data = load_json(path)
        if data:
            return data
    return {}


def normalize_terms(text: str) -> list[str]:
    low = text.lower()
    return [term for term in TERRITORY_TERMS if term in low]


def score_gsc_rows(gsc: dict) -> list[dict]:
    rows = gsc.get("rows", [])
    scored = []
    for row in rows:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        query, page = keys[0], keys[1]
        terms = normalize_terms(query + " " + page)
        if not terms:
            continue
        impressions = int(row.get("impressions", 0))
        clicks = int(row.get("clicks", 0))
        position = float(row.get("position", 99.0))
        ctr = clicks / impressions if impressions else 0.0
        opportunity = impressions * max(position - 3, 1) * (1.0 - min(ctr, 0.5))
        scored.append({
            "query": query,
            "page": page,
            "clicks": clicks,
            "impressions": impressions,
            "ctr": ctr,
            "position": position,
            "terms": terms,
            "opportunity_score": opportunity,
        })
    return sorted(scored, key=lambda item: item["opportunity_score"], reverse=True)


def competitor_territory_pages(velocity: dict) -> list[dict]:
    baseline = velocity.get("baseline", {})
    pages = []
    for domain, mapping in baseline.items():
        if domain.startswith("_") or not isinstance(mapping, dict):
            continue
        for url, page in mapping.items():
            text = f"{url} {page.get('title','')} {page.get('keyword','')}"
            terms = normalize_terms(text)
            if not terms:
                continue
            pages.append({
                "domain": domain,
                "url": url,
                "title": page.get("title", ""),
                "traffic": page.get("traffic", 0),
                "keyword": page.get("keyword", ""),
                "terms": terms,
            })
    return sorted(pages, key=lambda item: int(item.get("traffic") or 0), reverse=True)


def main() -> int:
    outdir = state_dir("counter-content")
    ts = stamp()
    gsc = load_json(state_dir("gsc-export") / "latest_gsc_query_page.json")
    velocity = load_first_json([
        state_dir() / "competitor_baseline.json",
        state_dir("competitor-velocity") / "competitor_baseline.json",
    ])
    if not gsc:
        error = {"timestamp": datetime.now(timezone.utc).isoformat(), "success": False, "error": "Missing latest GSC export. Run gsc_query_page_export.py first."}
        write_json(outdir / f"{ts}_error.json", error)
        print(error["error"])
        return 2
    if not velocity:
        error = {"timestamp": datetime.now(timezone.utc).isoformat(), "success": False, "error": "Missing competitor velocity baseline. Run competitor_velocity.py first."}
        write_json(outdir / f"{ts}_error.json", error)
        print(error["error"])
        return 2

    gsc_rows = score_gsc_rows(gsc)
    competitor_pages = competitor_territory_pages({"baseline": velocity})
    by_term: dict[str, dict] = defaultdict(lambda: {"gsc": [], "competitors": []})
    for row in gsc_rows:
        for term in row["terms"]:
            if len(by_term[term]["gsc"]) < 8:
                by_term[term]["gsc"].append(row)
    for page in competitor_pages:
        for term in page["terms"]:
            if len(by_term[term]["competitors"]) < 8:
                by_term[term]["competitors"].append(page)

    briefs = []
    for term, evidence in sorted(by_term.items(), key=lambda kv: len(kv[1]["gsc"]) + len(kv[1]["competitors"]), reverse=True):
        if not evidence["gsc"] or not evidence["competitors"]:
            continue
        top_gsc = evidence["gsc"][:5]
        target_pages = Counter(row["page"] for row in top_gsc).most_common(3)
        briefs.append({
            "topic": term,
            "target_pages": [page for page, _ in target_pages],
            "gsc_evidence": top_gsc,
            "competitor_evidence": evidence["competitors"][:5],
            "recommendation": f"Refresh the strongest matching AOT page for '{term}' with a quick-answer block, FAQ/schema support, and contextual links from related rental/tour pages.",
            "acceptance_criteria": [
                "Target page keeps existing booking/FareHarbor links intact.",
                "Visible copy answers the high-impression GSC queries directly.",
                "Competitor angle is countered with AOT-specific local/logistics guidance.",
                "FAQPage/HowTo/Product schema is added only where visible content supports it.",
            ],
        })

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": True,
        "gsc_window": {"start_date": gsc.get("start_date"), "end_date": gsc.get("end_date")},
        "brief_count": len(briefs),
        "briefs": briefs[:25],
    }
    write_json(outdir / "latest_counter_content_briefs.json", report)
    write_json(outdir / f"{ts}_counter_content_briefs.json", report)
    md = ["# AOT GSC + Ubersuggest Counter-Content Briefs", "", f"- Briefs generated: {len(briefs)}", ""]
    for brief in briefs[:12]:
        md.extend([
            f"## {brief['topic'].title()}",
            "",
            "**Target pages**",
            *[f"- {page}" for page in brief["target_pages"]],
            "",
            "**GSC evidence**",
            *[f"- {row['query']} → {row['page']} ({row['clicks']} clicks, {row['impressions']} impressions, pos {row['position']:.1f})" for row in brief["gsc_evidence"][:5]],
            "",
            "**Competitor evidence**",
            *[f"- {page['domain']}: {page['title']} — {page['url']} ({page.get('traffic',0)} traffic)" for page in brief["competitor_evidence"][:5]],
            "",
            f"**Recommendation:** {brief['recommendation']}",
            "",
        ])
    write_text(outdir / "latest_counter_content_briefs.md", "\n".join(md))
    print(f"Counter-content briefs generated: {len(briefs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
