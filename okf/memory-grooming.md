---
type: Standard
title: Hermes Memory Grooming + Capacity Check
description: Weekly prune + 12-hour capacity check for `~/.hermes/profiles/*/memories/MEMORY.md` (cap 2200 chars) and `USER.md` (cap 1375 chars). Prevents memory from growing unbounded and eating the per-turn context budget.
resource: okf/memory-grooming.md
tags: [memory, grooming, capacity, hermes, agent:ned, prismatic-engine]
timestamp: 2026-06-19T17:30:00Z
linear_issue: GRO-2021,GRO-2022
git_repo: mbgulden/prismatic-engine
git_path: okf/memory-grooming.md
last_verified: 2026-06-19
verified_by: ned
status: current
---

# Hermes Memory Grooming + Capacity Check

**Status:** LIVE as of Jun 19 2026. Two crons active in orchestrator profile.

## What this standard guarantees

Every Hermes profile's `MEMORY.md` (≤2200 chars) and `USER.md` (≤1375 chars) stays within capacity bounds. This is critical because **memory is injected as system prompt every turn** — every char of memory eats the per-turn context budget.

Without this standard, memory grows unbounded and you hit:
- The "compression hard limit" (silent truncation mid-session)
- Increased per-call token costs (every turn reads the full memory)
- Slower response times (larger context = slower model inference)
- Eventually the agent "forgets" earlier context because old content gets pushed out by newer content

## Caps

| File | Hard cap | Reason |
|------|----------|--------|
| `MEMORY.md` | 2,200 chars | Injected as system prompt every turn |
| `USER.md` | 1,375 chars | Same — describes who the user is |

These caps were decided as part of GRO-2033 (documentation hub tier 1 decision).

## Cron schedule

| Cron | Frequency | Job ID | Script |
|------|-----------|--------|--------|
| Memory grooming | Weekly Sun 00:15 UTC | `63b5dd0ddf98` | `memory_grooming.py --apply --telegram` |
| Capacity check | Every 12 hours | `e2088800a9cbc865` | `memory_capacity_check.py` |

## What the grooming cron does

`memory_grooming.py` reads each profile's `MEMORY.md` and `USER.md`, parses the `entry\n§\nentry` format, and:

1. **Identifies stale entries** based on heuristic patterns (e.g. `GRO-####` issue refs that are now `Done`, age markers like "as of YYYY-MM-DD")
2. **Classifies each as durable or auto-removable** based on a safety whitelist of 38+ keywords (people, projects, technologies, internal codenames)
3. **Refuses to remove anything matching the whitelist** (Michael, Becca, Benjamin, William, Victoria, Ella, AGY, Jules, Hermes, Linear, HD/Human Design, Splenic/Projector/Generator/Manifestor, Prismatic, Telegram, Ollama, GPU, Tailscale, AGPL, PII, Darius, darius-star, cron, `§` markers, internal IPs)
4. **Preserves first and last entries as anchors**
5. **Refuses to write below 200 chars** (safety floor)
6. **Sends a Telegram summary** of what was removed (or "all clear")
7. **Always backs up** the file before modifying (`.bak-preapply-<timestamp>`)
8. **Supports `--dry-run`** for safe testing

Output: `/home/ubuntu/.hermes/profiles/ned/cron/output/<job_id>/`

## What the capacity check does

`memory_capacity_check.py` is much simpler:

1. Walks all 22 profile directories
2. Reads each `MEMORY.md` and `USER.md` char count
3. Computes % of cap (2200 / 1375)
4. **Silently when healthy** (no Telegram ping)
5. **Posts a single summary to Telegram** when any file crosses 80%

The "silent when healthy" behavior is important: a healthy state should be invisible. You only want to hear about it when something needs attention.

## Why this matters

Without memory bounds:
- **Token cost** scales linearly with memory size. A 5000-char memory is **2.3×** more expensive per turn than a 2200-char memory.
- **Compression triggers early** (you hit the threshold faster)
- **Agent context "rotates"** — newer content pushes out older, but there's no guarantee what's preserved

With memory bounds:
- Token cost is predictable
- Capacity check catches drift before it bites
- Grooming removes genuinely stale entries (e.g. "GRO-2021 in progress" becomes obsolete once GRO-2021 is done)
- The whitelist ensures nothing important (people, projects, technologies) ever gets pruned

## Configuration

`memory_grooming.py` accepts:
- `--profile <name>` — target one profile (default: all)
- `--apply` — actually write the pruned file (default: dry-run report only)
- `--dry-run` — explicit dry-run (redundant with default behavior)
- `--telegram` — post summary to Telegram
- `--verbose` — show per-entry decisions

`memory_capacity_check.py` accepts:
- `--threshold` (default 80) — % cap that triggers an alert
- `--dry-run` — don't write the JSONL trend log

## When to run them manually

```bash
# Memory grooming on a single profile, dry-run first
PRISMATIC_HOME=/home/ubuntu/work python3 \
  /home/ubuntu/.hermes/profiles/orchestrator/scripts/memory_grooming.py \
  --profile orchestrator --dry-run --verbose

# Capacity check
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/memory_capacity_check.py \
  --dry-run
```

## Backup convention

Every groom run creates a backup:
- `~/.hermes/profiles/<name>/memories/MEMORY.md.bak-preapply-<timestamp>`
- `~/.hermes/profiles/<name>/memories/USER.md.bak-preapply-<timestamp>`

Backups are NOT auto-pruned (intentional — they're the safety net for "what if the groom was wrong?"). After 100+ backups accumulate, a future sweep should add `find ... -mtime +90 -delete` or similar.

## Hand-off

This standard is **maintained by `agent:ned`** (memory hygiene is part of the production-readiness sweep). If memory bounds feel wrong for a new profile:

1. Check the profile's SOUL.md to see if it's a "persona" (chat IDs, active models) vs "infrastructure" (cron IDs, paths)
2. Adjust the durabile_keywords whitelist if needed (script has 38+ keywords; add new ones carefully)
3. Run a dry-run grooming and review what would be removed

If you add new types of "durable" content, extend the whitelist rather than weakening the cap. The cap exists because unbounded memory is the failure mode we're preventing.

## Related

- `portable-skills/agent-ned/SKILL.md` — Ned's skill, with memory hygiene references
- `~/.hermes/profiles/ned/memories/MEMORY.md` — runtime pointer to this standard
- GRO-2021 (memory grooming), GRO-2022 (capacity check), GRO-2035 (memory vs docs boundary)
- `~/.hermes/profiles/ned/skills/infrastructure/infrastructure-health-sweep/references/memory-grooming-script-pitfalls.md` — agent-side pitfall notes

---

*Shipped by `agent:ned` as part of the broader memory infrastructure work. Cron IDs visible in `~/.hermes/profiles/orchestrator/cron/jobs.json`.*
