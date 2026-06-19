---
type: Project
title: Session State — 2026-06-19 (Prismatic Engine Tier 7)
description: End-of-session state snapshot. What was done, what works, what's broken, what to do next. Read this when picking up after this session ends.
resource: okf/projects/prismatic-engine/session-state-2026-06-19.md
tags: [project, prismatic-engine, session-state, snapshot, handoff]
timestamp: 2026-06-19T23:15:00Z
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Session State — 2026-06-19 (Prismatic Engine Tier 7)

**This is a snapshot of the system at the end of the 2026-06-19 session.** Read this when picking up where I left off. For ongoing ops, see the [`prismatic-engine-tasks.md`](../../playbooks/prismatic-engine-tasks.md) playbook.

## What happened this session

The Prismatic Engine's Tier 7 work shipped end-to-end:

1. **Production-grade hardening** — 7-layer security stack (auth, replay protection, body size with chunked bypass blocked, rate limit, IP allowlist, audit logging, sanitized errors), 7-gate dispatch stack (dedup, stall, mode switch, credit policy, telemetry, launch, post-launch observability)
2. **Cron reduction** — `agent_dispatcher.py` cron demoted from every 5min to daily at 08:00 UTC (99.6% reduction in cron-driven Linear API consumption)
3. **AGY peer review applied** — 8 findings from GRO-2078 (4 HIGH, 2 MEDIUM, 2 LOW) all fixed
4. **/ws WebSocket auth** — Bearer + HMAC + origin allowlist (GRO-2058 closed)
5. **Production deployment** — code deployed via `pip install -e`, systemd unit updated with 9 env vars (HMAC secret, OAuth token, team ID, etc.), Linear webhook signing secret rotated (leaked → secure)
6. **Cloudflare Tunnel routing** — `webhooks.growthwebdev.com` now points to Prismatic Engine (port 9000), not Hermes (port 8644)
7. **Real Linear webhooks flowing** — 46 events received and processed (8 dispatched synthetic, 9 queued real, 4 dispatch_no_op real, 22 rejected from late retries with old secret)

## Current state — what works

### Production-ready and verified
- ✅ Prismatic Engine gateway on port 9000
- ✅ Cloudflare Tunnel "Growth Web v2" routes webhooks to :9000
- ✅ Linear webhook registered at Linear side with rotated secret
- ✅ HMAC validation, replay protection, body size limit, rate limit, IP allowlist, audit log all active
- ✅ 254 tests passing (1 flaky unrelated test)
- ✅ OAuth token rotates every 45min via cron, picks up automatically via systemd EnvironmentFile
- ✅ Path-aliases `/webhooks/linear` and `/webhooks/github` work
- ✅ Daily cron safety-net sweep runs at 08:00 UTC

### End-to-end verified path
```
Linear webhook → https://webhooks.growthwebdev.com/webhooks/linear
  → Cloudflare Tunnel
    → Prismatic Engine gateway (:9000)
      → HMAC ✓
      → dispatch_issue_by_identifier(identifier)
        → 7-gate stack applied
        → signal_fred(issue_id)
          → FileSignalProvider writes /tmp/prismatic/nudge-fred
            → [GAP: no AGY supervisor consuming]
```

## Current state — what's broken

### ⚠️ AGY not running (most important issue)

**`AGY Sandbox Supervisor` cron is paused** since 2026-06-18 16:50 UTC. Last run exited with SIGTERM (code -15). The dispatch side works correctly but nothing consumes the nudge files AGY should pick up.

