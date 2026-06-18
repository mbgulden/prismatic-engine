# Prismatic Golden Flow v0 — One Demo, One Spine

**Owner:** Fred (orchestrator) — execution by AGY
**Target repo:** `prismatic-engine` (`/home/ubuntu/work/prismatic-engine`)
**Linear parent issue:** GRO-1958 (created in this turn)
**Date:** 2026-06-18

---

## Why this doc exists

Michael’s north star for the Prismatic Engine is:

> Install engine → get value → attach capabilities as needed → detach without losing state.

Right now there are a lot of good ideas (AGY chat, schedule observatory, VCS provider layer, Linear clone concerns, plugin architecture) and no single end-to-end flow that proves the engine works.

This document locks down **one** demo flow. The flow is intentionally small. It is the spine every later feature must justify itself against.

**Rule of thumb:** if a feature does not improve this flow, it is not in v0.

---

## The golden flow (one paragraph)

> From Telegram, Fred points Prismatic at a Linear issue. Prismatic starts an AGY session on the issue, gives it a workspace, watches live output, posts a progress event to Linear, opens a PR through the GitHub API, hands the PR off to Jules CLI (jules.google.com) for review, records the schedule event, and emits a `golden_flow_completed` event the user can see on phone. The user can pause, summarize, redirect, or kill the session from Telegram at any step.

That’s it. Nothing else is v0.

---

## User story

As a builder who works on a phone, I want to:

1. Pick a Linear issue from Telegram.
2. Have AGY actually work on it.
3. See live progress on phone.
4. Steer / pause / inspect at will.
5. Get a PR with a Jules review handoff.
6. See the result land in my Linear issue and schedule view.

Without opening a laptop.

---

## Systems involved (v0 boundary)

In scope for v0:

- Prismatic Engine kernel (`prismatic-engine/`)
- Prismatic Telegram adapter (read-only commands; pause/inspect/redirect)
- Prismatic GitHub API provider (create/update branch + PR + comment)
- Prismatic Linear provider (read issue, post progress + completion comment, transition state)
- Prismatic AGY provider (start session, stream events, kill, summarize)
- Prismatic Jules CLI (jules.google.com) provider (hand off review, poll result)
- Prismatic Schedule provider (record schedule event from AGY/Jules work)
- One canonical “golden flow” task list with one shippable demo
- One Linear parent + child issues, one Telegram message, one PR, one Jules review

Out of scope for v0 (deferred, will be planned separately):

- Schedule Observatory UI
- AGY Chat TUI parity
- Multi-user / multi-tenant
- Linear clone / local task manager
- Full plugin marketplace
- SovereignSentinel
- Full remote IDE / editor
- Public API surface
- Mobile native app

---

## Capability contract (the bare minimum)

The flow only needs these capabilities to exist as named contracts. They do not have to be polished — they have to be real.

| Capability | What it must do in v0 | Backend |
|---|---|---|
| `linear` | Read issue, post comment, transition state, observe updates | Linear GraphQL via `LINEAR_API_KEY` |
| `agy` | Start a session bound to an issue, stream events, kill, summarize | `agy --print --sandbox` with normalized event capture |
| `vcs.github` | Create branch, open PR, post PR comment, fetch PR diff/status | GitHub REST/GraphQL via `GITHUB_TOKEN` / `gh` adapter |
| `jules` | Hand off a PR URL for review, poll review result | Jules CLI (jules.google.com) REST |
| `telegram` | Receive `/agy start GRO-1958`, send live status, accept `/agy pause/summarize/kill` | Telegram Bot API |
| `schedule` | Record a normalized schedule event when AGY/Jules work starts and ends | Engine-side SQLite/JSON store |
| `artifact` | Capture run output and key links as clickable artifacts | `prismatic-publish` / `prismatic-reply` |

Anything else is feature creep. If the flow can’t be built with these seven, the contract needs adjusting, not the flow.

---

## Event schema (v0 minimum)

The engine needs a normalized event stream for the flow. These are the only event types v0 requires.

