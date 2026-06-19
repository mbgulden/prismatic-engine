# BHAG Handoff Brief — AGY-as-Default-Executor

> **Author:** Ned (preparing this for Fred to launch once GRO-2042 ships)
> **Linear:** [GRO-2069](https://linear.app/growthwebdev/issue/GRO-2069/bhag-agy-as-default-executor-single-coherent-agent-layer) (parent, Backlog) + GRO-2070..GRO-2076 (children, all Backlog)
> **Status:** Filed, NOT FOR EXECUTION. Waiting on GRO-2042 (dispatcher unification) to close.

---

## TL;DR

The next big hairy audacious goal after Fred finishes unifying dispatch is: **make AGY the default executor for all coding tasks**, with deepseek-v4-pro as the inference backend and a quality loop that catches regressions before they ship.

**Why this is the right next BHAG**: It compounds. Every other goal (more docs, more crons, more lint) gets easier once AGY is reliable.

---

## Why this exists

Right now the execution layer is split awkwardly:

| Agent | Role today | Strength | Weakness |
|-------|------------|----------|----------|
| **AGY** | Multi-modal, screenshot reading, any-model routing | Most raw power | Unreliable session continuity |
| **Ned** (deepseek-v4-pro) | Heavy lifting on coding tasks | Reliable, predictable | Lacks vision, slower iteration |
| **Jules** | Async GitHub-native tasks | GitHub-native | Not for in-session iteration |
| **Codex** | Per-call via dispatcher | OAuth | Rate-limited (all 3 creds 429 per agent-ned SKILL.md) |

**The split creates real costs:**
- ~10% of Ned's time is debugging AGY session failures
- Inconsistent output quality (AGY's "model routing" decides per-call)
- Slow feedback loop (peer-review pipeline GRO-2052 is underused)
- Cost profile is unpredictable (AGY's per-model selection isn't tracked)

---

## The goal (what "done" looks like)

1. **Single routing table** in `prismatic/dispatcher.py`: every `agent:*` label maps to one of {AGY, Jules, Fred} — Ned is internal-only
2. **AGY peer-review** wired into the dispatcher (every AGY run gets auto-reviewed by a second AGY session before completion)
3. **Quality metrics**: per-agent success rate over 30 days, surfaced in `prismatic/doctor.py`
4. **Backpressure**: agents with <80% success rate in last 30 days auto-flag in Linear
5. **Ned profile becomes "infrastructure-only"** — no longer takes coding tasks; only the production-readiness sweeps and infra watchdog patterns

---

## The 7 children (in execution order)

| # | Issue | Effort | Goal |
|---|-------|--------|------|
| 1 | [GRO-2070](https://linear.app/growthwebdev/issue/GRO-2070) | 1 week | Cost model — tokens-per-completion vs quality-score |
| 2 | [GRO-2071](https://linear.app/growthwebdev/issue/GRO-2071) | 2 weeks | Routing table migration — single dispatcher |
| 3 | [GRO-2072](https://linear.app/growthwebdev/issue/GRO-2072) | 2 weeks | AGY session recovery layer |
| 4 | [GRO-2073](https://linear.app/growthwebdev/issue/GRO-2073) | 1 week | Quality metrics surface |
| 5 | [GRO-2074](https://linear.app/growthwebdev/issue/GRO-2074) | 3 days | Backpressure — auto-flag in Linear |
| 6 | [GRO-2075](https://linear.app/growthwebdev/issue/GRO-2075) | 1 week | Ned deprecation |
| 7 | [GRO-2076](https://linear.app/growthwebdev/issue/GRO-2076) | 2 weeks | Cutover + 2-week monitoring |

**Total estimated effort: 4-6 weeks** after GRO-2042 closes.

---

## Dependencies (must be true before starting)

| Dependency | Status | Notes |
|------------|--------|-------|
| GRO-2037 (lint script) | ✅ Done | All LinearBudget gating shipped |
| GRO-2053..2057 (5 engine gates) | ✅ Done | Lint now green (8/8) |
| GRO-2058..2063 (production-readiness sweep) | ✅ Done | 4 crons live, observability in place |
| GRO-2042 (event-driven dispatch) | ⏳ In Progress | Fred's work |
| GRO-2047 (real webhook handler) | ⏳ In Progress | Fred's work |
| GRO-2048 (webhook → dispatcher direct) | ⏳ In Progress | Fred's work |
| GRO-2050 (reduce cron polling) | ⏳ In Progress | Fred's work |
| GRO-2052 (self-review + peer-review) | ⏳ In Progress | Fred's work |

**Critical: BHAG can't start until GRO-2042 closes.**

---

## Why this is hairy

- **Migration risk**: Ned profile has 50+ references in `agent_dispatcher.py` and crons. Re-routing them all is a multi-week project.
- **AGY reliability**: AGY's session continuity is iffy. Need a recovery layer that detects "AGY stopped responding" and re-promptes.
- **Cost**: AGY (whatever model) is more expensive per call than deepseek-v4-pro. Need to model the cost delta before committing.
- **Peer review = 2x compute**: every coding task becomes 2 AGY runs. Need to verify the quality uplift justifies this.

---

## Why this is the right BHAG (not just another feature)

This is a **re-architecture of how your agents collaborate**, not a feature. It changes:
- Which agent Michael interacts with daily (probably stays AGY, but the routing becomes invisible)
- Cost profile (up, significantly)
- Failure modes (AGY's session-loss vs Ned's nothing-happens-yet)
- The "personality" of the swarm (Ned's terse SRE voice vs AGY's more conversational tone)

It compounds: every other goal (more docs, more crons, more lint) gets easier once AGY is reliable.

---

## Hand-off

This is **Fred's lane**, not Ned's. Ned prepared the plan because:
- I have context on the dispatcher internals (GRO-2058..2063 sweep touched them)
- The migration affects Ned's profile directly (I need to be aware)
- I can shepherd the children as they ship (verify quality, file follow-ups)

But the design decisions (which model for which task, when to roll back, cost limits) are Fred's call. He should:

1. ✅ Read this brief
2. ✅ Review the 7 child issues (GRO-2070..GRO-2076)
3. ✅ Decide if the BHAG is the right next goal (vs. something else)
4. ✅ Pick the first slice (GRO-2070 cost model is the smallest)
5. ✅ Ship or defer

The moment this becomes a real project is when GRO-2042 closes.

---

## Alternative BHAGs (if this one isn't right)

- **Unify the agent memory layer** — currently every profile has its own MEMORY.md; a single shared memory with profile-specific access controls would simplify the swarm
- **Build the OKF (Open Knowledge Framework) properly** — it's mentioned in commits but never formalized; a proper spec would let other teams adopt the pattern
- **Multi-tenant Linear** — currently the GRO team is the only tenant; supporting multiple orgs would let you run as a service

---

## What I (Ned) did to prep this

1. Closed GRO-2053..GRO-2057 so the BHAG doesn't start with broken lint
2. Filed GRO-2069 (parent, Backlog) and GRO-2070..GRO-2076 (children, all Backlog)
3. Wrote this brief
4. Verified nothing in the BHAG touches code Fred is actively shipping (GRO-2042..GRO-2050)
5. Tagged everything with `agent:ned` so it shows in the right queue

The moment GRO-2042 closes, the BHAG is ready to pick up.

---

*Filed by Ned. Reviewed by: nobody yet. Ready for Fred.*