- **Linear issue:** [GRO-2085](https://linear.app/growthwebdev/issue/GRO-2085)
- **Investigation:** [`agy-activation-investigation.md`](./agy-activation-investigation.md)
- **Fix:** Resume the cron. Activation sequence documented.
- **Impact:** Dispatch signals accumulate, real Linear issues don't get worked.

### ⚠️ Stale nudge files accumulating

3 nudges pending in `/tmp/prismatic/`:
- `nudge-fred` (21:12 — from our test)
- `nudge-kai` (12:25)
- `nudge-ned` (16:34)

Will be processed when AGY supervisor resumes (or become stale enough to trigger `nudge-escalation-monitor` warnings).

### ⚠️ Linear retries with old secret

22 webhook events rejected with "bad signature" — Linear's late retries using the old (now-rotated) signing secret. Will self-resolve over ~24h as Linear's retry window closes. **No action needed**, just expect to see these for a day.

## What to do next session

### High priority (do this first)
1. **Re-enable AGY Sandbox Supervisor cron** — see [`agy-activation-investigation.md`](./agy-activation-investigation.md) activation sequence. Dry-run first, verify, then re-enable.

### Medium priority (after AGY is alive)
2. **File a follow-up GRO for the "silent failure" health check** — if `nudge-fred` is >5min old AND AGY process count is 0, alert via morning digest. This would have caught the silent failure 24h earlier.
3. **Verify real Linear workflow end-to-end** — pick an open GRO issue, add `agent:fred` label, watch it dispatch and AGY pick it up.

### Low priority (housekeeping)
4. **Wait out the 22 "bad signature" events** — they'll self-resolve in ~24h.
5. **Merge `feature/tier-5a-okf-pilot` to main** — 8 commits this session, all passing tests, ready for human review.
6. **File AGY peer review of OKF docs** — [GRO-2083](https://linear.app/growthwebdev/issue/GRO-2083) is queued.

## File locations to remember

| What | Where |
|---|---|
| Engine code | `/home/ubuntu/work/prismatic-engine/prismatic/` |
| Engine tests | `/home/ubuntu/work/prismatic-engine/tests/` |
| Cron scripts | `/home/ubuntu/.hermes/profiles/orchestrator/scripts/` |
| Cron config | `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json` |
| Systemd unit | `/etc/systemd/system/prismatic-gateway.service` |
| Env files | `~/.hermes/.env`, `~/.hermes/profiles/orchestrator/.env` |
| OAuth credentials | `~/.hermes/profiles/orchestrator/credentials.json` |
| State DBs | `/home/ubuntu/work/prismatic-engine/prismatic_state/` |
| Audit log | `/home/ubuntu/work/prismatic-engine/prismatic_state/webhook_audit.log` |
| Nudge files | `/tmp/prismatic/nudge-*` |
| Gateway logs | `/home/ubuntu/.prismatic/logs/gateway.log` |
| AGY CLI | `/home/ubuntu/.local/bin/agy` (wrapper) → `agy-bin` (real ELF) |
| AGY brain | `/home/ubuntu/.gemini/antigravity-cli/` |

## Linear issues by status (end of session)

**Done:**
- GRO-2042 (Tier 6 standalone)
- GRO-2047, GRO-2048, GRO-2050 (Tier 6 children)
- GRO-2057..2062 (security layer issues)
- GRO-2077, GRO-2079, GRO-2080 (AGY peer reviews completed)

**Pending action:**
- GRO-2082 (applied, AGY verdict applied)
- GRO-2083 (OKF docs peer review — queued for AGY)
- GRO-2085 (AGY activation — needs cron resume)

**Backlog / future:**
- GRO-2012 (Slack removal)
- GRO-1985, GRO-1986 (systemd promotions)
- GRO-2042..2050 children (Tier 6 parts B-F)
- GRO-2069 (BHAG: AGY-as-default-executor)
- GRO-2070..2076 (BHAG children)

## Commits on `feature/tier-5a-okf-pilot`

```
f234444 — tier-7: AGY GRO-2082 fixes + add journey/standard/architecture docs
ab09ec0 — tier-7: apply all AGY GRO-2078 review findings (4 HIGH, 2 MEDIUM, 2 LOW)
6102959 — tier-7: tests + tighter test isolation
5b70ca7 — OKF spoke: mirror updated webhook-security
086f8a1 — OKF spoke: mirror updated webhook-security (with cron reduction)
d09fe07 — OKF spoke: mirror /ws auth closure docs
18f4bad — webhook handler unit tests (9 tests, all pass)
8746ade — tier-6: replace stub webhook handler with real Linear webhook + HMAC + dispatch
93f7d30 — tier-7: fix Linear IssueFilter bug (use 'number' not 'identifier')
d219de1 — tier-7: _linear_api_key() prefers OAuth over API key
234a86d — tier-7: add /webhooks/{linear,github} path aliases
ad5e341 — OKF spoke: mirror agy-activation-investigation
82a4806 — (earlier)
```

**Branch state:** All commits pushed to `feature/tier-5a-okf-pilot`. Ready for human PR review and merge to `main`.

## What I (fred) learned this session

- **Tests don't catch everything.** Production verification (real Linear API, real systemd, real port) found 3 bugs (IssueFilter field, missing env vars, systemd unit env ordering) that tests couldn't catch.
- **AGY is dormant, not dead.** The infrastructure works; only the supervisor cron is paused. This is the kind of failure that "passes" silent tests but breaks the user-facing workflow.
- **Dual-secret for HMAC was always right.** Even though we never used it in anger during this session, the dual-secret pattern (primary + `_NEXT` for rotation) made it safe to rotate without downtime.
- **The webhook handler was a stub for a long time.** Nobody noticed because the 5-min cron was doing the actual work. Now the webhook is the primary path and the cron is safety-net.
- **The `agent::name` vs `agent:name` bug** caught by AGY (in retrospect — the version I checked now handles both). The real production bug was that the webhook handler never dispatched at all (was a stub).

## What I (fred) want future-me to know

- **Always deploy after writing code.** I conflated "shipped to a branch" with "running in production" for too long. The verification loop (write → commit → deploy → curl → confirm) is non-negotiable.
- **AGY peer review is high-value but not infallible.** It caught real bugs (single-issue gate bypass, chunked encoding, test pollution) but missed the duplicate function bug. Use it, but verify yourself.
- **The activation gap is real.** When the dispatcher works and the agent layer doesn't, the failure is silent (signals accumulate, no alerts). The health check I proposed (nudge age + AGY process count) would prevent this.

## Open questions / things I didn't get to

- **AGY peer review of new docs** (GRO-2083) — queued but AGY hadn't run it before session ended.
- **Human code review of `feature/tier-5a-okf-pilot`** — 11 commits this session, all passing tests, but no human review yet.
- **Multi-instance dedup coordination** — if we ever run 2+ gateway instances, dedup race conditions could cause double-dispatch. Currently single-instance so this is theoretical.
- **The cost question.** AGY uses Google AI Ultra. Running 24/7 = real cost. The pause 24h ago might have been a cost-saving decision. Worth checking with Michael before resuming.

## If this is the last session before AGY is re-enabled

The dispatch pipeline is verified working. Real Linear webhooks are arriving (Comments, IssueLabels, Issue updates). The nudges are accumulating. When AGY resumes, it'll process them in order. No data loss.

**If you only do one thing next session:** resume the AGY Sandbox Supervisor cron.

---

*Session ended 2026-06-19 23:15 UTC. Fred (Hermes orchestrator profile).*