```jsonc
// Session lifecycle
{ "type": "session.started",   "session_id": "...", "capability": "agy",        "issue_id": "GRO-1958" }
{ "type": "session.progress",  "session_id": "...", "capability": "agy",        "percent": 42, "summary": "Wrote tests for ..." }
{ "type": "session.paused",     "session_id": "...", "capability": "agy",        "reason": "user_command" }
{ "type": "session.killed",     "session_id": "...", "capability": "agy" }
{ "type": "session.summarized", "session_id": "...", "summary": "...", "artifacts": [...] }
{ "type": "session.completed",  "session_id": "...", "outcome": "success" }

// VCS handoff
{ "type": "vcs.branch_created",  "provider": "github",  "branch": "feature/golden-flow", "base": "main" }
{ "type": "vcs.pr_opened",       "provider": "github",  "pr_number": 12, "url": "..." }
{ "type": "vcs.pr_status",       "provider": "github",  "state": "open", "checks": "pending" }
{ "type": "vcs.pr_comment",      "provider": "github",  "pr_number": 12, "body": "..." }

// Jules review handoff
{ "type": "jules.handoff",        "pr_url": "...", "review_type": "code_review" }
{ "type": "jules.review_started", "pr_url": "..." }
{ "type": "jules.review_completed", "pr_url": "...", "verdict": "approved" | "changes_requested", "summary": "..." }

// Schedule (engine-owned record of external schedule activity)
{ "type": "schedule.recorded",    "source": "agy" | "jules" | "linear", "event": "task_started" | "task_ended", "issue_id": "GRO-1958" }

// Engine signal
{ "type": "golden_flow_completed", "issue_id": "GRO-1958", "pr_url": "...", "jules_verdict": "approved" }
```

UI, cron, dashboards, and the command center all consume from this stream. v0 does not need a fancy UI — it just needs the stream and a way to print the events from Telegram.

---

## State objects (v0 minimum)

```text
GoldenFlowRun
  id: str
  issue_id: str            # Linear identifier
  capability: "agy" | "jules" | "vcs"
  status: started | running | paused | completed | killed | failed
  session_id: str
  workspace: str           # local path
  pr_url: str
  jules_review_id: str
  events: [GoldenFlowEvent]
  started_at: ts
  ended_at: ts
```

Storage can be a flat JSON file under `~/.local/share/prismatic/golden_flow/<id>.json` for v0. SQLite/Postgres is plugin territory.

---

## Telegram command surface (v0 minimum)

User-facing commands, in this exact form:

```text
/agy start GRO-1958
/agy status
/agy pause
/agy summarize
/agy resume
/agy kill
/agy where          # what is AGY working on right now
/agy artifact N     # open artifact N from the most recent run
```

That is the entire v0 surface. No aliases, no flags, no multi-agent routing UI. One AGY session at a time.

---

## What is real vs mocked in v0

| Component | v0 status | Notes |
|---|---|---|
| Linear read/comment/state transition | Real | Uses `LINEAR_API_KEY` directly |
| GitHub branch + PR + comment | Real | Uses `GITHUB_TOKEN` or `gh` adapter |
| AGY session start/kill/stream | Real | `agy --print --sandbox`, normalized event capture |
| AGY workspace (ephemeral clone) | Real | shallow git clone of the issue’s repo |
| Jules review handoff | Real | `jules.google.com` API if exposed; otherwise queued task in `agent:jules` lane |
| Schedule recording | Real | Engine writes normalized schedule record |
| Telegram gateway | Real but minimal | Only the 7 commands above |
| Schedule Observatory UI | Mocked | Not built; events recorded only |
| AGY Chat TUI parity | Mocked | “Chat” is just command/result, not a full chat session |
| Command center UI | Not built | Engine emits events; UI is a later plugin |
| SovereignSentinel | Not built | Out of scope |
| Multi-user / multi-tenant | Mocked | Single user, single Telegram chat |

If something is in the “real” column, it must be tested end-to-end before the v0 demo is considered done. Mocked is acceptable as long as the engine contract around it is real.

---

## Acceptance test (definition of v0 done)

The golden flow is **DONE** when all of the following can be demonstrated on a fresh clone of `prismatic-engine` with `LINEAR_API_KEY`, `GITHUB_TOKEN`, `AGY_TOKEN`, and `TELEGRAM_BOT_TOKEN` in env:

