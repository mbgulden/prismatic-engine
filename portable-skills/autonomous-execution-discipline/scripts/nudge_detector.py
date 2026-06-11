#!/usr/bin/env python3
"""Nudge Detection — script-only cron (runs every 1 min).
Checks for trigger files and prints structured alerts ONLY when actionable.
Silent when nothing to process (empty stdout = no delivery)."""

import os, json, glob
from datetime import datetime, timezone

TRIGGER_FILE = "/tmp/trigger-fred-work"
PRISMATIC_DIR = "/tmp/prismatic"
CLEANED_TRACKER = os.path.expanduser("~/.hermes/profiles/orchestrator/skills/agent-orchestration/autonomous-execution-discipline/references/cleaned-signals-tracker.json")

def now():
    return datetime.now(timezone.utc).isoformat()

def check_legacy_trigger():
    """Check old /tmp/trigger-fred-work format."""
    if not os.path.exists(TRIGGER_FILE):
        return None
    try:
        with open(TRIGGER_FILE) as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if len(lines) < 3:
            return None
        retries = int(lines[0])
        max_retries = int(lines[1])
        issue_id = lines[2]
        title = lines[3] if len(lines) > 3 else "Unknown"
        signal_id = lines[4] if len(lines) > 4 else "none"
        return {
            "format": "legacy",
            "issue_id": issue_id,
            "title": title,
            "retries": retries,
            "max_retries": max_retries,
            "signal_id": signal_id,
            "path": TRIGGER_FILE
        }
    except Exception:
        return None

def check_prismatic_signals():
    """Check structured /tmp/prismatic/nudge-* signals."""
    if not os.path.isdir(PRISMATIC_DIR):
        return []
    signals = []
    for f in sorted(glob.glob(f"{PRISMATIC_DIR}/nudge-*")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            signals.append({
                "format": "prismatic",
                "target": data.get("target", "unknown"),
                "issue_id": data.get("issue_id", ""),
                "title": data.get("title", ""),
                "priority": data.get("priority", 3),
                "signal_id": data.get("signal_id", ""),
                "path": f
            })
        except Exception:
            pass
    return signals

def check_resurrection(signal_id, issue_id):
    """Check cleaned-signals-tracker for resurrection pattern."""
    if not os.path.exists(CLEANED_TRACKER):
        return 0
    try:
        with open(CLEANED_TRACKER) as f:
            tracker = json.load(f)
        cleanings = [e for e in tracker.get("cleanings", []) 
                     if e.get("signal_id") == signal_id or e.get("issue_id") == issue_id]
        return len(cleanings)
    except Exception:
        return 0

# Main
legacy = check_legacy_trigger()
prismatic = check_prismatic_signals()

alerts = []

if legacy:
    resurrections = check_resurrection(legacy["signal_id"], legacy["issue_id"])
    if resurrections >= 3:
        alerts.append(f"RESURRECTED x{resurrections}: {legacy['issue_id']} — {legacy['title']} (legacy trigger)")
    elif legacy["retries"] >= legacy["max_retries"]:
        alerts.append(f"MAX RETRIES: {legacy['issue_id']} — {legacy['title']}")
    else:
        alerts.append(f"NUDGE: {legacy['issue_id']} — {legacy['title']} (retry {legacy['retries']}/{legacy['max_retries']})")

for sig in prismatic:
    resurrections = check_resurrection(sig["signal_id"], sig["issue_id"])
    if resurrections >= 3:
        alerts.append(f"RESURRECTED x{resurrections}: {sig['issue_id']} — {sig['title']} (prismatic)")
    else:
        alerts.append(f"NUDGE[{sig['target']}]: {sig['issue_id']} — {sig['title']} (p{sig['priority']})")

if alerts:
    print(f"[{now()}] Nudge Detection:")
    for a in alerts:
        print(f"  {a}")
else:
    # Silent — nothing to report
    pass
