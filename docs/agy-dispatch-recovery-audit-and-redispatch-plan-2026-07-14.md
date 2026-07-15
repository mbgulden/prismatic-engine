# AGY Dispatch Recovery Audit and Safe Redispatch Plan

**Date:** 2026-07-14  
**Prepared by:** Kai  
**Audience:** Fred / Orchestrator / Dispatch-maintenance agent  
**Scope:** Prismatic Engine AGY dispatch failure affecting rubric-to-10/10, cohesive app surface, and RC1 Portable App Readiness tasks  

## Executive summary

The AGY task tree did not fail because AGY reasoned poorly. AGY did not get a chance to reason.

The dispatch system launched AGY with an invalid model alias:

```text
gemini-3.5-flash-high
```

The current AGY CLI rejected that model before reading the tasks:

```text
Error: invalid --model "gemini-3.5-flash-high":
model gemini-3.5-flash-high is not recognized as a known model or custom model in settings

Available models:
  Gemini 3.5 Flash (Medium)
  Gemini 3.5 Flash (High)
  Gemini 3.5 Flash (Low)
  Gemini 3.1 Pro (Low)
  Gemini 3.1 Pro (High)
  Claude Sonnet 4.6 (Thinking)
  Claude Opus 4.6 (Thinking)
  GPT-OSS 120B (Medium)

dispatch.tokens.actual_input=0
dispatch.tokens.actual_output=0
```

So this was primarily a **dispatch/config failure**, with secondary **dependency scheduling** and **sandbox guard/template** failures.

## Verified facts from the failure review

| Evidence | Result |
|---|---|
| Task range inspected | `GRO-3836` through `GRO-3936` |
| Issues counted | 101 |
| Linear state observed | 101 / 101 in `In Progress` |
| Sandboxes observed | 101 / 101 exist under `/archive/agy_sandboxes` |
| `RESULT.md` files | 101 / 101 exist |
| Real task result | 101 / 101 are auto-generated `RESULT — ABANDONED` reports |
| AGY token input | `0` |
| AGY token output | `0` |
| Sampled sandbox HEAD | `a8003e5`, current-ish Prismatic repo content |
| Main culprit | stale AGY dispatcher/supervisor config, not AGY competence |

## Root-cause ranking

| Rank | Cause | Confidence | Notes |
|---:|---|---:|---|
| 1 | Invalid AGY model alias in dispatcher | Very high | Logs explicitly show invalid `--model`; token input/output were `0` |
| 2 | Bulk dispatch ignored dependency staging | High | Rubric, supplemental, cohesive, and RC tasks were all launched as a flat pool |
| 3 | Sandbox guard/template mismatch | High | Old guard forbids clone/install/test/build commands required by audit/release tasks |
| 4 | Abandonment guard masked the actual failure | Medium | It wrote `RESULT.md`, but only as abandonment diagnostics |
| 5 | Old Prismatic app code | Low | Sampled sandboxes were at current-ish `a8003e5`; stale assumptions were in dispatch/supervisor config |

## What must happen before redispatch

### P0 — Fix AGY model mapping

Find where the AGY dispatcher/supervisor config uses:

```text
gemini-3.5-flash-high
```

Replace it with the current accepted display-style model name:

```text
Gemini 3.5 Flash (High)
```

Better: implement model validation by querying or parsing available AGY models before launching real work.

**Acceptance:**

- One tiny AGY dry run starts successfully.
- Logs no longer show `invalid --model`.
- `dispatch.tokens.actual_input > 0`.
- `dispatch.tokens.actual_output > 0`.

### P0 — Add preflight before any batch launch

Before dispatching more than one task, verify:

- AGY binary exists and is executable.
- Configured model is valid.
- Sandbox root is writable.
- Warm cache repo is accessible and fresh.
- Linear API can read/update the target issue.
- A sandbox can write `RESULT.md`.
- Self-review script exists:

```text
~/.hermes/profiles/orchestrator/scripts/agy_self_review.py
```

**Acceptance:** a bad model/config aborts the batch before task state changes.

### P0 — Reset failed Linear tasks safely

For affected tasks, inspect each issue/sandbox. If the only result is `RESULT — ABANDONED` and the log shows invalid model / zero tokens:

- Move state from `In Progress` back to `Todo`.
- Remove `dispatch:paused` if caused only by this failure.
- Remove `agent:needs-human-review` if caused only by this failure.
- Keep `agent:agy` and project/pipeline labels.
- Add a Linear comment:

```text
Reset for redispatch. Prior AGY launch failed before execution because dispatcher used invalid model alias `gemini-3.5-flash-high`; AGY received 0 input tokens and produced 0 output tokens. No task work was performed. Safe to redispatch after model/preflight/scheduling fixes.
```

### P0 — Do not relaunch all 101 tasks

This is the most important operational rule.

The task tree is staged:

```text
scorecard baseline
→ initial rubric 10/10 execution
→ supplemental audit coverage
→ cohesive app surface integration
→ RC1 Portable App Readiness audit
→ blocker fixes / release proof
```

Do not treat everything with `agent:agy + dispatch:ready` as runnable at once.

## Safe redispatch sequence

### Stage 1 — Scorecard baseline only

Start with:

