# GRO-504–512 + GRO-537 — `agent:ned` Batch Routing Triage Note

**Issue:** GRO-508 — PHASE 2: Build HD Personalization Engine (anchor issue for this batch)
**Triage owner:** Ned (infrastructure) — not the correct lane for execution
**Status as of 2026-06-27 ~23Z:** Out of lane — 10-issue `agent:ned` backlog is the same routing-blocker Michael dequeued earlier today
**Branch:** `ned/GRO-508` (triage-only run, no source changes outside `scripts/ops/`)

---

## Why this batch does not belong on Ned's queue

The Prismatic Engine scanner surfaced **10 Linear issues** labeled `agent:ned` in this run:

```
1. GRO-537: Design and build brand home page
2. GRO-512: PHASE 2: Paid Launch — Cohort 1, $997/person
3. GRO-511: PHASE 2: Beta Launch — 5 Students, Free, Heavy Feedback
4. GRO-510: PHASE 2: Record Bootcamp Video Content
5. GRO-509: PHASE 2: Build Community Platform MVP
6. GRO-508: PHASE 2: Build HD Personalization Engine
7. GRO-507: PHASE 2: Design Multi-Type Curriculum Architecture
8. GRO-506: PHASE 1: Retrospective — What worked, what did not, gate for Phase 2
9. GRO-505: PHASE 1: Execute Week 4 — MSP Partnership Playbook and Live Fire
10. GRO-504: PHASE 1: Execute Week 3 — Enterprise Sales and Procurement
```

**All 10 are content / marketing / product / launch-ops / phase-planning work** — none are infrastructure tasks.

Michael has **explicitly dequeued this batch three times today**:

- **12:39 UTC** — "Ned — routing blocker" on GRO-537 / GRO-512 (first wave)
- **17:25 UTC** — "Ned triage — out of lane (systemic)" across the wave
- **22:33 UTC** — "routing blocker (re-flag)" across GRO-504–508 + GRO-509–512 + GRO-537

In the most recent comment (GRO-508, 22:33 UTC), Michael was explicit:

> **Why no `finalize_task.sh` call:** running it would falsely transition the Linear state to "In Review" without any real code change. I refuse to fabricate work in a forbidden lane just to clear a queue flag.

This run follows that instruction: **no `finalize_task.sh`, no state transition, no fabricated code**.

---

## Lane ownership reminder (Ned)

From the Prismatic Engine workspace governance and Ned's agent contract:

- ✅ Write: `scripts/`, `prismatic/`, `plugins/`, plus ops/lane hygiene
- ❌ Do NOT modify: `content/`, `assets/`, `designs/`, `research/`, `active-oahu/`
- ❌ Do NOT build: marketing landing pages, copy, lead magnets, social-proof modules, pricing pages, blog content, video scripts, Gumroad checkouts, bootcamp curriculum, launch-cohort landing flows

Ned's domain is strictly:

1. **GPU nodes** — Ollama Qwen 32B + Hermes 70B on `k3s-node-230` (100.78.237.7)
2. **Disk space watchdog** — Hermes VM `~/`, NAS mounts, 85%/90% thresholds
3. **GitHub hygiene** — stale repos (>7d no push), `.gitignore` coverage, repo size, untracked accumulation
4. **Cloudflare deployments** — Pages, tunnels, DNS/SSL expiry
5. **Swarm agent health** — Kai, Autobot, Jamie, Sage, Sam, gateway processes
6. **Prismatic Engine hygiene** — lane discipline, label routing, lock/heartbeat correctness

---

## The 10 issues mapped to their correct lanes

| Issue | Title | Correct lane | Why Ned can't execute |
| --- | --- | --- | --- |
| GRO-537 | Design and build brand home page | Designer / coder (Beyond SaaS / Belief Deprogrammer) | Marketing site build, copy + assets — read-only on `content/`, `assets/`, `designs/` |
| GRO-512 | PHASE 2: Paid Launch — Cohort 1, $997/person | Launch ops / Fred + Michael | Cohort pricing, payment config, refund policy — revenue-critical human decisions |
| GRO-511 | PHASE 2: Beta Launch — 5 Students, Free, Heavy Feedback | Launch ops / Fred + Michael | Cohort selection criteria, intake form, feedback collection — coordination, not infra |
| GRO-510 | PHASE 2: Record Bootcamp Video Content | Video production / Michael | Recording schedule, edit specs, hosting decision — physical / creative workflow |
| GRO-509 | PHASE 2: Build Community Platform MVP | Coder / PM (Community platform build) | Platform selection (Circle, Discord, Mighty, custom) + UX — out of Ned's lane |
| GRO-508 | PHASE 2: Build HD Personalization Engine | Coder / data (Human Design provider integration) | Chart-calc engine, synthesis pipeline, recommendation logic — `providers/hd_synthesis/` work, not Ned infra |
| GRO-507 | PHASE 2: Design Multi-Type Curriculum Architecture | Curriculum designer (Fred / Michael) | Multi-type taxonomy + module breakdown — content design |
| GRO-506 | PHASE 1: Retrospective | Michael (human retrospective) | Decisions about Phase 2 gate criteria, not code |
| GRO-505 | PHASE 1: Execute Week 4 — MSP Partnership Playbook and Live Fire | Michael + sales lane | Outbound sales, partnership contracts — human revenue work |
| GRO-504 | PHASE 1: Execute Week 3 — Enterprise Sales and Procurement | Michael + sales lane | Enterprise pipeline + procurement — human revenue work |

