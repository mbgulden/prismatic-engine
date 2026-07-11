# Dispatch Rollout and Rollback Gates

**Issue:** GRO-3501  
**Owner lane:** Ned / infrastructure  
**Scope:** Any change that alters Prismatic Engine ingestion, queueing, routing, dispatcher selection, agent launch, state sync, or retry behavior.

This runbook defines the stop/go criteria for deploying new dispatch or ingestion behavior. It is intentionally mechanical. If an operator cannot answer a gate from live evidence, the gate is **red** and the rollout stops. Servers are good at ambiguity only when they are down.

## 1. Rollout modes

Use the smallest mode that proves the change.

| Mode | Use when | Blast radius | Required before advancing |
|---|---|---:|---|
| `dry-run` | New classifier, label routing, queue filtering, or state-sync logic | None; logs only | Evidence shows expected decisions for representative real issues/events |
| `shadow` | New dispatch path can observe the live feed without launching agents | Read-only side effects only | Shadow decisions match the current production path or all deltas are explained |
| `canary` | One lane, one label, or a fixed small issue set can exercise the path | Bounded to canary cohort | Canary passes all rollout gates for two consecutive checks |
| `ramp` | Canary is clean and the change needs broader coverage | Increased but reversible | Ramp has no rollback triggers and no unexplained queue drift |
| `full` | All gates have passed and rollback artifacts are ready | Production | Post-rollout watch remains green through the observation window |

Default progression: `dry-run -> shadow -> canary -> ramp -> full`. Skipping a mode requires a written comment on the Linear issue explaining why the skipped mode provides no additional safety.

## 2. Pre-rollout GO gates

All gates below must be green before enabling behavior that can launch an agent or mutate Linear state.

| Gate | GO condition | STOP condition | Evidence to capture |
|---|---|---|---|
| Change scope | Change has a named issue, owner, affected paths, and rollback owner | Unknown owner, broad “cleanup” diff, or unrelated files in the commit | Linear issue ID, branch name, `git diff --name-only` |
| Lane safety | Change only touches allowed lanes for the implementing agent | Touches another agent's lane without explicit handoff | File list and lock list |
| Dispatch contract | Each event outcome is one of: `dispatch`, `queue`, `drop`, `defer`, or `escalate` | Any event path can silently no-op | Contract table or test fixture proving every branch |
| Idempotency | Replaying the same event does not launch duplicate agents or duplicate state transitions | Dedup key missing or replay changes state twice | Replay test or dry-run log with repeated event ID |
| Queue health baseline | Current pending queue, oldest event age, and consumer/curator status are known | Baseline unavailable or already degraded beyond warning threshold | Queue depth, oldest age, service status |
| Rate budget | Expected Linear/API call volume fits the active budget with margin | Budget impact unknown or expected to exceed configured budget | Estimated calls/hour and current budget reading |
| Telemetry | Launch, skip, error, and state-sync outcomes emit observable records | Rollout would make success/failure invisible | Log/event/table names and sample record |
| Rollback readiness | Previous version/flag/config and exact rollback command are known | No reversible switch, no previous artifact, or rollback untested | Command, artifact/version, and who can run it |
| Human decision boundary | Conditions needing Michael are explicit before rollout | Operator must improvise during incident | “Ask Michael if …” list or `none` |

If any STOP condition is true, do not deploy. Move the Linear issue back to Todo or comment the blocker with exact evidence.

## 3. Canary GO gates

A canary may start only after the pre-rollout gates pass. During canary, keep the cohort tiny: one lane, one label, or a fixed issue list. The canary advances only when all of these hold:

1. **No duplicate dispatch:** repeated webhook/poller input launches at most one worker per dedup key.
2. **No silent drop:** every input event is accounted for in logs or queue state.
3. **No wrong-lane launch:** sampled canary launches match the intended agent label and lane mapping.
4. **No unexpected Linear state jump:** label-only changes do not move issues between workflow states unless the rollout explicitly owns that transition.
5. **Queue drains:** pending queue count returns to baseline or lower after the canary cycle.
6. **Oldest age stable:** oldest unprocessed event age does not increase across two checks.
7. **Error budget clean:** no new uncaught exception, retry storm, or repeated timeout in the canary path.
8. **Operator repeatability:** a second operator/pass can run the same check commands and get the same verdict from current state.

Minimum canary observation window: two consecutive checks separated by at least one normal dispatcher interval. If the normal interval is unknown, use 15 minutes.

## 4. Ramp gates

Ramp in bounded steps. Recommended sequence:

1. `1 lane / 1 label`
2. `all labels in 1 lane`
3. `all lanes except known noisy lanes`
4. `full production`

Each step must pass the canary GO gates before the next step. Do not ramp while a previous step has pending retries, unprocessed queue growth, or unexplained state drift.

## 5. Rollback triggers

Rollback immediately when any hard trigger fires. Do not “watch it for a bit.” That is how small dispatch bugs become a comment landfill.

