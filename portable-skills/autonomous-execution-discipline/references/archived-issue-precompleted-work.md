# Archived Issue with Pre-Completed Work — Nudge Executor

## The Pattern

The nudge executor finds a trigger file referencing an archived issue (GRO-151, identifier below GRO-500), but a prior session already completed the work. The trigger file was simply never cleaned up. This is distinct from the general "stale archived issue" pattern (where new work is needed) — here, **the work was done, artifacts exist, and the registry already reflects completion.**

## Detection Steps

1. **Step 0: Issue not found** — Query Linear API, issue doesn't exist (archived/bulk-deleted).
2. **Step 0.5 (Search A–F): Artifacts found** — Broad search reveals reference docs, implementation files, and registry updates already in place.
   - Search the skill's `references/` directory for docs matching the signal topic
   - Search `~/work/research/<topic>/` for extraction JSONs/notes
   - Use `search_files` with multiple keywords
   - Check `~/work/context-corpus*` for master-index entries
   - **Check the project-registry.json** — look for `_completed` entries or updated `next_action` mentioning the GRO number
3. **Cross-reference all three sources** — The registry says "done," artifacts exist on disk, and the trigger file has `retries_done=0`. The prior session completed the work but skipped cleaning up the trigger file.

## How to Handle

1. **Do NOT re-execute** — the work is done. Step 0.5 Case 2 applies.
2. **Verify artifact completeness** — Quick check: do the key files exist? Are they non-trivial? (GRO-151 had 21 files across 3 directories, including a 985-line firmware guide and 13KB webhook handler.)
3. **Delete nudge files** — `rm -f /tmp/trigger-fred-work /tmp/prismatic/nudge-*`
4. **Report the resolution** — State clearly: "GRO-XXX work was already completed by a prior session. Trigger file cleaned up."

## Concrete Example — GRO-151 (Jun 2026)

**Trigger file:**
```
0         ← retries_done
3         ← max_retries
GRO-151    ← issue_id
Implement Smart Lock IoT Bridge MVP  ← title
97b3b109-...                          ← signal_id
```

**Situation:** GRO-151 (archived, below the queryable range). The work — Smart Lock IoT Bridge MVP with FastAPI webhook handler, MQTT client, docker-compose stack, SQLite persistence, and 985-line firmware flashing guide — was fully built by a prior session on Jun 7, 2026. The registry already had a `_completed` entry (line ~914) and `next_action` pointed to "hardware provisioning" (human-gated). But `/tmp/trigger-fred-work` was never cleaned up.

**Artifacts verified on disk:**
| Location | Contents |
|----------|----------|
| `agentic-swarm-ops/docs/smart-lock-bridge-mvp/` | README, docker-compose, Makefile, FIRMWARE-GUIDE (41KB), webhook simulator, MQTT bridge, Mosquitto config |
| `autonomous-execution-discipline/references/smart-lock-bridge-mvp/` | webhook_handler.py (13KB), mqtt_client.py, config.py, models.py, database.py, dual docker-compose files, FLASHING_GUIDE.md (38KB), IMPLEMENTATION_PLAN.md |
| `agentic-swarm-ops/docs/sovereign-sentinel/smart-lock-bridge/` | IMPLEMENTATION_PLAN.md (8.6KB), FLASHING_GUIDE.md (38KB), all source files |

**Resolution:** Confirmed all artifacts exist → deleted trigger file → reported cleanup.

## Why This Happens

Prior sessions that complete work for archived issues may:
- Finish the implementation but forget file cleanup
- Not realize the trigger file was created by a dispatcher that's still polling
- Create artifacts in both places (project repo + skill refs) but not delete `/tmp/trigger-fred-work`

The trigger file is a filesystem semaphore — it doesn't auto-expire. Without Step 0.5 pre-verification, the nudge executor re-does completed work every 5 minutes.

## Pitfalls

- ❌ **Re-executing completed work:** Always pre-verify before building. The registry entry saying "done" is sufficient evidence — trust it.
- ❌ **Not checking the registry first:** The registry's `_completed` entries and `next_action` updates are the fastest signal. A registry entry mentioning the GRO number with a completion note means the work is done — no need for extensive artifact pre-verification.
- ❌ **Creating a new Linear issue for the completed work:** The archive was intentional. The work is documented in reference files. No new issue needed.
- ❌ **Leaving the trigger file for the next tick:** Always `rm -f /tmp/trigger-fred-work` after processing, even if the work was already done. A 5-minute cron tick will re-fire with the same stale signal.