1. Run `prismatic init` → engine installs with no warnings.
2. Run `prismatic doctor` → reports `linear:ok`, `vcs.github:ok`, `agy:ok`, `jules:ok`, `telegram:ok`.
3. Send `/agy start GRO-1958` from Telegram.
4. Receive a Telegram message within 30 seconds: `AGY started on GRO-1958 (session …)`.
5. AGY creates a branch `feature/golden-flow-1958` and opens a PR.
6. Telegram receives `PR opened: <url>` event.
7. Jules review is dispatched automatically; verdict lands in Telegram within the configured SLA.
8. Linear issue GRO-1958 receives a final comment with PR link + Jules verdict, and state transitions to `Done`.
9. Schedule provider records a `task_started` and `task_ended` event for GRO-1958.
10. `~/.local/share/prismatic/golden_flow/<id>.json` exists and contains the full event stream.
11. `/agy summarize` returns a 1-paragraph summary with three bullet links (PR, Jules verdict, artifact).
12. A repeat run on the same issue is idempotent (does not create duplicate PRs or sessions).

If any of those fail, v0 is not done.

---

## Explicitly out of scope (v0)

- Multi-AGY parallel sessions
- TUI chat parity
- Schedule Observatory visualization
- Plugin marketplace
- Linear clone
- SovereignSentinel
- Custom auth / multi-user
- Mobile native app
- Public REST API
- “Cherry on top” features (theming, animations, dashboards)
- Anything not required to pass the 12-step acceptance test

If a feature is proposed during v0, the response is:

> Does this improve the 12-step acceptance test? If no, defer.

---

## Architecture constraints (carry into implementation)

1. **Engine vs harness separation:** all event/schema/storage code lives in `prismatic-engine`. No Hermes imports in the kernel.
2. **Capability registration:** each provider (linear, agy, jules, vcs.github, telegram, schedule, artifact) registers a contract. No provider code calls another provider directly — they emit events on the bus.
3. **No cross-lane files:** AGY works on a `feature/` branch from `deploy-fresh`. Fred merges to staging.
4. **No AGY timeout traps:** use the proven dispatch pattern (real `--add-dir` workspace, real `--model`, `--print-timeout 24h0m0s`, `subprocess.run(timeout=None)`).
5. **Idempotent runs:** repeat invocations on the same issue must not duplicate branches/PRs/sessions.
6. **Artifact everything:** every event with a useful file/link becomes a `prismatic-publish` artifact for click-through from Telegram.

---

## Suggested task list for AGY (queued as GRO-1959, with children GRO-1960..1966)

1. GRO-1960 — Add capability registry skeleton (`prismatic/capabilities/`)
2. GRO-1961 — Add normalized event bus (`prismatic/event_bus.py`)
3. GRO-1962 — Wire `linear` capability (read issue, comment, state transition, observe)
4. [x] GRO-1963 — Wire `vcs.github` capability (branch, PR, comment, status)
5. GRO-1964 — Wire `agy` capability (start, stream, pause, kill, summarize)
6. GRO-1965 — Wire `jules` capability (handoff, poll verdict)
7. GRO-1966 — Wire `telegram` and `schedule` capability + golden flow runner
8. GRO-1967 — Acceptance test runner that proves the 12 steps pass

Each child issue must:

- Branch from `deploy-fresh` with the proper prefix (`feature/` for AGY).
- Land in its own PR.
- Include a unit test for the new module.
- Update the `prismatic-golden-flow-v0.md` to check off the acceptance step(s) it enables.
- Be idempotent and re-runnable.

---

## Open questions for Michael (must be answered before AGY starts coding)

1. Which repo should AGY use as the “target repo” for the demo? `prismatic-engine` itself, or a sandbox repo?
2. Should the v0 Telegram bot be the existing `Fred` bot or a new `prismatic-demo` bot?
3. Do you want AGY to run in `--sandbox` mode (recommended) or full mode for the demo?
4. Should the AGY session be 1-3 concurrent (organic ceiling) or strictly 1 for the demo?
5. When Jules review verdict is `changes_requested`, do we want AGY to auto-fix (good for demo) or hand back to user (cleaner)?

---

## Guiding rule (v0 mantra)

> One flow. Seven capabilities. Twelve acceptance steps. Zero scope creep.