---

## Root cause: scanner routing bug

Confirmed in `prismatic/state_machine.py:574–588` — the canonical agent-label map is:

```python
label_map: dict[str, Step] = {
    "agent:fred":  Step.DECOMPOSE,
    "agent:kai":   Step.DISPATCH,
    "agent:agy":   Step.EXECUTE,
    "agent:jules": Step.REVIEW,
    "agent:codex": Step.REFINE,
    "agent:done":  Step.INTEGRATE,
    "agent::fred": Step.DECOMPOSE,  # alt format
    "agent::kai":  Step.DISPATCH,
    # ... (no "agent:ned" entry — Ned is infra-only, not pipeline-step)
}
```

`agent:ned` is **not** in the canonical label map. The `prismatic/lanes/ned/scan_tasks.py` referenced by the cron job (`script: prismatic/lanes/ned/scan_tasks.py`) does not exist on `origin/deploy-fresh` HEAD (`617922ff`). Whatever cron wrapper is invoking the scanner appears to be defaulting the label to `agent:ned` when it can't classify an issue.

**Fix recommendation (for the next dispatcher-config PR — not in this triage):**

1. Verify `prismatic/lanes/ned/scan_tasks.py` exists at the path the cron config expects, or update the cron `script:` field
2. Add explicit label-matching for `agent:fred` / `agent:kai` / `agent:agy` / `agent:jules` / `agent:codex` so the scanner does not fall back to `agent:ned`
3. Reserve `agent:ned` strictly for issues carrying infrastructure keywords (GPU, disk, CF, Tailscale, swarm, lane, lock) so the label means what it says

---

## Action taken by Ned (this run)

- Read skeleton: `~/.hermes/profiles/ned/scripts/autonomous-task-skeleton.md` (Step 4 lane guard explicitly applies)
- Read all 10 issues' most recent comments via Linear GraphQL — confirmed Michael's dequeue pattern is current and explicit
- Acquired lock on `scripts/ops/` lane (`ned` agent — stored as `prismatic-engine` in swarm_locks.json per the swarm.js CLI convention)
- Created branch `ned/GRO-508` from `origin/deploy-fresh` (current HEAD `617922ff`)
- Wrote this triage note as the only truthful deliverable
- **Did NOT call `finalize_task.sh`** — per Michael's 22:33 UTC instruction (would falsely transition state)
- **Did NOT push** the branch — triage-only, no remote action needed

---

## Operational follow-ups Ned can pick up unprompted

While not blocking on these 10 marketing/launch items, here are infra-side items Ned **can** do without being asked:

1. **Daily infra health sweep** — GPU ping, Ollama tag check, disk usage on Hermes VM + NAS mounts, GitHub stale-repo scan, CF Pages status. Already in Ned's cron contract; continue running.
2. **Add CF Pages + DNS health check for `belief-deprogrammer.com`** — if Pages deployment exists, add to Ned's daily sweep (low-risk infra work)
3. **Scanner routing fix** — file a separate Linear issue with label `agent:fred` (or whoever owns the dispatcher) pointing at the missing `scan_tasks.py` path + the `agent:ned` fallback bug
4. **Lock-discipline check** — verify no agent is silently holding `scripts/`, `prismatic/`, `plugins/` for >5 minutes with stale heartbeat

These will surface as separate cron findings rather than rolling them into the GRO-508 comment thread.

---

## Sibling triage notes (existing precedent in this repo)

- `scripts/ops/gro-542-contact-booking-triage.md` (commit `185acb80`)
- `scripts/ops/gro-545-social-proof-triage.md` (commit `4a349797`)
- `scripts/ops/gro-558-landing-pages-triage.md` (commit `a4f6f52e`)
- `scripts/ops/gro-559-email-capture-triage.md` (commit `bc86fc63`) — canonical reference for batch triage pattern
- `scripts/ops/gro-564-cpa-reengage-triage.md` (commit `5e4368c1`) — human-action pattern
- `scripts/ops/gro-567-cpa-balance-triage.md` (commit `28b0307f`) — human-action pattern

This file follows the same pattern as the GRO-559 reference, updated for the **current 10-issue `agent:ned` backlog** as of 2026-06-27 ~23Z.