### Hard rollback triggers

| Trigger | Condition | Immediate action |
|---|---|---|
| Duplicate launches | Same issue/event launches more than one active worker inside the dedup TTL | Disable new path or revert flag; keep queue intact for replay |
| Wrong-lane dispatch | Event launches an agent outside the expected lane map | Disable new path; comment affected issue(s); restore labels/state if mutated |
| Silent drop | Event disappears from queue without `dispatch`, `drop`, `defer`, or `escalate` evidence | Disable consumer path; preserve DB/logs; replay from last known good cursor |
| Queue runaway | Pending queue grows for two checks or oldest event age exceeds the warning threshold | Pause ingestion-to-dispatch bridge; keep ingestion writing durable queue |
| Retry storm | Same event retries more than the configured retry cap or produces repeated identical failures | Disable retry loop; mark event deferred/escalated once |
| State corruption | Linear issue state changes without an allowed transition reason | Stop dispatch; revert state/labels for affected issues; file incident note |
| Budget breach | Linear/API budget reaches stop threshold or request rate exceeds expected rollout budget | Disable rollout; leave safety-net queue enabled |
| Telemetry blind spot | Required launch/skip/error records stop appearing during rollout | Roll back; a blind dispatcher is not production software |
| Human-decision boundary crossed | Rollout needs credentials, production reboot, billing-risk action, or policy decision | Stop and ask Michael with concrete options |

### Soft rollback triggers

Soft triggers stop ramping and require investigation. Roll back if they persist for two checks:

- Queue depth above baseline but not yet runaway.
- Oldest event age increasing slowly.
- Canary deltas that are explainable but not yet encoded as tests.
- Non-critical telemetry lag.
- Manual cleanup needed for any issue in the canary cohort.

## 6. Rollback procedure

Use this order so rollback is safe to repeat:

1. **Freeze forward motion.** Disable the new flag/config/path or stop ramping. Do not delete queued events.
2. **Capture evidence.** Record current commit, flag/config value, queue depth, oldest event age, affected issue IDs, and the first failing log line.
3. **Restore previous behavior.** Revert the feature flag/config, reset to the previous deployment artifact, or revert the commit. Prefer a switch over code surgery.
4. **Re-run health checks.** Verify gateway/consumer/curator are active, queue writes continue, and no new workers launch from the disabled path.
5. **Replay safely.** Replay only from the last known good cursor/dedup boundary. Replays must preserve dedup keys.
6. **Repair external state.** For any affected Linear issue, restore labels/state explicitly and comment once with the correction.
7. **Write the incident note.** Include root trigger, rollback command, affected cohort, and remaining follow-up.

Rollback is complete only when the queue is no longer growing, no duplicate workers are active, and affected Linear state is either restored or explicitly handed off.

## 7. Repeatable operator checklist

Copy this block into the Linear issue or incident note for each rollout.

```markdown
## Dispatch rollout gate — GRO-XXXX

Mode: dry-run | shadow | canary | ramp | full
Change: <branch/commit/config>
Cohort: <lane/label/issues>
Rollback command/config: <exact command or flag>

Pre-rollout gates:
- [ ] Scope known
- [ ] Lane safety verified
- [ ] Dispatch contract covers dispatch/queue/drop/defer/escalate
- [ ] Idempotency/replay checked
- [ ] Queue baseline captured
- [ ] Rate budget checked
- [ ] Telemetry sample visible
- [ ] Rollback artifact/command ready
- [ ] Human decision boundary documented

Canary/ramp checks:
- [ ] No duplicate dispatch
- [ ] No silent drop
- [ ] No wrong-lane launch
- [ ] No unexpected Linear state jump
- [ ] Queue drains or remains at baseline
- [ ] Oldest event age stable
- [ ] No retry storm or uncaught exception
- [ ] Check can be repeated from current state

Verdict: GO | STOP | ROLLBACK
Evidence links/logs:
- <path or command output>
```

## 8. Safe-to-repeat rules

- Every rollout decision must be based on current state, not memory from a prior pass.
- Re-running the checklist must not mutate state except for explicitly named smoke/canary events.
- Rollback commands must be idempotent: running them twice leaves the system in the same safe state.
- Replay must start from a recorded cursor/dedup boundary, never from “about where it failed.”
- Linear comments should summarize each rollout/rollback once; repeated checks belong in local logs unless the verdict changes.
- If the operator cannot prove whether an event was dispatched, queued, dropped, deferred, or escalated, the correct verdict is `STOP`.

## 9. Definition of done for dispatch-change rollout plans

A rollout plan is acceptable when it names:

1. The mode progression and cohort for each step.
2. The explicit GO gates.
3. The explicit STOP/rollback triggers.
4. The exact rollback switch/command/artifact.
5. The evidence that makes the process repeatable by another pass.

Anything less is a plan-shaped outage waiting for a calendar invite.
