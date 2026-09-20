# GRO-3471 — Prismatic Engine Ingestion/Dispatch Reliability Program

## Purpose

This program binds the Prismatic Engine repair work into one end-to-end reliability track: live issue intake, queueing, lane routing, worker execution, artifact publication, Linear state sync, and rollout safety.

The program is complete only when the path can be demonstrated from a real Linear issue to a real worker run and back to Linear with evidence, and when silent stalls can be attributed to a named layer instead of treated as generic queue fog.

## Program definition of done

- All six phase epics exist and are scoped as phase-level deliverables.
- Every phase has explicit success criteria and non-goals.
- The live path demonstrates: Linear intake → durable queue → lane dispatch → worker/session proof → artifact/result proof → Linear comment/state update.
- Silent stalls are detectable and attributable to one of: intake, queue, routing, launch, execution, artifact, state sync, label debt, or rollout guardrail.
- Label debt and stale review noise are measured, reduced, and tracked as queue hygiene work instead of hidden in execution counts.

## Phase map

| Phase | Linear epic | Reliability layer | Success criteria | Non-goals |
| --- | --- | --- | --- | --- |
| 1 | GRO-3472 — Observability & Failure Taxonomy | Cross-system visibility | One health view answers “what is broken?” in under one minute; every critical subsystem has a status; failure classes are actionable. | Replacing subsystem logs; building a full BI dashboard; declaring idle queues broken without lane contract context. |
| 2 | GRO-3473 — Ingestion Reliability & Idempotent Queue | Intake, durability, replay safety | Consumer/curator stay up through an observation window; duplicate event reprocessing has no duplicate side effects; failed events remain visible and recoverable. | Rewriting Linear integration wholesale; adding new business workflow semantics; hiding failures by dropping events. |
| 3 | GRO-3474 — Lane Routing & Dispatch Contract | Dispatch eligibility and lane ownership | Each active lane has a written contract; `agent:*` labels are not ambiguous; review-only work cannot leak into execution queues; starvation is explicit. | Moving content/design work into Ned’s lane; using label-only routing as the contract; solving all future team taxonomy problems. |
| 4 | GRO-3475 — Execution Proof & State Sync | Worker launch, artifacts, Linear updates | Every dispatched issue has run proof, a result artifact, and a Linear evidence update; state/label transitions match the workflow contract; at least one proof exists per active lane. | Counting queue entries as execution; fabricating artifacts; requiring humans to manually reconcile routine state sync. |
| 5 | GRO-3476 — Label Debt Cleanup & Queue Hygiene | Backlog truth and stale-noise reduction | Legitimate active work is dispatchable; duplicates/stale review issues are parked or canceled; queue counts represent production work; label debt is measurable. | Closing valid work for neat metrics; relabeling without evidence; treating queue hygiene as a substitute for worker execution. |
| 6 | GRO-3477 — Guardrails, Replay & Rollout Safety | Regression prevention and recovery | Forced failures are detected quickly; replay/backfill works after outage or corruption; smoke tests exercise the live path; rollout has stop/go gates. | Rebooting production services without approval; bypassing branch/lock discipline; replacing phase-level implementation with one giant script. |

## End-to-end proof contract

A completed proof must include these evidence points in order:

1. **Intake:** Linear issue identifier, labels, state, and scanner/router observation.
2. **Queue:** durable queue or ledger entry with idempotency marker.
3. **Routing:** lane contract decision explaining why the issue is launchable or parked.
4. **Launch:** worker/session/process identifier or equivalent proof that execution actually started.
5. **Artifact:** committed code, report, log, result file, PR, or other reviewable output.
6. **State sync:** Linear comment and state/label transition evidence.
7. **Health attribution:** if any step stalls, the health view names the failed layer and the operator action.

## Silent-stall attribution model

| Layer | Stall symptom | Required attribution signal |
| --- | --- | --- |
| Intake | Linear work exists but no scanner/router observation appears | Last successful Linear poll/webhook, auth state, and query/filter result count. |
| Queue | Scanner sees work but no durable entry advances | Queue depth, oldest item age, dedupe key, and dead-letter count. |
| Routing | Queue entry exists but no lane claims it | Lane contract result, missing labels/states, or explicit starvation signal. |
| Launch | Lane claims work but no worker starts | Supervisor attempt record, command/adapter error, and retry budget. |
| Execution | Worker starts but produces no result | Session heartbeat, last log line, timeout class, and lock state. |
| Artifact | Worker completes but evidence is missing | Expected artifact path/PR/comment target and publication error. |
| State sync | Artifact exists but Linear remains stale | GraphQL mutation result, state transition target, and retry/dead-letter record. |
| Label debt | Counts are high but launchable work is low | Labeled vs dispatchable counts, stale duplicate count, and review-only quarantine count. |
| Rollout | New guardrail blocks or silently regresses the path | Smoke-test result, feature flag/rollback state, and stop/go decision. |

## Operating notes

- Executor-owned implementation stays in `scripts/`, `prismatic/`, `plugins/`, and repo documentation/specs.
- Client repositories and staging mirrors remain out of lane.
- Linear issue comments remain the source for lane-dequeue decisions; scanner output is the dispatch gate for scheduled cron pickup.
- Finalization must ensure atomic lock release, Linear transition, and evidence recording.
