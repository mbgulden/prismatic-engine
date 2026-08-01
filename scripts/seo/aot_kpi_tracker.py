#!/usr/bin/env python3
"""
Active Oahu Tours — Weekly KPI Tracking Script
Runs autonomously via cron. Tracks rankings changes, competitor landscape, and traffic trends.
Saves to reports directory and outputs a summary for delivery.
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

OUTDIR = str(Path(os.environ.get("PRISMATIC_STATE_DIR", REPO_ROOT / "prismatic_state")).expanduser() / "seo" / "kpi-tracking")
os.makedirs(OUTDIR, exist_ok=True)

TS = datetime.now().strftime("%Y%m%d_%H%M%S")
MY_SITE = "activeoahutours.com"

# Previous run data (for comparison)
PREVIOUS_FILE = f"{OUTDIR}/latest_keywords.json"

TOKEN_FILE = "/tmp/ubs_token"
if not os.path.exists(TOKEN_FILE):
    print("ERROR: No Ubersuggest token found at /tmp/ubs_token")
    sys.exit(1)

TOKEN = open(TOKEN_FILE).read().strip()

async def call_mcp(tool, args):
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client("https://ubersuggest-mcp.neilpatelapi.com/mcp", 
                                          headers={"Authorization": f"Bearer {TOKEN}"}) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                text = result.content[0].text if result.content else "{}"
                return json.loads(text)
    except BaseException as e:
        details = []
        if hasattr(e, "exceptions"):
            for sub in e.exceptions:
                response = getattr(sub, "response", None)
                if response is not None:
                    details.append(f"{type(sub).__name__}: HTTP {response.status_code} {response.reason_phrase}")
                else:
                    details.append(f"{type(sub).__name__}: {sub}")
        else:
            response = getattr(e, "response", None)
            if response is not None:
                details.append(f"{type(e).__name__}: HTTP {response.status_code} {response.reason_phrase}")
            else:
                details.append(f"{type(e).__name__}: {e}")
        return {"error": " | ".join(details) or str(e)}

async def main():
    print(f"=== KPI TRACKING RUN: {datetime.now().isoformat()} ===")
    failures = []
    
    # 1. Domain overview (traffic snapshot)
    print("\n--- Traffic Snapshot ---")
    for domain in [MY_SITE, "kailuabeachadventures.com", "surfnsea.com"]:
        data = await call_mcp("domain_overview", {"domain": domain})
        if isinstance(data, dict) and "error" in data:
            msg = f"domain_overview failed for {domain}: {data['error']}"
            failures.append(msg)
            print(f"ERROR: {msg}")
        elif isinstance(data, dict):
            print(f"{domain}: {data.get('organic', 0)} kw, DA {data.get('domainAuthority', 0)}, {data.get('backlinks', 0)} blinks")
        else:
            msg = f"domain_overview returned unexpected payload for {domain}: {type(data).__name__}"
            failures.append(msg)
            print(f"ERROR: {msg}")
    
    # 2. Our top keywords (for rankings tracking)
    print("\n--- Our Top Keywords ---")
    data = await call_mcp("domain_keywords", {"domain": MY_SITE, "type": "organic", "limit": 30})
    our_kws = {}
    if isinstance(data, dict) and "error" in data:
        failures.append(f"domain_keywords failed for {MY_SITE}: {data['error']}")
        print(f"ERROR: domain_keywords failed for {MY_SITE}: {data['error']}")
    elif isinstance(data, list):
        for k in data:
            kw = k.get("keyword", "").lower()
            our_kws[kw] = {"pos": k.get("position"), "vol": k.get("volume"), "traffic": k.get("traffic")}
    else:
        failures.append(f"domain_keywords returned unexpected payload for {MY_SITE}: {type(data).__name__}")
        print(f"ERROR: domain_keywords returned unexpected payload for {MY_SITE}: {type(data).__name__}")
    if not our_kws:
        failures.append(f"domain_keywords returned zero keywords for {MY_SITE}")
        print(f"ERROR: domain_keywords returned zero keywords for {MY_SITE}")
    
    # 3. KBA keywords (gap analysis)
    print("\n--- KBA Keywords (sample) ---")
    kba_data = await call_mcp("domain_keywords", {"domain": "kailuabeachadventures.com", "type": "organic", "limit": 30})
    kba_kws = {}
    if isinstance(kba_data, dict) and "error" in kba_data:
        failures.append(f"domain_keywords failed for kailuabeachadventures.com: {kba_data['error']}")
        print(f"ERROR: domain_keywords failed for kailuabeachadventures.com: {kba_data['error']}")
    elif isinstance(kba_data, list):
        for k in kba_data:
            kw = k.get("keyword", "").lower()
            kba_kws[kw] = {"pos": k.get("position"), "vol": k.get("volume")}
    else:
        failures.append(f"domain_keywords returned unexpected payload for kailuabeachadventures.com: {type(kba_data).__name__}")
        print(f"ERROR: domain_keywords returned unexpected payload for kailuabeachadventures.com: {type(kba_data).__name__}")
    if not kba_kws:
        failures.append("domain_keywords returned zero keywords for kailuabeachadventures.com")
        print("ERROR: domain_keywords returned zero keywords for kailuabeachadventures.com")

    if failures:
        print("\n=== KPI TRACKING FAILED ===")
        print("Ubersuggest/MCP returned no usable ranking data. Preserving previous snapshot; not overwriting latest_keywords.json.")
        for item in failures:
            print(f"- {item}")
        sys.exit(2)
    
    # 4. Detect changes from last run
    if os.path.exists(PREVIOUS_FILE):
        with open(PREVIOUS_FILE) as f:
            prev = json.load(f)
        prev_kws = prev.get("our_keywords", {})
        
        gained = []
        lost = []
        improved = []
        declined = []
        
        for kw, info in our_kws.items():
            if kw not in prev_kws:
                gained.append((kw, info["pos"], info["vol"]))
            elif prev_kws[kw].get("pos", 99) > info["pos"]:
                improved.append((kw, prev_kws[kw]["pos"], info["pos"], info["vol"]))
            elif prev_kws[kw].get("pos", 99) < info["pos"]:
                declined.append((kw, prev_kws[kw]["pos"], info["pos"], info["vol"]))
        
        for kw, info in prev_kws.items():
            if kw not in our_kws:
                lost.append((kw, info["pos"], info.get("vol", 0)))
        
        print("\n--- Rankings Changes ---")
        print(f"Gained: {len(gained)} new keywords")
        for kw, pos, vol in sorted(gained, key=lambda x: -x[2])[:5]:
            print(f"  + {kw} (pos {pos}, vol {vol})")
        print(f"Lost: {len(lost)} keywords dropped")
        for kw, pos, vol in sorted(lost, key=lambda x: -x[2])[:5]:
            print(f"  - {kw} (was pos {pos})")
        print(f"Improved: {len(improved)} keywords")
        for kw, old, new, vol in sorted(improved, key=lambda x: -x[3])[:5]:
            print(f"  ↑ {kw}: {old} → {new} (vol {vol})")
        print(f"Declined: {len(declined)} keywords")
        for kw, old, new, vol in sorted(declined, key=lambda x: -x[3])[:5]:
            print(f"  ↓ {kw}: {old} → {new} (vol {vol})")
    else:
        print("\n--- (No previous data for comparison — baseline established) ---")
    
    # Save current data for next comparison
    snapshot = {
        "timestamp": TS,
        "our_keywords": our_kws,
        "kba_keywords": kba_kws,
    }
    with open(PREVIOUS_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)
    
    # Also save a dated copy
    with open(f"{OUTDIR}/{TS}_snapshot.json", "w") as f:
        json.dump(snapshot, f, indent=2)
    
    print(f"\n✓ Snapshot saved to {OUTDIR}/")
    print("=== RUN COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(main())
