# Prismatic Engine — Comprehensive Rubric & Assessment
**Date:** June 11, 2026  
**Based on:** 24 AGY reports (June 8), 7-day field operation, architecture spec v1

---

## Scoring Scale

| Score | Meaning |
|-------|---------|
| **5** | Production-grade, documented, tested, used daily |
| **4** | Working but rough edges — manual steps, missing docs |
| **3** | Partial — works in some contexts, gaps in others |
| **2** | Designed/specified but not built |
| **1** | Identified as needed but no plan |
| **0** | Not considered / blind spot |

---

# SECTION A: Dispatch — "Who does what?"

### A1. Task Intake
- [ ] **Linear polling** — dispatcher queries Linear for `agent:*` labels  
  **Score: 4** — Works. 15-min cron. Dedup bug fixed today (was blocking re-dispatch).
- [ ] **Multi-source intake** — GitHub Issues, Jira, email-to-task, Slack commands  
  **Score: 1** — Linear only. Design exists for standalone SQLite queue.
- [ ] **Webhook-driven (push, don't poll)** — Linear webhooks → instant dispatch  
  **Score: 0** — Cron polling only. 15-min latency ceiling.
- [ ] **Priority-aware routing** — P1 issues routed before P4  
  **Score: 2** — Priority exists in Linear but dispatcher is FIFO per label.

### A2. Agent Signaling
- [ ] **Signal provider works reliably** — agents wake up when dispatched  
  **Score: 4** — FileSignalProvider writes to `/tmp/prismatic/nudge-{agent}`. Works for Fred/Ned/Kai.
- [ ] **Multi-transport signaling** — HTTP, Redis, Telegram fallbacks  
  **Score: 2** — FallbackChain designed, HTTP/Redis stubbed, only FileSignalProvider active.
- [ ] **Signal confirmation/ack** — agent acknowledges receipt  
  **Score: 1** — Signal sent, no ack. Dispatcher doesn't know if agent received it.
- [ ] **Stale signal detection** — if agent doesn't pick up within N minutes, escalate  
  **Score: 0** — No escalation path. Signal sits in nudge file indefinitely.

### A3. Deduplication
- [ ] **True duplicate detection** — blocks only identical re-dispatches, not retries  
  **Score: 4** — Fixed today. Now only blocks launch-mode agents, signal agents skip dedup.
- [ ] **TTL proportional to priority** — high-priority retries faster  
  **Score: 0** — Fixed 60-min TTL regardless of priority.

### A4. Agent Lane Assignment
- [ ] **Lane map exists** — each agent knows what directories it owns  
  **Score: 2** — Spec exists in PRISMATIC_ENGINE.yaml. Not enforced in code.
- [ ] **Lane validation at dispatch** — dispatcher checks lane ownership before routing  
  **Score: 1** — Not implemented. Dispatcher routes by label only.
- [ ] **Cross-agent handoff** — completed work auto-routes to reviewer  
  **Score: 3** — Works via label swap (agent:ned→agent:fred). But manual, not automated.

**Dispatch subtotal: 23 / 55**

---

# SECTION B: Governance — "How do they not break each other?"

### B1. File Claim System
- [ ] **Lock before edit** — agents claim files before modifying  
  **Score: 0** — Designed but not built. No central lock registry exists (`~/.antigravity/` empty).
- [ ] **Lock by relative path** — not absolute, cross-machine compatible  
  **Score: 2** — Spec corrected by AGY (v1 used absolute paths). Code not implemented.
- [ ] **Heartbeat pings** — locks auto-release if agent dies  
  **Score: 0** — Designed. No heartbeat daemon running.
- [ ] **Stale lock cleanup** — automatic release of abandoned locks  
  **Score: 0** — Designed as lazy pruning. Not implemented.

### B2. Branch Governance
- [ ] **Agent-specific branch prefixes** — `content/`, `feature/`, `design/`  
  **Score: 2** — Convention documented. Not enforced. Ned works on master directly per Michael's directive.
- [ ] **Pre-push hooks** — validates lane ownership, branch name, locks  
  **Score: 0** — Not built. Python hook script spec exists.
- [ ] **Staging governor** — only Fred merges to deploy-fresh  
  **Score: 0** — Not enforced. Agents push directly to master in practice.
- [ ] **Main protection** — no agent can push to production without approval  
  **Score: 0** — Cloudflare Pages auto-deploys on master push. No gate.

### B3. Conflict Prevention
- [ ] **Pre-push diff analysis** — checks if another agent modified same files  
  **Score: 0** — Conflict predictor designed. Not implemented.
- [ ] **Semantic dependency detection** — changing a shared type breaks consumers  
  **Score: 1** — AGY identified this as missing. No implementation.
- [ ] **Merge conflict auto-resolution** — agent notified of conflict with resolution steps  
  **Score: 0** — No tooling. Manual fix only.

### B4. Agent Identity
- [ ] **Commit attribution** — agent prefix in commit messages  
  **Score: 3** — Ned uses commit messages with issue refs. Not consistent across agents.
- [ ] **Git config per agent** — separate user.email per Hermes profile  
  **Score: 2** — Profile configs exist. Not systematically enforced.
- [ ] **Audit trail** — who changed what, when, under which task  
  **Score: 3** — Git log + Linear comments provide partial trail.

**Governance subtotal: 13 / 55**

---

# SECTION C: Visibility — "Can you trust what you can't see?"

### C1. Dashboard
- [ ] **Lock state display** — visual map of who holds what lock  
  **Score: 0** — Designed. No dashboard built.
- [ ] **Agent activity feed** — last action, current task, run history  
  **Score: 1** — Linear provides issue state. No unified agent feed.
- [ ] **Blocked agent alerts** — agent stuck > N minutes → notification  
  **Score: 0** — No alerting. Stale issues sit in Backlog silently.

### C2. Observability
- [ ] **Cron job health dashboard** — which jobs are green/yellow/red  
  **Score: 2** — `cronjob(action='list')` shows status. No dashboard, manual check.
- [ ] **Agent throughput metrics** — tasks completed per hour/day per agent  
  **Score: 1** — Countable from Linear. No automated tracking.
- [ ] **Error rate tracking** — failed dispatches, failed agent runs, timeouts  
  **Score: 1** — Cron output files capture errors. No aggregation.

### C3. Notification
- [ ] **Review-needed alerts** — agent:fred issues surfaced to Fred  
  **Score: 3** — Works via dispatcher + nudge files. Fred sees them.
- [ ] **Escalation path** — stuck tasks escalate after N failures  
  **Score: 2** — AGY stall recovery has 3-retry escalation. Only for AGY.
- [ ] **Completion summaries** — daily digest of what the swarm accomplished  
  **Score: 3** — Golden Thread Daily Digest cron works. Morning briefing too.

**Visibility subtotal: 13 / 45**

---

# SECTION D: Refinement — "How does work improve?"

### D1. 7-Step Loop
- [ ] **Decompose** — megaprompt → specialized agent contracts  
  **Score: 3** — AGY does this naturally for research. Not formalized.
- [ ] **Dispatch** — route each contract to right agent  
  **Score: 3** — Works via Linear labels + dispatcher.
- [ ] **Execute** — agent does scoped work  
  **Score: 4** — Ned executes well. Fred orchestrates. AGY researches.
- [ ] **Review** — specialist reviewer checks work  
  **Score: 2** — Fred reviews Ned. But no AGY review of Fred, no Kai review of AGY.
- [ ] **Feedback** — issues found → revision request  
  **Score: 1** — Linear comments serve as feedback. No structured revision loop.
- [ ] **Refine** — agent revises based on review  
  **Score: 1** — Ad-hoc. No systematic re-dispatch for revisions.
- [ ] **Integrate** — approved work merged, next phase triggered  
  **Score: 3** — Works for code (git push). No automated next-phase trigger.

### D2. Mode Switch
- [ ] **Interactive mode** — human approves each handoff  
  **Score: 2** — Design exists. Not implemented as config switch.
- [ ] **Collaborative mode** — human approves at breakpoints (current mode)  
  **Score: 3** — This is how we operate today. Not formalized.
- [ ] **Autonomous mode** — AI-only reviews, human gets summary  
  **Score: 2** — Nightly autonomous backlogger exists but is fragile.

### D3. Quality Gates
- [ ] **Syntax/lint gate** — code must pass syntax check before review  
  **Score: 2** — `verify_syntax.py` exists for darius-star. Not universal.
- [ ] **Test gate** — tests must pass before merge  
  **Score: 1** — Playwright tests exist for darius-star. Not run automatically.
- [ ] **Style gate** — code must match conventions  
  **Score: 0** — Not enforced. AGY flagged 8-space indentation in audio.js.

**Refinement subtotal: 27 / 60**

---

# SECTION E: Portability — "Can others use this?"

### E1. Standalone Mode
- [ ] **Offline operation** — works without Linear/GitHub/Hermes cloud  
  **Score: 1** — Spec exists. SQLite task queue designed. Not built.
- [ ] **Local task queue** — SQLite-based `prismatic_tasks.db`  
  **Score: 1** — Schema designed. No DB created.
- [ ] **Subprocess/Docker adapters** — run agents without Hermes gateway  
  **Score: 0** — Designed. Not implemented.

### E2. Installation
- [ ] **pip install prismatic-engine** — single command setup  
  **Score: 0** — No pip package. Code exists in `/prismatic-engine/` but not packaged.
- [ ] **Five-step quickstart** — init → add → run → status → done  
  **Score: 0** — Spec exists. No working CLI.
- [ ] **Configuration wizard** — `prismatic-engine init`  
  **Score: 0** — Designed. Not built.

### E3. Portable Skills
- [ ] **Skills library is browsable** — people can discover skills  
  **Score: 2** — `skills_list()` works within Hermes. No external catalog.
- [ ] **Skills are self-contained** — portable between Hermes instances  
  **Score: 3** — SKILL.md format with references works. No export/packaging.
- [ ] **Skill quality ratings** — proven vs experimental  
  **Score: 0** — No quality metadata on skills.
- [ ] **Skill dependencies declared** — "this skill requires X command"  
  **Score: 2** — `prerequisites` and `required_commands` fields exist. Inconsistently populated.

### E4. Agent Profiles
- [ ] **Profile templates** — new agent can be created from template  
  **Score: 1** — Ned's profile exists. No template system.
- [ ] **Profile documentation** — SOUL.md explains agent's role, lane, capabilities  
  **Score: 1** — SOUL.md amendments designed. Never applied.
- [ ] **Profile portability** — agent profile can run on different machines  
  **Score: 2** — Profiles are directory-based. Could be copied. No tooling.

**Portability subtotal: 13 / 50**

---

# SECTION F: Skills Library Audit — What's Ready to Share?

| Skill | Quality | Portable? | Dependencies | Ready? |
|-------|---------|-----------|-------------|--------|
| **orchestrator-delegation-discipline** | 5/5 | ✅ | None (conceptual) | **YES** |
| **golden-thread** | 5/5 | ⚠️ | Linear API key | Needs Linear abstraction |
| **autonomous-execution-discipline** | 4/5 | ✅ | None (conceptual) | **YES** |
| **human-design-computation** | 4/5 | ⚠️ | Swiss Ephemeris | Needs bundled data |
| **daily-transit-briefing** | 4/5 | ⚠️ | HD computation | Needs HD engine |
| **static-site-seo-fix** | 4/5 | ✅ | curl, grep | **YES** |
| **cloudflare-deployment** | 3/5 | ⚠️ | CF API token | Needs auth setup |
| **agent-ned** | 4/5 | ⚠️ | Hermes cron | Hermes-specific |
| **agent-orchestration/agy*** | 3/5 | ❌ | AGY CLI binary | **NOT PORTABLE** |
| **himalaya** (email) | 4/5 | ✅ | himalaya CLI | **YES** (with config) |
| **github-pr-workflow** | 4/5 | ✅ | gh CLI | **YES** |
| **systematic-debugging** | 4/5 | ✅ | None | **YES** |
| **test-driven-development** | 3/5 | ✅ | Language test runner | **YES** (generic) |

**Ready-to-share count: 7 of 13 audited**

---

# Comprehensive Questions the Rubric Must Answer

1. **Can a new user install this in under 5 minutes?** → Current answer: No. Score 0/5.
2. **Can two agents work on the same repo without conflicts?** → Only by convention, not enforcement. Score 2/5.
3. **If an agent crashes, does the system recover?** → Locks would persist forever. Score 0/5.
4. **Can you see what every agent is doing right now?** → Only by checking Linear + cron status manually. Score 2/5.
5. **Does the system work offline?** → No. Everything depends on Linear + GitHub + Hermes cloud. Score 0/5.
6. **Can you add a new agent without modifying core code?** → Manual profile creation. No plugin system. Score 1/5.
7. **Are task handoffs between agents reliable?** → Label swap works, but no confirmation/ack. Score 3/5.
8. **Can a non-technical person review agent outputs?** → No dashboard. Must read Linear. Score 1/5.
9. **Does the system prevent duplicate work?** → Dedup exists for launch agents. Not perfect. Score 3/5.
10. **Can you measure swarm throughput?** → Manual counting from Linear. No metrics. Score 1/5.
11. **Are agent skills discoverable?** → Only via `skills_list()` inside Hermes. Score 2/5.
12. **Can skills be installed separately from the engine?** → No packaging system. Score 0/5.
13. **Does the system survive a reboot?** → Cron jobs restart. Running agents die and don't resume. Score 2/5.
14. **Is there an audit trail for every change?** → Git + Linear. Partial. Score 3/5.
15. **Can a friend/client deploy this on their own hardware?** → Not without significant setup. Score 1/5.

---

# Overall Scores

| Section | Score | Max | % |
|---------|-------|-----|---|
| A. Dispatch | 23 | 55 | 42% |
| B. Governance | 13 | 55 | 24% |
| C. Visibility | 13 | 45 | 29% |
| D. Refinement | 27 | 60 | 45% |
| E. Portability | 13 | 50 | 26% |
| **TOTAL** | **89** | **265** | **34%** |

---

# Top 10 Gaps (Priority Order)

1. **No file locking** → Two agents can edit the same file. 0/5.
2. **No standalone mode** → Can't run offline or share with others. 0/5.
3. **No dashboard** → Can't see what agents are doing. 0/5.
4. **No branch enforcement** → Agents push directly to master. 0/5.
5. **No pre-push validation** → Lane violations undetected. 0/5.
6. **No pip package** → Can't be installed by others. 0/5.
7. **No heartbeat/cleanup** → Crash recovery nonexistent. 0/5.
8. **No agent profile templates** → Creating new agents is manual. 1/5.
9. **No skill quality ratings** → Users can't distinguish proven from experimental. 0/5.
10. **No structured review loop** → Feedback is ad-hoc. 1/5.

---

# What Actually Works Today (Strengths)

1. **Linear → agent dispatch** — labels route tasks to the right agent. Tuned and debugged.
2. **Signal provider** — nudge files wake agents. Simple, reliable.
3. **Ned as workhorse** — executes code tasks autonomously on 5-min cron.
4. **AGY as researcher** — produces thorough reports, design docs, audits.
5. **Golden thread** — project continuity across sessions. Registry updated.
6. **Cron job fleet** — 32 jobs running, mostly green. Morning briefing, daily digest.
7. **Multi-agent parallel work** — Ned + AGY worked simultaneously today on different tasks.
8. **Label-based handoff** — agent:ned→agent:fred→agent:done pipeline works.
9. **Skills system** — 130+ skills, loadable by agents, with references and scripts.