- [GRO-3837](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3837) — inventory rubric items and scoring rules
- [GRO-3838](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3838) — run current PE baseline and assign scores with evidence
- [GRO-3839](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3839) — create 10/10 closure ledger and missing-evidence queue

Do not dispatch downstream work until this trio produces evidence.

### Stage 2 — Initial 10/10 execution tree

Only after Stage 1:

- [GRO-3836](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3836)
- [GRO-3840](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3840)
- [GRO-3844](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3844)
- [GRO-3848](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3848)
- [GRO-3852](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3852)
- [GRO-3857](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3857)

### Stage 3 — Supplemental audit coverage

Only after Stage 1 mapping proves coverage gaps:

- [GRO-3861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3861)
- [GRO-3866](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3866)
- [GRO-3871](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3871)

### Stage 4 — Cohesive app surface integration

Only after enough pieces are actually implemented or intentionally deferred:

- [GRO-3888](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3888)

Goal sequence:

```text
install/run
→ dashboard home/readiness
→ plugin catalog
→ governance/policy
→ create/start job
→ approval
→ artifact/provenance
→ audit history
→ export/publish
→ safe disconnect
→ evidence/report
```

### Stage 5 — RC1 Portable App Readiness

Only after cohesive app surface proof:

- [GRO-3900](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3900)
- [GRO-3908](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3908)
- [GRO-3915](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3915)
- [GRO-3923](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3923)
- [GRO-3930](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3930)

Goal sequence:

```text
freeze RC scope
→ run Portable App Readiness Audit
→ classify P0/P1/P2/P3/won't-fix
→ fix release blockers
→ rerun audit
→ cut versioned developer preview
```

## Task-type-aware sandbox guardrails

The current generic guard is too blunt for audit/release tasks:

```text
Do NOT:
  * launch pytest / lighthouse / axe / npm / pnpm / pip / cargo / go test
  * run git fetch / git clone
```

That conflicts with RC1 audit/release work.

Replace the generic guard with task-type rules:

| Task type | Allowed |
|---|---|
| Research / mapping | Read files, inspect docs, produce report; no long tests/builds needed |
| Audit | May run bounded smoke/check scripts and inspect command output |
| Implementation | May edit files and run focused tests/lint |
| Release proof | May run clean checkout, install, build/package, smoke tests, release checks |
| Visual/dashboard QA | May run browser/static visual checks and capture screenshots |

Use timeouts instead of blanket bans.

## Required redispatch proof

Before claiming dispatch recovered, run **one task only**:

- [GRO-3837](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3837)

Expected proof:

- AGY launches with valid model.
- AGY reads task.
- AGY writes real `RESULT.md`.
- AGY runs self-review.
- AGY outputs final line:

```text
DONE: GRO-3837 ...
```

- Linear receives a meaningful comment.
- Task moves to correct review/next state.
- `dispatch.tokens.actual_input > 0`.
- `dispatch.tokens.actual_output > 0`.

Only after that should Stage 1 continue.

## Recommended next audit

Yes, after Fred finishes the dashboard branch integration, run a new audit. It should not be another broad, vibes-style audit. It should be a focused **Readiness-to-Run Ingestion Queue audit** with these gates:

| Gate | Question |
|---|---|
| Dashboard route | Does protected `/dashboard` serve the governance dashboard, not marketing HTML? |
| Dashboard tabs | Are all tabs visible and wired without console errors? |
| Ingestion queue | Does the queue tab read durable queue state, not just static/mock data? |
| Drain path | Can the dashboard trigger a bounded queue drain safely? |
| Dispatcher | Does dispatch preflight verify model/config before moving tasks? |
| AGY model | Does a one-task AGY dry run consume and emit tokens? |
| Recovery | Are failed/stale/dead-letter events visible and safely replayable? |
| Plugin lifecycle | Do plugin jobs/artifacts/policy/audit events still work? |
| PWP reference | Does PWP lifecycle demo still prove connect → job → artifact → approval → safe disconnect? |
| Public/release proof | Do public launch, security, release, and dashboard visual checks pass? |

The output should be a scorecard with blockers, not a loose narrative.

## Best way forward

1. Let Fred finish the dashboard integration lift, but do not let him discard the main-only proof layer.
2. Give Fred the dashboard preservation report:
   - `docs/fred-dashboard-integration-preservation-report-2026-07-14.md`
3. Give Fred this dispatch recovery audit:
   - `docs/agy-dispatch-recovery-audit-and-redispatch-plan-2026-07-14.md`
4. Create one integration branch from `deploy-fresh`, not a destructive reset.
5. Port missing `origin/main` docs/scripts/tests into that branch.
6. Upgrade the Ingestion Queue tab from compatibility status to durable queue/drain control.
7. Fix AGY dispatcher model/preflight/staging before any redispatch.
8. Run one AGY task only: [GRO-3837](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3837).
9. If that passes, continue Stage 1; do not jump to RC1.
10. After dashboard + dispatch are stable, run the focused Readiness-to-Run Ingestion Queue audit.

## Final expected marker

Do not claim this program is ready until Fred can honestly produce:

```text
DASHBOARD_DISPATCH_INGESTION_READY_OK
```

That marker should mean:

- dashboard branch integrated without throwing away main proof assets,
- ingestion queue can be operated safely from dashboard,
- AGY dispatcher preflight works,
- one-task AGY redispatch proof passed,
- public/plugin/release verification has real output.
