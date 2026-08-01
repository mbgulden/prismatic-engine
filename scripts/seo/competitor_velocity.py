#!/usr/bin/env python3
"""
Active Oahu Tours — Competitor Content Velocity Monitor
Runs weekly. Checks competitor top pages for new content.
Flags new pages entering our territory, traffic surges, and ranking shifts.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
from datetime import datetime

OUTDIR = str(Path(os.environ.get("PRISMATIC_STATE_DIR", REPO_ROOT / "prismatic_state")).expanduser() / "seo")
BASELINE_FILE = f"{OUTDIR}/competitor_baseline.json"
os.makedirs(OUTDIR, exist_ok=True)

TOKEN_FILE = "/tmp/ubs_token"
if not os.path.exists(TOKEN_FILE):
    print("ERROR: No Ubersuggest token found")
    sys.exit(1)

TOKEN = open(TOKEN_FILE).read().strip()
URL = "https://ubersuggest-mcp.neilpatelapi.com/mcp"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

TARGET_TERRITORIES = [
    "chinaman", "mokolii", "kualoa", "kaneohe", "sandbar",
    "sharks cove", "pupukea", "electric beach", "north shore",
    "kahana", "windward", "lanikai"
]

async def call_mcp(tool, args):
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(URL, headers=HEADERS) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                text = result.content[0].text if result.content else "{}"
                return json.loads(text)
    except Exception as e:
        return {"error": str(e)}

def extract_pages(data):
    """Extract page list from domain_top_pages response."""
    if isinstance(data, dict) and "topPages" in data:
        return data["topPages"]
    return []

async def main():
    TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"=== COMPETITOR VELOCITY CHECK: {datetime.now().isoformat()} ===\n")

    # Load baseline
    baseline = {}
    if os.path.exists(BASELINE_FILE):
        baseline = json.load(open(BASELINE_FILE))
        print(f"Loaded baseline from {BASELINE_FILE}")
    else:
        print("No baseline found — creating initial baseline")
    
    # Fetch current competitor top pages
    domains = {
        "kailuabeachadventures.com": "KBA",
        "surfnsea.com": "SurfNS",
    }
    
    alerts = []
    
    for domain, label in domains.items():
        print(f"\n--- {label} ({domain}) ---")
        data = await call_mcp("domain_top_pages", {"domain": domain, "limit": 20})
        current_pages = extract_pages(data)
        
        if not current_pages:
            print(f"  No data for {domain}")
            continue
        
        print(f"  Top pages fetched: {len(current_pages)}")
        
        # Build URL set for comparison
        current_urls = {p.get("url", ""): p for p in current_pages}
        
        if domain in baseline:
            prev_urls = set(baseline[domain].keys())
            curr_urls = set(current_urls.keys())
            
            # New pages
            new_urls = curr_urls - prev_urls
            for url in sorted(new_urls):
                p = current_urls[url]
                title = p.get("title", "Untitled")
                traffic = p.get("traffic", 0)
                # Check if in our territory
                territory_hit = [t for t in TARGET_TERRITORIES if t in title.lower() or t in url.lower()]
                marker = " 🚨 TERRITORY" if territory_hit else ""
                print(f"  NEW: {title[:60]} ({traffic} traffic){marker}")
                alerts.append(f"  {label}: NEW — {title[:60]} ({traffic} tr){marker}")
            
            # Removed pages
            removed_urls = prev_urls - curr_urls
            for url in sorted(removed_urls):
                old = baseline[domain][url]
                print(f"  DROPPED: {old.get('title', 'Untitled')[:60]}")
                alerts.append(f"  {label}: DROPPED — {old.get('title', 'Untitled')[:60]}")
            
            # Traffic surges (300%+ increase or 50%+ drop)
            for url in curr_urls & prev_urls:
                curr = current_urls[url]
                prev = baseline[domain][url]
                curr_t = curr.get("traffic", 0) or 0
                prev_t = prev.get("traffic", 0) or 0
                
                if prev_t > 10 and curr_t > prev_t * 3:
                    print(f"  SURGE: {curr.get('title', '')[:50]} — {prev_t} → {curr_t}")
                    alerts.append(f"  {label}: SURGE — {curr.get('title', '')[:50]} ({prev_t}→{curr_t})")
                elif prev_t > 10 and curr_t < prev_t * 0.5:
                    print(f"  DROP: {curr.get('title', '')[:50]} — {prev_t} → {curr_t}")
                    alerts.append(f"  {label}: DROP — {curr.get('title', '')[:50]} ({prev_t}→{curr_t})")
        else:
            print(f"  First baseline for {domain} — {len(current_pages)} pages saved")
        
        # Save to baseline
        baseline[domain] = {p.get("url", ""): {
            "title": p.get("title", ""),
            "traffic": p.get("traffic", 0),
            "keyword": p.get("keyword", "")
        } for p in current_pages}
    
    # Save updated baseline
    baseline["_meta"] = {"last_updated": TS, "version": 2}
    with open(BASELINE_FILE, "w") as f:
        json.dump(baseline, f, indent=2)
    print(f"\n✓ Baseline saved to {BASELINE_FILE}")
    
    # Save this run
    run_file = f"{OUTDIR}/{TS}_competitor_velocity.json"
    with open(run_file, "w") as f:
        json.dump({"timestamp": TS, "alerts": alerts, "baseline": baseline}, f, indent=2)
    print(f"✓ Run saved to {run_file}")
    
    # Final summary
    if alerts:
        print(f"\n=== {len(alerts)} ALERTS ===")
        for a in alerts:
            print(a)
    else:
        print("\n=== No changes detected ===")
    
    print("\n=== RUN COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(main())
