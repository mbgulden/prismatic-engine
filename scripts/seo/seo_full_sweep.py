#!/usr/bin/env python3
"""
Managed Site — Full Competitive SEO Sweep
Runs autonomously. Saves structured reports to cron/output/seo-audit/
Each phase opens/closes its own MCP session (max 3-4 calls per session).
"""
import asyncio, json, os, sys, time
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
from datetime import datetime

OUTDIR = str(Path(os.environ.get("PRISMATIC_STATE_DIR", REPO_ROOT / "prismatic_state")).expanduser() / "seo")
os.makedirs(OUTDIR, exist_ok=True)

TS = datetime.now().strftime("%Y%m%d_%H%M%S")

# === TARGETS ===
MY_SITE = os.environ.get("PRISMATIC_PRIMARY_SITE", "engine.local")

# Direct competitors
_env_direct = os.environ.get("PRISMATIC_COMPETITORS", "")
DIRECT = (
    [d.strip() for d in _env_direct.split(",") if d.strip()]
    if _env_direct
    else ["competitor1.com", "competitor2.com"]
)

# Core seed keywords for content expansion
_env_seeds = os.environ.get("PRISMATIC_SEED_KEYWORDS", "")
SEED_KEYWORDS = (
    [s.strip() for s in _env_seeds.split(",") if s.strip()]
    if _env_seeds
    else [
        "agent hypervisor",
        "swarm concurrency",
        "autonomous multi-agent",
        "worktree process isolation",
        "deterministic verification",
    ]
)

# === HELPERS ===
TOKEN = open('/tmp/ubs_token').read().strip()
MCP_URL = "https://ubersuggest-mcp.neilpatelapi.com/mcp"
MCP_HEADERS = {"Authorization": f"Bearer {TOKEN}"}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def save_json(name, data):
    path = f"{OUTDIR}/{TS}_{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log(f"  Saved: {name}.json ({len(json.dumps(data, default=str))} bytes)")
    return path

