#!/usr/bin/env python3
"""
clear_stale_escalations.py — one-shot cleanup for the 6 stale escalations
from the 2026-07-01 incident.

When the curator's tag rules were updated (the fix that recognized
'source=linear' correctly), the older events that had been tagged with
the old "unmatched source='linear' topic='unknown'" reason stayed in the
DB as escalations. This script marks them as auto-pick (their correct
tag now) so they don't show up as false-positive escalations in the
digest.

This is a one-shot script. After running, the curator's tag rules are
the source of truth going forward — no more stale entries.
"""
import sys
import os
import json
import sqlite3
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/.prismatic/venv_stable/lib/python3.12/site-packages")
sys.path.insert(0, "/home/ubuntu/work/prismatic-engine")

from prismatic.curator.lane import tag_event, BusEvent

CURATOR_DB = "/home/ubuntu/.prismatic/curator/state.sqlite"
BUS_DB = "/home/ubuntu/.prismatic/bus/event_log.sqlite"

conn_bus = sqlite3.connect(BUS_DB)
cur_bus = conn_bus.execute("SELECT rowid, topic, payload_json FROM events")
events = {row[0]: (row[1], json.loads(row[2])) for row in cur_bus}
conn_bus.close()

conn = sqlite3.connect(CURATOR_DB)
cur = conn.execute("""
    SELECT rowid, event_rowid, tag, lane_hint, reason FROM tagged_events
    WHERE tag = 'escalate' AND reason LIKE 'unmatched source=%'
""")

fixed = 0
for row in cur:
    tevid, evrowid, old_tag, lane, reason = row
    if evrowid not in events:
        print(f"  ev={evrowid}: not found in bus, skipping")
        continue
    topic, payload = events[evrowid]
    ev = BusEvent(rowid=evrowid, topic=topic, payload=payload, ts=0.0, source=payload.get("source"))
    new_result = tag_event(ev)
    if new_result.tag != "escalate":
        conn.execute(
            "UPDATE tagged_events SET tag = ?, lane_hint = ?, reason = ? WHERE rowid = ?",
            (new_result.tag, new_result.lane_hint, new_result.reason, tevid),
        )
        print(f"  tev={tevid} ev={evrowid}: {old_tag} → {new_result.tag} ({new_result.reason[:50]})")
        fixed += 1
    else:
        print(f"  tev={tevid} ev={evrowid}: still {new_result.tag} ({new_result.reason[:50]})")

conn.commit()
conn.close()
print(f"\nFixed {fixed} stale escalations.")