async def call_mcp(tool, args):
    """Single MCP call in its own session (to avoid timeout)"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    try:
        async with streamablehttp_client(MCP_URL, headers=MCP_HEADERS) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                text = result.content[0].text if result.content else "{}"
                return json.loads(text)
    except Exception as e:
        log(f"  ERROR in {tool}({args}): {e}")
        return {"error": str(e)}

async def batch_calls(calls):
    """Run multiple MCP calls sequentially in separate sessions"""
    results = {}
    for i, (tool, args, key) in enumerate(calls):
        log(f"  [{i+1}/{len(calls)}] {tool}({args.get('domain', args.get('keyword', ''))})")
        data = await call_mcp(tool, args)
        results[key] = data
    return results

# === PHASES ===

async def phase1_domain_overviews():
    """Phase 1: domain_overview for all competitors"""
    log("\n=== PHASE 1: Domain Overviews ===")
    calls = []
    for domain in [MY_SITE] + DIRECT:
        calls.append(("domain_overview", {"domain": domain}, domain))
    results = await batch_calls(calls)
    save_json("phase1_domain_overviews", results)
    
    # Quick summary
    print("\n--- Domain Overview Summary ---")
    print(f"{'Site':<35} {'Traffic':>10} {'DA':>4} {'Keywords':>8} {'Ref Domains':>12}")
    print("-"*75)
    for domain, data in results.items():
        if "error" in data:
            print(f"{domain:<35} ERROR")
        else:
            print(f"{domain:<35} {data.get('organic', 0):>10,} {data.get('domainAuthority', 0):>4} "
                  f"{data.get('paidKeywords', 0)+len(data.get('organicKeywords', [])):>8,} "
                  f"{data.get('refDomains', 0):>12,}")
    return results

async def phase2_competitor_keywords():
    """Phase 2: domain_keywords (top 50) for top competitors"""
    log("\n=== PHASE 2: Competitor Keywords (top 50) ===")
    top = [d for d in DIRECT if d in ["kailuabeachadventures.com", "surfnsea.com", "hawaiianwatersports.com", "hawaiibeachtime.com"]]
    calls = []
    for domain in [MY_SITE] + top:
        calls.append(("domain_keywords", {"domain": domain, "type": "organic", "limit": 50}, domain))
    results = await batch_calls(calls)
    save_json("phase2_domain_keywords", results)
    
    # Keyword gap analysis
    my_kws = set()
    if MY_SITE in results and "organicKeywords" in results[MY_SITE]:
        my_kws = {k.get("keyword", "").lower() for k in results[MY_SITE]["organicKeywords"]}
    
    print(f"\n--- Keywords NOT in {MY_SITE} ---")
    for domain, data in results.items():
        if domain == MY_SITE or "error" in data:
            continue
        if "organicKeywords" in data:
            their_kws = {k.get("keyword", "").lower() for k in data["organicKeywords"]}
            gap = their_kws - my_kws
            print(f"\n{domain} — {len(gap)} gap keywords:")
            for kw in sorted(gap)[:15]:
                print(f"  · {kw}")
    return results

async def phase3_top_pages():
    """Phase 3: domain_top_pages for top competitors"""
    log("\n=== PHASE 3: Top Pages ===")
    top = DIRECT[:5]
    calls = []
    for domain in [MY_SITE] + top:
        calls.append(("domain_top_pages", {"domain": domain, "limit": 20}, domain))
    results = await batch_calls(calls)
    save_json("phase3_top_pages", results)
    
    print("\n--- Top Pages Summary ---")
    for domain, data in results.items():
        if "error" in data:
            continue
        pages = data.get("pages", data.get("topPages", []))
        print(f"\n{domain} — {len(pages)} top pages:")
        for p in pages[:5]:
            title = p.get("title", p.get("pageTitle", "?"))
            traffic = p.get("traffic", p.get("estimatedTraffic", 0))
            print(f"  · {title[:60]} ({traffic:,} est)")
    return results

async def phase4_backlink_opportunity():
    """Phase 4: backlink opportunity — domains linking to competitors but not us"""
    log("\n=== PHASE 4: Backlink Opportunity ===")
    results = await call_mcp("backlink_opportunity", {
        "positive_targets": ["kailuabeachadventures.com", "surfnsea.com"],
        "negative_targets": [MY_SITE],
        "limit": 25
    })
    save_json("phase4_backlink_opportunity", results)
    
    domains = results.get("data", results.get("opportunities", []))
    if isinstance(domains, list) and len(domains) > 0:
        print(f"\n--- Backlink Opportunities ({len(domains)}) ---")
        for d in domains[:10]:
            if isinstance(d, dict):
                print(f"  · {d.get('domain', '?')} — {d.get('backlinks', '?')} links")
            else:
                print(f"  · {d}")
    return results

async def phase5_keyword_suggestions():
    """Phase 5: keyword suggestions for core seed terms"""
    log("\n=== PHASE 5: Keyword Suggestions ===")
    calls = []
    for kw in SEED_KEYWORDS:
        calls.append(("keyword_suggestions", {"keyword": kw, "limit": 20}, kw))
    results = await batch_calls(calls)
    save_json("phase5_keyword_suggestions", results)
    
    # Collect all unique suggestions
    all_suggestions = set()
    for kw, data in results.items():
        if "error" not in data:
            suggestions = data.get("suggestions", data.get("data", []))
            if isinstance(suggestions, list):
                for s in suggestions:
                    if isinstance(s, dict):
                        all_suggestions.add(s.get("keyword", ""))
                    elif isinstance(s, str):
                        all_suggestions.add(s)
    
    print(f"\n--- Total Unique Keyword Suggestions: {len(all_suggestions)} ---")
    for s in sorted(list(all_suggestions))[:30]:
        print(f"  · {s}")
    return results

async def phase6_serp_analysis():
    """Phase 6: SERP analysis for high-value keywords (where we're positions 2-4)"""
    log("\n=== PHASE 6: SERP Analysis ===")
    priority_kws = [
        "kailua beach kayak rental",       # position 3, 590 vol
        "kailua kayak rental",             # position 2, 390 vol
        "rent kayak kailua",               # position 2, 390 vol
        "kaneohe sandbar kayak rentals",   # position 4, 140 vol
        "stand up paddleboard rental",     # position 4, 2400 vol
        "kayak rental kailua beach",       # position 2, 590 vol
        "paddle board rental oahu",        # position 2, 110 vol
        "kaneohe bay kayak rentals",       # position 4, 70 vol
    ]
    calls = []
    for kw in priority_kws:
        calls.append(("serp_analysis", {"keyword": kw, "limit": 10}, kw))
    results = await batch_calls(calls)
    save_json("phase6_serp_analysis", results)
    
    print("\n--- SERP Analysis Summary ---")
    for kw, data in results.items():
        if "error" not in data:
            serp = data.get("serpResults", data.get("results", data.get("data", [])))
            if isinstance(serp, list) and len(serp) > 0:
                print(f"\n{kw}:")
                for r in serp[:5]:
                    if isinstance(r, dict):
                        title = r.get("title", r.get("pageTitle", "?"))[:50]
                        url = r.get("url", r.get("link", "?"))
                        print(f"  [{r.get('position', '?')}] {title}")
    return results

async def phase7_auto_competitors():
    """Phase 7: validate and check unknown competitors"""
    log("\n=== PHASE 7: Unknown Competitors Check ===")
    unknowns = [
        "hawaiianwatersports.com",
        "bluebaykayakrentals.com", 
        "windwardwatersports.com",
    ]
    calls = []
    for domain in unknowns:
        calls.append(("domain_overview", {"domain": domain}, domain))
    results = await batch_calls(calls)
    save_json("phase7_unknown_competitors", results)
    
    print("\n--- Additional Competitor Check ---")
    for domain, data in results.items():
        if "error" in data:
            print(f"{domain}: ERROR/Rate-limited")
        else:
            traffic = data.get("organic", 0)
            da = data.get("domainAuthority", 0)
            print(f"{domain}: {traffic:,} traffic, DA {da}")
    return results

async def main():
    log(f"=== MANAGED SITE — FULL SEO SWEEP ===")
    log(f"Started: {datetime.now().isoformat()}")
    log(f"Token: {TOKEN[:20]}...")
    
    phases = [
        ("Phase 1 — Domain Overviews", phase1_domain_overviews),
        ("Phase 2 — Competitor Keywords (gap analysis)", phase2_competitor_keywords),
        ("Phase 3 — Top Pages", phase3_top_pages),
        ("Phase 4 — Backlink Opportunity", phase4_backlink_opportunity),
        ("Phase 5 — Keyword Suggestions (content expansion)", phase5_keyword_suggestions),
        ("Phase 6 — SERP Analysis (priority keywords)", phase6_serp_analysis),
        ("Phase 7 — Unknown Competitors", phase7_auto_competitors),
    ]
    
    results_summary = {}
    for phase_name, phase_fn in phases:
        log(f"\n{'='*60}")
        log(f"Starting: {phase_name}")
        log(f"{'='*60}")
        try:
            data = await phase_fn()
            results_summary[phase_name] = "✓ Complete"
        except Exception as e:
            log(f"PHASE FAILED: {e}")
            results_summary[phase_name] = f"✗ FAILED: {e}"
    
    # Final summary
    log(f"\n{'='*60}")
    log(f"SWEEP COMPLETE")
    log(f"{'='*60}")
    for phase, status in results_summary.items():
        log(f"  {status} — {phase}")
    
    log(f"\nReports saved to: {OUTDIR}/")
    log(f"Timestamp: {TS}")
    
    # Write a final index file
    index = {
        "timestamp": TS,
        "sites_scanned": [MY_SITE] + DIRECT,
        "seed_keywords": SEED_KEYWORDS,
        "phases": results_summary,
        "files": os.listdir(OUTDIR)
    }
    save_json("_index", index)

if __name__ == "__main__":
    asyncio.run(main())
