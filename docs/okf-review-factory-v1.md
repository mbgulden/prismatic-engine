# Prismatic Engine Review/Merge Factory V1 — Comprehensive OKF

**Status:** CANONICAL SPEC DRAFT — pending Michael sign-off
**Author:** Fred (orchestrator profile), 2026-07-31
**Trigger:** Michael's review-merge-factory plan + Fred's 10-question analysis + cross-cutting additions

This document is the **canonical reference** for the Review/Merge Factory V1 build. It is intended to be the basis for breaking into Linear epics and child tasks after sign-off.

---

## 0. Decision (verbatim from Michael's plan)

> Stop using George as the synchronous reviewer and release clerk for every candidate. Build a durable, event-driven review factory in PE. Keep the cap on writers/producers, not on read-only reviewers.
>
> George becomes the policy/exception owner. AGY and Jules become ordinary review workers using the same versioned review contract. Deterministic checks run before an LLM reviewer is assigned.

## 0.1 Michael's clarifications (2026-07-31)

1. **Build owner for the whole factory: Antigravity 2.0 on Michael's laptop.** All RF-0 through RF-6 work is consolidated there so AGY has full visibility. *Implication:* the canonical source-of-truth for the build may not be `/home/ubuntu/work/prismatic-engine/` initially; a parallel branch lives on Michael's machine. **Coordination protocol:** Fred posts OKF updates to `state/okf-review-factory-v1.md`; Michael mirrors decisions back to Antigravity. PRs target the same canonical `prismatic-engine` repo when they ship.
2. **Merge authorization must NOT require Michael in the loop.** The factory should fully close that gap. Standing policy + versioned approver credentials replace per-merge Michael-click approvals for Tier 0/1. Tier 2/3 still hit a human (George), but the human is **policy-mediated, not the bottleneck**.
3. **All 10 of Fred's recommendations accepted.** (Q1 lane split becomes moot since Michael owns the whole build via Antigravity; the rest are in.)
4. **All 4 cross-cutting items (A/B/C/D) accepted.**

---

## 1. Problem statement

The current path serializes 9 distinct steps through one Telegram agent (George):

1. Discovery
2. Artifact binding
3. Test selection
4. Test execution
5. Adversarial review
6. Proof formatting
7. Publication preparation
8. Merge verification
9. Deployment verification
10. Next-task admission

That preserves evidence but does not scale. It also repeatedly rebuilds one-shot scripts and proof packets instead of projecting one canonical run record.

**Existing PE pieces that the factory must reuse (not duplicate):**

| Piece | Path | Lines (approx) | Reuse plan |
|---|---|---|---|
| Durable completed-work intake | `prismatic/agy_completed_work.py` | 1658 | RF-1 ingestion source |
| Canonical AGY run state machine | `prismatic/agy_completed_work.py` | (above) | Terminal producer result → review_pending transition |
| Exact commit/tree/result receipt binding | (existing primitives) | — | RF-1 receipt schema baseline |
| Immutable archive reproduction | (existing) | — | RF-2 verifier foundation |
| Provider-neutral verification receipts | `prismatic/verifiers/` (schemas.py, registry.py) | — | RF-2 receipt consumer |
| Event-driven dashboard/gateway control | `prismatic/gateway/` | — | RF-5 dashboard surface |
| Existing PR reviewer code | `prismatic/review/` (pr_reviewer.py, pipeline.py, registry.py) | — | **Wrap as preliminary heuristic; do NOT replace** (cross-cutting A) |

---

## 2. Target flow

```
Ned result packet
  -> completed_work row (agy_completed_work intake)
  -> deterministic intake validation
  -> risk classification (versioned policy file) + test-plan resolution
  -> immutable verification job (deterministic worker, parallel-safe checks)
  -> reviewer queue (lease exactly one)
  -> AGY/Jules exact-artifact review (portable capability pack)
  -> merge-ready queue (with witness if Tier 2+)
  -> policy-authorized merge executor (no human click for Tier 0/1)
  -> exact merge-tree verification (one-shot merge, idempotency key)
  -> optional deployment queue (separate authorization, NOT in V1)
```

**No Telegram polling. No George hand-crafted verifier for normal candidates.** Dashboard shows queue depth, age, current worker, receipts, blockers, and authorization state.

---

## 3. Parallelism policy

- **Writer/producer cap:** 1 (unchanged until PE policy changes)
- **Read-only immutable reviewers:** concurrent cap **3** initially
- **Witness requirement:** primary + 1 independent witness for Tier 2+ paths OR an explicit policy rule
- **George touch rate:** **only exceptions** — conflicting verdicts, production/runtime changes, security boundary changes, migrations, release activation, policy changes
- **Default for ordinary candidates:** ONE reviewer, NO witness, NO George

**Lease-duration policy (Fred Q4):**

| Tier | Lease duration | First expiry | Second expiry |
|---|---|---|---|
| 0 | 2 minutes | auto re-queue | escalate to repair_required |
| 1 | 15 minutes | auto re-queue | escalate to repair_required |
| 2 | 30 minutes | auto re-queue (witness slot offered to alternate) | escalate to repair_required |
| 3 | N/A | (Tier 3 does not enter reviewer queue) | (handled by human authorization gate) |

**Lease deadlock prevention:** if 3 reviewers are all expired on different candidates, the 4th candidate's lease offer goes to a held queue with `wait_reason: reviewer_pool_exhausted`. The dashboard surfaces this as a "reviewer availability" finding, NOT silent retry.

---

## 4. Risk tiers (verbatim + classification rule from Fred Q3)

### Tier 0 — deterministic only
Docs, fixtures, generated metadata, or allowlisted non-runtime changes. Requires exact-head binding, diff check, configured lint/tests, and receipt validation. Human/LLM review is sampled or policy-triggered.

### Tier 1 — ordinary source
Normal isolated source/test changes. Requires deterministic gate plus one independent reviewer.

### Tier 2 — sensitive
Authentication, filesystem boundaries, Git mutation, process launch, admission/queue state, SQLite schema/migration, packaging, or dashboard truth adapters. Requires deterministic gate plus two independent reviewers or one specialist reviewer.

### Tier 3 — production authority
Deployment, systemd, credentials, destructive migration, merge policy, writer-cap changes, or control-plane authority. Always stops at an explicit authorization point. George reviews the exception; no automatic deploy.

### Classification mechanism (Fred Q3)

**Versioned policy file** at `prismatic/review_policy/v1.yaml`. Schema sketch:

```yaml
version: 1
rules:
  - id: tier-3-prod
    match_paths: ["deploy/**", "systemd/**", "**/credentials*", "**/secrets/**"]
    risk_tier: 3
    required_witnesses: 2
    notes: "Production authority changes"

  - id: tier-2-auth
    match_paths: ["prismatic/auth/**", "prismatic/migrations/**", "prismatic/**/sqlite*", "prismatic/gateway/auth*"]
    risk_tier: 2
    required_witnesses: 2
    notes: "Sensitive surfaces"

  - id: tier-2-git-mutation
    match_paths: ["prismatic/review/**", "prismatic/merge_executor*", "prismatic/policy/**"]
    risk_tier: 2
    required_witnesses: 2
    notes: "Git mutation + factory-self"

  - id: tier-0-docs
    match_paths: ["docs/**", "*.md", "fixtures/**", "**/test_data/**"]
    risk_tier: 0
    witness_required: false
    deterministic_only: true

  - id: tier-0-generated
    match_paths: ["**/generated/**", "**/*.pb.go", "**/schema_generated*"]
    risk_tier: 0
    witness_required: false
    deterministic_only: true

default:
  risk_tier: 1
  witness_required: false
```

**Fail-closed on policy miss:** if no rule matches and the default is Tier 1 but the verifier detects a high-risk pattern (auth, sqlite, prod, credentials), the verifier auto-escalates to Tier 2 and emits a `policy_miss_escalation` finding. The original classification is preserved in the audit trail.

---

## 5. Canonical records

### 5.1 `review_jobs`

| Field | Type | Notes |
|---|---|---|
| `review_job_id` | UUID v4 | Primary key |
| `completed_work_id` | UUID | FK to agy_completed_work row |
| `task_id` | str | Linear task identifier (e.g., GRO-4188) |
| `repository` | str | e.g., `mbgulden/prismatic-engine` |
| `base_commit` | str | SHA |
| `base_tree` | str | SHA |
| `candidate_commit` | str | SHA |
| `candidate_tree` | str | SHA |
| `result_packet_path` | str | filesystem path |
| `result_packet_sha256` | str | hex digest |
| `changed_paths_json` | JSON | list of changed paths |
| `risk_tier` | int | 0/1/2/3 — set by classifier |
| `policy_version` | str | e.g., `v1` |
| `state` | enum | queued, verifying, review_ready, reviewing, repair_required, rejected, merge_ready, merge_authorized, merging, merged, merge_verification_failed |
| `required_witnesses` | int | 0/1/2 |
| `completed_witnesses` | int | increments as witnesses complete |
| `created_at` | timestamp | UTC |
| `lease_owner` | str | reviewer capability version + agent ID |
| `lease_expires_at` | timestamp | UTC |

**State transitions are idempotent.** RF-1 specifies the transition table.

### 5.2 `verification_receipts`

| Field | Type | Notes |
|---|---|---|
| `receipt_id` | UUID | PK |
| `review_job_id` | UUID | FK |
| `candidate_commit` | str | bound exactly |
| `candidate_tree` | str | bound exactly |
| `immutable_archive_id` | str | identity of the fresh extraction |
| `commands` | JSON | list of commands run, with executable identity (sha256 of interpreter + script) |
| `exit_codes` | JSON | command → exit code |
| `log_paths` | JSON | command → log path |
| `log_sha256` | JSON | command → log digest |
| `changed_path_invariance_proof` | str | pointer to the side-effect proof |
| `classification` | enum | targeted, bounded_regression, canonical_suite, package_wheel, browser, production |
| `explicit_non_claims` | JSON | list of strings: e.g., `["did not run canonical suite", "did not run browser proof"]` |
| `baseline_failures` | JSON | list of failure identities (so baseline rot is never called canonical green) |
| `created_at` | timestamp | UTC |

### 5.3 `review_decisions`

| Field | Type | Notes |
|---|---|---|
| `decision_id` | UUID | PK |
| `review_job_id` | UUID | FK |
| `reviewer_id` | str | agent ID + capability version |
| `reviewer_capability_version` | str | e.g., `prismatic-review-worker==1.0.0` |
| `candidate_commit` | str | bound exactly |
| `candidate_tree` | str | bound exactly |
| `receipt_id` | UUID | FK |
| `verdict` | enum | clean, repair_required, rejected |
| `findings` | JSON | list of `{severity, path, line, invariant, reproduction_command}` |
| `idempotency_key` | str | sha256(reviewer_id + candidate_tree + verdict) |
| `created_at` | timestamp | UTC |

### 5.4 `merge_authorizations`

| Field | Type | Notes |
|---|---|---|
| `authorization_id` | UUID | PK |
| `review_job_id` | UUID | FK |
| `repository` | str | bound |
| `pr_number` | int | bound |
| `pr_head_commit` | str | bound — must equal reviewed candidate |
| `pr_base_commit` | str | bound — must equal reviewed base |
| `candidate_tree` | str | bound |
| `expected_merge_tree` | str | computed before mutation |
| `policy_version` | str | e.g., `v1` |
| `actor` | str | who authorized (user, capability, or "standing-policy: tier-N") |
| `scope` | enum | tier-0-auto, tier-1-auto, tier-2-exception, tier-3-exception |
| `expires_at` | timestamp | UTC — auto-revoke after |
| `consumed_at` | timestamp | UTC — set on merge execution |
| `idempotency_key` | str | sha256(authorization_id + merge_attempt_n) |

**Review completion alone NEVER implies merge authority.** These are separate records.

### 5.5 `repair_packets` (Fred Q5)

| Field | Type | Notes |
|---|---|---|
| `packet_id` | UUID | PK |
| `candidate_tree` | str | bound |
| `findings_json` | JSON | list of `review_decisions.findings` |
| `producer_id` | str | which agent produced (Ned/AGY/Jules) |
| `consumed_at` | timestamp | UTC, nullable |
| `resolution_attempt_n` | int | counter — prevent infinite loops |
| `created_at` | timestamp | UTC |

Producer consumes via existing agy_completed_work intake path. Avoids "bespoke manual review project" creation (matches the plan's explicit goal).

---

## 6. RF-0 — Spec freeze (prerequisite for RF-1)

**RF-0 is a documentation gate, not a code gate.** ~1 day of writing. No PR, no merge.

| Deliverable | Owner | Acceptance |
|---|---|---|
| Freeze agy_completed_work.py schema (or document current) | Ned (Antigravity) | Document in `prismatic/review_factory/spec/agy_completed_work_v1.md` |
| review_jobs schema (column types, indexes, constraints) | Ned | `prismatic/review_factory/spec/review_jobs_v1.md` |
| verification_receipts schema | Ned | `prismatic/review_factory/spec/verification_receipts_v1.md` |
| review_decisions schema | Ned | `prismatic/review_factory/spec/review_decisions_v1.md` |
| merge_authorizations schema | Ned | `prismatic/review_factory/spec/merge_authorizations_v1.md` |
| repair_packets schema (Fred Q5) | Fred (orchestrator) | `prismatic/review_factory/spec/repair_packets_v1.md` |
| Lease semantics (Fred Q4) | Ned | `prismatic/review_factory/spec/lease_semantics_v1.md` |
| Policy file format (Fred Q3) | Fred | `prismatic/review_factory/spec/policy_file_v1.yaml` |
| RF-1 → RF-6 hand-off contracts | Fred | `prismatic/review_factory/spec/handoff_contracts_v1.md` |
| Dashboard v1 cut (Fred Q7) | Fred | `prismatic/review_factory/spec/dashboard_v1_cut.md` |

**RF-0 acceptance marker:** `PE_REVIEW_FACTORY_SPEC_V1_OK` — all 10 documents checked in, signed off by:
- Ned (queue/verifier/merge ownership)
- Fred (capability/dashboard ownership)
- Michael (policy authorization)

**Why RF-0 before RF-1:** prevents RF-1 from being rebuilt when RF-3 ships with new contract requirements. Cheapest possible insurance.

---

## 7. Implementation slices

### RF-1 — queue and state machine

Add `review_jobs`, leases, idempotent transitions, API projection, and tests. Wire accepted completed-work rows into `queued` exactly once. **No worker execution or merge.**

**Owner:** Ned (Antigravity)
**Lane:** PE core
**Acceptance marker:** `PE_REVIEW_FACTORY_QUEUE_OK`

### RF-2 — deterministic verification worker

Immutable archive runner, policy-driven test plan, parallel-safe checks, durable receipt/logs, setup-vs-product classification, side-effect invariance tests.

**Owner:** Ned (Antigravity) — worker; Fred contributes check definitions for orchestrator-relevant surfaces
**Acceptance marker:** `PE_REVIEW_FACTORY_VERIFIER_OK`

### RF-3 — portable reviewer capability and worker

Install the same capability pack for AGY and Jules; consume `review_ready`; lease exactly one job; write idempotent exact-artifact decisions. **Include adversarial fixture candidates that must be blocked despite green tests** (cross-cutting B).

**Owner:** Fred (orchestrator)
**Lane:** portable capability
**Distribution:** pip-installable package `prismatic-review-worker` published to internal index (Fred Q6). Atomic install, version-pinned, no copy-paste drift.
**Acceptance marker:** `PE_REVIEW_FACTORY_AGY_REVIEWER_OK`

### RF-4 — merge-ready policy and executor

Risk-tier witness rules, merge-ready projection, **explicit authorization records with NO Michael click for Tier 0/1** (Michael Q2), one-shot merge, exact post-merge verification. No deploy.

**Owner:** Ned (Antigravity)
**Acceptance marker:** `PE_REVIEW_FACTORY_MERGE_EXECUTOR_OK`

**Standing merge policy for Tier 0/1** is the threshold the plan calls out. Until that policy exists, the executor stops at `merge_ready` and emits a `merge_authorizations` row with `requested: true`. The approval path for v1 is `python -m prismatic.merge_executor approve <authorization_id>` with operator-level credentials. **No new web UI for v1.**

### RF-5 — canonical dashboard integration

Reconnect into the existing dashboard with **v1 cut (Fred Q7)**:

**Must-have (RF-5 ships these):**
1. **Queue Operations:** depth + oldest age by state/tier, current worker (if leased), lease expiry
2. **Authorization State:** explicit "merge authorization required" and "deployment authorization required" indicators per candidate

**Nice-to-have (if cheap):**
3. **Throughput counters:** 1h/24h completed verdicts

**Deferred to post-v1:**
- p50/p95 latency per stage (need historical data)
- Reviewer utilization, exception rate
- Blocker/finding summary (linked from receipt instead)

**Owner:** Fred (orchestrator) — already owns dashboard surface
**Acceptance marker:** `PE_REVIEW_FACTORY_DASHBOARD_OK` (desktop/mobile proof required)

### RF-6 — backlog importer

Import Ned's compiled candidates idempotently from structured manifests. Deduplicate by repository + candidate tree + task identity. Reject entries without exact candidate/base/result bindings into a `needs_materialization` lane instead of making George investigate each one manually.

**Owner:** Ned (Antigravity)
**Acceptance marker:** `PE_REVIEW_FACTORY_BACKLOG_IMPORT_OK`

---

## 8. Cross-cutting additions (from planning-mode analysis)

### A. Wrap, don't replace, `prismatic/review/`

The plan says "existing PR reviewer code as a preliminary heuristic only, not acceptance authority." RF-3 implementation must **wrap** `pr_reviewer.py`, `pipeline.py`, and `registry.py` as the Tier 0/1 preliminary heuristic that runs BEFORE the LLM reviewer is assigned. **Do not deprecate or replace.**

This:
- Reuses ~3 existing modules (~500 LOC)
- Cheap deterministic pass before expensive LLM
- Matches the deterministic-first principle
- Keeps `pr_reviewer.py` as the "v0 reviewer" the plan implies

### B. Adversarial regression suite is part of RF-3 acceptance

The plan implies adversarial fixtures ("must be blocked despite green tests") but doesn't say where they live. **RF-3 ships with `prismatic/review_factory/tests/adversarial_fixtures/` containing:**

- Candidates with green tests but security boundary violations
- Candidates with green tests but invariants broken in the diff
- Candidates with receipt integrity valid but candidate_tree not descendant of base
- Candidates with Tier 0 classification but actually Tier 2 (catches policy misses)

The 20-fixture acceptance batch is honest production-like. The adversarial suite is the canary. **Both must pass for RF-3 acceptance.**

### C. One-time triage of legacy `agent:peer-review-blocked` queue

Before the factory ships, do a one-time classification of the existing 49 `agent:peer-review-blocked` Linear tasks:

| Class | Action |
|---|---|
| **Legacy George-protocol** (work submitted, awaiting review) | Finish under old protocol OR mark Won't-Fix |
| **Forward-looking only** (label, no actual review pending) | Reclassify or leave as-is |
| **Factory retrofit candidate** | Wait for RF-3 acceptance, then process through factory |

This is a one-time cleanup, not part of RF-1 through RF-6. **Owner: Fred (orchestrator).** Should happen before RF-3 acceptance so the factory doesn't inherit backlog noise.

### D. Dashboard "deployment authorization required" indicator ships in v1

The plan mentions "optional deployment queue (separate authorization)" but RF-1 through RF-6 don't include deployment. RF-5 ships the indicator anyway:

- State: `merge_authorized`
- Surface: "deployment authorization required" badge per candidate
- Action: NOT clickable in v1 (no deployment executor ships)

This prevents the state machine from having a dead-end branch. The indicator is honest about what's gated.

---

## 9. Service-level targets (Michael's plan + Fred Q9 calibration)

| Metric | Target | Status |
|---|---|---|
| Intake → deterministic receipt (Tier 0/1) | median < 5 minutes | **Aspirational for v1** |
| Review-ready → verdict (Tier 1) | median < 15 minutes | **Aspirational for v1** |
| George touch rate (Tier 0/1) | < 10% | **REAL GATE** — measure on 20-fixture batch |
| Throughput (20-fixture batch) | end-to-end without George hand-crafting | **REAL GATE** |

**Calibration note (Fred Q9):** the 5min/15min targets are diagnostic measurements during the 20-fixture test, not launch gates. Optimization against the targets begins post-v1 acceptance. Without this guard, the team optimizes for the metric and rubber-stamps Tier 1 jobs to keep p95 down.

---

## 10. Success boundary

The factory is successful when a batch of **at least 20 fixture candidates** can be:
1. Ingested idempotently from structured manifests
2. Deterministic-verified (parallel-safe, immutable archive)
3. Classified by risk tier via policy file
4. Assigned across multiple read-only reviewers (AGY + Jules)
5. Projected to `merge_ready | repair_required | rejected`
6. **Without George manually constructing per-candidate prompts or proof scripts**

**Production merge/deploy is NOT part of that throughput test.** RF-4 ships the executor + Tier 0/1 standing policy approval path; production deployments stay manual.

---

## 11. What does NOT ship in V1

- Deployment executor (mentioned in plan as "separate authorization" — out of scope)
- Web UI for approval (per Fred Q2 — v1 uses `prismatic.merge_executor approve <id>` CLI)
- p50/p95 latency dashboards (per Fred Q7 — deferred until data exists)
- AGY vs Jules functional differentiation (per Fred Q8 — same capability, different availability)
- Tier 3 reviewer queue (Tier 3 is human-authorization-only, no reviewer lease)
- Migration of the existing 49 review-blocked tasks (per cross-cutting C — one-time triage)

---

## 12. Out-of-band items currently in flight (do not block RF-0)

Three items unrelated to the factory build are in my in-flight list:

| Item | Status | Disposition |
|---|---|---|
| **PR #382 merge** (`feature/gro-4188-evidence-recaps` → main) | ✅ **MERGED 2026-07-31T20:03:33Z** by mbgulden (Michael). Merge commit `21be7812`. | **This is the documented LEGACY GEORGE-PROTOCOL EXEMPLAR.** See §12.1 below. |
| `feature/gro-3306 → main` (orchestrator/scripts, different repo) | Pending operator decision | Unrelated to factory |
| 7 additional `.bak` files (deferred from Move 18) | Pending operator decision | Unrelated to factory |

### 12.1 PR #382 — Legacy George-Protocol Exemplar (CRITICAL REFERENCE)

**Why this matters for the factory build:** PR #382 is the **last candidate that went through the old George-protocol flow** before the factory ships. It is the "before" baseline that proves the factory is needed.

**URLs (Antigravity-readable):**
- PR: https://github.com/mbgulden/prismatic-engine/pull/382
- Merge commit: `21be78125d6e73366e1576a8ecd39944cedbde15`
- Linear task: https://linear.app/growthwebdev/issue/GRO-4188 (now Done)
- Original PR branch tip: `c09761ed9df193b7fa86c0b94bb5b55e5321247e` (Fred's last commit)
- Local worktree used for diagnostics: `/home/ubuntu/work/prismatic-engine-gro-4188/`

**The actual cost (read this carefully, Antigravity):**

| Phase | Commits | Wall-clock time | Who did it |
|---|---|---|---|
| Original implementation | 4 (Fred) | ~30 min | Fred |
| PR #381 opened | 1 merge | day 1 | Fred |
| George review + fixes | 5 (George) | ~6 days (Jul 23 → Jul 31) | George |
| Sync merge from main | 1 | — | George |
| Final merge to main | 1 (Michael) | day 8 | Michael |
| **Total** | **11 commits across 8 days** | **8 days for one PR** | 2 agents + 1 operator |

**Key inefficiencies the factory must eliminate:**

1. **Serial hand-review:** George rewrote 5 commits manually (586dddf4, f4d76b19, f0f77c84, bb0bfdb2, 03c693b7). Each was a hand-crafted fix for a finding George had identified. The factory should turn these into structured `review_decisions.findings` + `repair_packets` so the producer (Ned/AGY) does the fix in their own lane.

2. **Synchronous Telegram polling:** George couldn't parallelize — he had to wait for each commit before reviewing. The factory's lease + queue model removes the polling.

3. **Per-candidate proof scripts:** Each George review likely included bespoke verification scripts. The factory's reusable `verification_receipts` schema means the verifier runs once per risk tier, not per candidate.

4. **Merge authorization is implicit:** Michael merged because George said it was OK. No `merge_authorizations` record. The factory makes this explicit and versioned.

5. **Single-reviewer bottleneck:** Even with George's review, this PR took 8 days. RF-3 + RF-4 must reduce Tier 1 wall-clock to <15 minutes (the SLA target).

**Antigravity, when building RF-1 through RF-6:** if you find yourself building something that looks like "George hand-reviewing a candidate," STOP. That's the pattern we're replacing. The factory's job is to make that pattern extinct.

---

## 13. Linear epic + child task structure (proposed)

After Michael sign-off, this OKF breaks into:

**Parent epic:** `GRO-RF-V1` — Prismatic Engine Review/Merge Factory V1
**Project:** Google AI Ultra Toolkit & Workflow (same as GRO-3306)

| Task ID | Title | Slice | Owner | Parent |
|---|---|---|---|---|
| GRO-RF-V1.0 | RF-0 Spec freeze | RF-0 | Ned + Fred | GRO-RF-V1 |
| GRO-RF-V1.1 | RF-1 Queue + state machine | RF-1 | Ned | GRO-RF-V1 |
| GRO-RF-V1.2 | RF-2 Deterministic verifier | RF-2 | Ned | GRO-RF-V1 |
| GRO-RF-V1.3 | RF-3 Portable reviewer capability | RF-3 | Fred | GRO-RF-V1 |
| GRO-RF-V1.4 | RF-4 Merge executor + standing policy | RF-4 | Ned | GRO-RF-V1 |
| GRO-RF-V1.5 | RF-5 Dashboard integration | RF-5 | Fred | GRO-RF-V1 |
| GRO-RF-V1.6 | RF-6 Backlog importer | RF-6 | Ned | GRO-RF-V1 |
| GRO-RF-V1.7 | Cross-cutting A: Wrap pr_reviewer | Cross | Fred + Ned | GRO-RF-V1 |
| GRO-RF-V1.8 | Cross-cutting B: Adversarial fixture suite | Cross | Fred | GRO-RF-V1 |
| GRO-RF-V1.9 | Cross-cutting C: Legacy review-queue triage | Cross | Fred | GRO-RF-V1 |
| GRO-RF-V1.10 | Cross-cutting D: Dashboard auth indicator | Cross | Fred | GRO-RF-V1 |
| GRO-RF-V1.11 | 20-fixture acceptance test | Test | Ned + Fred | GRO-RF-V1 |

**Linear creation order:** wait for Michael OK on this OKF doc, then create the epic + 12 child tasks in one batch, post the OKF URL as the parent comment.

---

## 14. References

- Michael's original plan (this conversation, 2026-07-31)
- Existing PE pieces: `prismatic/agy_completed_work.py` (1658 lines), `prismatic/review/`, `prismatic/verifiers/`, `prismatic/gateway/`
- Move 20 OKF (PR #382 hold for George): `state/okf-move-20-pr382-yes-request.md`
- Counter discipline: 91/91=100% at time of writing

---

## 15. Sign-off

- [ ] **Michael** — final approval of v1 scope, lane split (Antigravity owns build), and merge-authorization model
- [ ] **Ned** — RF-0, RF-1, RF-2, RF-4, RF-6 ownership
- [ ] **Fred** — RF-0 spec deliverables (5 of 10), RF-3, RF-5, cross-cutting A/B/C/D
- [ ] **George** — confirms he's OK with the policy/exception role (he doesn't have to review every candidate)

Once all 4 sign, this OKF becomes canonical. Linear epics + child tasks get created. RF-0 starts immediately.

---

## 16. Antigravity Context Pack (READ THIS FIRST, AGY)

This section is **the load-bearing reference for AGY when building with Antigravity 2.0 on Michael's laptop.** Every link below is verified against the live state as of 2026-07-31T20:10Z. If a link goes stale, treat that as a sign to refresh this section before relying on it.

### 16.1 Canonical file paths (Antigravity, `cat` these)

| What | Path | Why you need it |
|---|---|---|
| **This OKF** | `/home/ubuntu/.hermes/profiles/orchestrator/state/okf-review-factory-v1.md` | Single source of truth for v1 scope |
| **Move 20 OKF** (PR #382 hold history) | `/home/ubuntu/.hermes/profiles/orchestrator/state/okf-move-20-pr382-yes-request.md` | Documents the legacy-protocol exemplar context |
| **Orchestrator gaps inventory** | `/home/ubuntu/.hermes/profiles/orchestrator/state/okf-orchestrator-gaps-2026-07-31.md` | 15 gaps including cross-cutting C (legacy triage) |
| **PE triage** | `/home/ubuntu/.hermes/profiles/orchestrator/state/okf-pe-unassigned-triage-2026-07-31.md` | PE-* task landscape before factory ships |
| **Fred's handoff state** | `/home/ubuntu/.hermes/profiles/orchestrator/state/current.json` | Live in-flight + pending decisions |
| **Fred's counter log** | `/home/ubuntu/.hermes/profiles/orchestrator/state/proactive-count.json` | Discipline tracking |
| **Linear helpers (canonical)** | `/home/ubuntu/.hermes/profiles/orchestrator/scripts/linear_helpers.py` | `linear_comment`, `linear_update_issue`, etc. |

### 16.2 PE canonical file paths (Antigravity, `cat` these)

| What | Path | What you'll find |
|---|---|---|
| **AGY completed-work intake** | `/home/ubuntu/work/prismatic-engine/prismatic/agy_completed_work.py` | 1658 lines. Functions: `normalize_agy_result_packet`, `retain_completed_work_evidence`, `integration_classification_for`, `default_db_path`, `default_evidence_dir` |
| **AGY result packet validator** | `/home/ubuntu/work/prismatic-engine/prismatic/agy_result_packet.py` | `is_raw_agy_result_packet`, `require_valid_packet` |
| **AGY packet normalizer** | `/home/ubuntu/work/prismatic-engine/prismatic/agent_packet_normalizer.py` | `normalize` function entry point |
| **AGY result packet JSON schema** | `/home/ubuntu/work/prismatic-engine/schemas/agy-result-packet.schema.json` | Authoritative packet contract |
| **AGY packet contract doc** | `/home/ubuntu/work/prismatic-engine/docs/agy-result-packet-contract.md` | Human-readable packet spec |
| **Existing PR reviewer (wrap target)** | `/home/ubuntu/work/prismatic-engine/prismatic/review/pr_reviewer.py` | `PRReviewer` Protocol, `StubPRReviewer`, `RealPRReviewer` |
| **Existing PR reviewer impl** | `/home/ubuntu/work/prismatic-engine/prismatic/review/pr_reviewer_impl.py` | Real implementation — wrap, don't replace |
| **Review pipeline** | `/home/ubuntu/work/prismatic-engine/prismatic/review/pipeline.py` | `classify_impact`, `decide_next_action`, `PipelineOrchestrator`, `build_rework_payload` |
| **Reviewer registry** | `/home/ubuntu/work/prismatic-engine/prismatic/review/registry.py` | `ReviewerRegistry`, `ComposedReviewerSpec`, secret/checks/impact/action rules |
| **Verifier registry** | `/home/ubuntu/work/prismatic-engine/prismatic/verifiers/registry.py` | `VerifierRegistry`, plugin model |
| **Verifier schemas** | `/home/ubuntu/work/prismatic-engine/prismatic/verifiers/schemas.py` | `validate_verifier_result`, `load_verifier_result_schema`, `verify_log_file_integrity` |
| **Verifier result schema (JSON)** | `/home/ubuntu/work/prismatic-engine/schemas/verifier-result.schema.json` | Authoritative verifier result contract |
| **Merge candidate manifest** | `/home/ubuntu/work/prismatic-engine/prismatic/merge_candidate_manifest.py` | Backlog importer foundation (RF-6 reuse) |
| **Merge status** | `/home/ubuntu/work/prismatic-engine/prismatic/merge_status.py` | Merge state tracking |
| **Gateway server (API surface)** | `/home/ubuntu/work/prismatic-engine/prismatic/gateway/server.py` | All `/api/*` routes live here |
| **Dashboard tabs (HTML)** | `/home/ubuntu/work/prismatic-engine/prismatic/gateway/dashboard_src/tabs/` | `dashboard.html`, `merge.html`, etc. — RF-5 adds a new tab here |
| **Dashboard primary touchpoint doc** | `/home/ubuntu/work/prismatic-engine/docs/dashboard-primary-touchpoint.md` | Dashboard design philosophy + existing surfaces |

### 16.3 Live API endpoints (Antigravity, you can hit these)

**Completed-work gate (RF-1 ingestion surface):**
- `GET /api/completed-work/gate/schema` — Returns the AGY completed-work integration gate contract
- `GET /api/completed-work/gate/demo` — Returns fixture-only gate status (proof endpoint)

**Plugins (RF-1 will reuse this pattern for `review_jobs`):**
- `GET /api/plugins/catalog`
- `GET /api/plugins/governance`
- `GET /api/plugins/jobs`, `POST /api/plugins/jobs`, `GET /api/plugins/jobs/{id}`
- `POST /api/plugins/jobs/{id}/approve|reject|start|events|status`
- `GET /api/plugins/audit-events`
- `GET /api/plugins/artifacts`, `POST /api/plugins/artifacts`, `GET /api/plugins/artifacts/{id}`

**Agents / harnesses (RF-3 reviewer registration):**
- `GET /api/agents`
- `GET /api/harnesses`
- `GET /api/agents/raw-output`

**Health / quota (RF-5 dashboard backing):**
- `GET /health`
- `GET /api/cost`
- `GET /api/quota/caps`

### 16.4 Live git SHAs (Antigravity, verify against these)

| What | SHA |
|---|---|
| PR #382 merge commit (just landed) | `21be78125d6e73366e1576a8ecd39944cedbde15` |
| origin/main HEAD (post-merge) | `21be78125d6e73366e1576a8ecd39944cedbde15` |
| PR #382 branch tip (Fred's last commit, pre-George) | `c09761ed9df193b7fa86c0b94bb5b55e5321247e` |
| PR #382 base (PR #379 merge, already on main) | `d7e21566b727003fb79f9768a08e4b1aca13612d` |
| PR #382 George's review-fix commits | `586dddf4`, `f4d76b19`, `f0f77c84`, `bb0bfdb2`, `03c693b7` |

### 16.5 Live Linear state (Antigravity, query via `linear_helpers`)

| What | Value |
|---|---|
| Project ID (GRO project) | `b6fb2651-5a1f-4714-9bcd-9eb6e759ffef` |
| Parent epic for orchestrator/scripts work | `GRO-3306` ("Google AI Ultra Toolkit & Workflow") |
| PR #382 Linear task | `GRO-4188` (state: Done) |
| Existing 49 `agent:peer-review-blocked` tasks | TBD: cross-cutting C will classify these |
| Michael's Linear ID | `4a8a76b2-...` (displayName `mbgulden`) |
| George's Linear ID | `cf8b7670-dc6a-432b-b21a-cdf2b77b88a9` (displayName `ellageorgeson`) |
| AGY Linear ID (oauth app) | `ab8a37c8-...` |
| Codex Linear ID | `6833f44b-...` |
| Linear API key location | `/home/ubuntu/.hermes/profiles/orchestrator/.env` (`LINEAR_API_KEY=***` |

### 16.6 Live environment (Antigravity, sanity check)

| What | Value |
|---|---|
| Working repo (canonical, use this) | `/home/ubuntu/work/prismatic-engine/` (clone fresh from `origin/main @ 21be7812` for the factory build; the existing local clone on `ned/GRO-4195` is stale and missing newer files) |
| Orchestrator profile dir | `/home/ubuntu/.hermes/profiles/orchestrator/` |
| AGY state dir | `/home/ubuntu/.prismatic/state/` |
| AGY default DB path | `~/.prismatic/agy-result-packets/` (per `agy_completed_work.py:default_db_path`) |
| Python version | 3.12 (per `pyproject.toml`) |
| Test framework | pytest 9.0.3, with asyncio_mode=strict |
| Lint | ruff (pinned to a specific version per `Pin ruff below breaking rule release` in GRO-4187) |
| CI workflow | `.github/workflows/test.yml` (matrix: py3.10/3.11/3.12/3.13) |
| **Fresh clone command** | `cd /home/ubuntu/work && git clone https://github.com/mbgulden/prismatic-engine.git prismatic-engine-rf-v1 && cd prismatic-engine-rf-v1 && git checkout -b feature/rf-0-spec-freeze origin/main` |

**Note on stale local clone:** `/home/ubuntu/work/prismatic-engine/` is currently checked out to `ned/GRO-4195` and is missing some files that exist on `origin/main` post-PR-382 (`agy_completed_work.py`, `verifiers/registry.py`, `merge_candidate_manifest.py`, etc.). The fresh-clone command above produces a working tree with all the canonical files. The OKF's file paths assume the fresh-clone layout.

### 16.7 Build conventions (Antigravity, follow these)

| Convention | Where it's enforced | Notes |
|---|---|---|
| Commit message format | `[<agent>] <summary> (#<GRO-id>)` | e.g., `[Fred] Add evidence-cited journal recaps (#GRO-4188)` |
| Branch naming | `feature/gro-<id>-<slug>` or `agent/gro-<id>-<slug>` | The factory builds use `feature/rf-<n>-<slug>` (NEW convention) |
| Pre-commit hook | `.git/hooks/pre-commit` (per GRO-4187 work) | Verify before commit |
| Test isolation | pytest fixtures must be self-contained | AGY completed-work tests use `tmp_path` |
| Immutability | `completed_work` rows are insert-only | Never update; supersede with new row |
| Lane scope | `prismatic/SOUL.md` declares lane boundaries | Don't cross lanes |

### 16.8 Anti-patterns (Antigravity, do NOT do these)

1. ❌ Do NOT create a second completed-work database. Reuse `agy_completed_work.py`.
2. ❌ Do NOT replace `pr_reviewer.py`. Wrap it as the preliminary heuristic.
3. ❌ Do NOT add a web UI for merge approval. v1 uses `python -m prismatic.merge_executor approve <id>`.
4. ❌ Do NOT require Michael in the merge-authorization loop for Tier 0/1. Standing policy + credentials replace his click.
5. ❌ Do NOT run two reviewers on every candidate. Default is one. Witness only for Tier 2+ or explicit policy rule.
6. ❌ Do NOT silently retry product failures. Retry only infrastructure/setup failures.
7. ❌ Do NOT call baseline parity canonical green. Mark baseline failures separately by exact identity.
8. ❌ Do NOT mutate the producer's worktree. Always work on a fresh archive extraction.
9. ❌ Do NOT push, merge, deploy, restart, or mutate Linear from RF-1 through RF-6 without separate authority.
10. ❌ Do NOT treat review completion as merge authority. These are separate records.

### 16.9 RF-0 spec-freeze deliverables — exact file destinations

When RF-0 lands, the 10 spec docs go to `/home/ubuntu/work/prismatic-engine/prismatic/review_factory/spec/`:

```
prismatic/review_factory/spec/
├── README.md                                # Index + cross-references
├── agy_completed_work_v1.md                 # Schema freeze (Ned)
├── review_jobs_v1.md                        # Table schema (Ned)
├── verification_receipts_v1.md              # Receipt schema (Ned)
├── review_decisions_v1.md                   # Decision schema (Ned)
├── merge_authorizations_v1.md               # Authorization schema (Ned)
├── repair_packets_v1.md                     # Repair packet schema (Fred)
├── lease_semantics_v1.md                    # Lease rules (Ned, with Fred review)
├── policy_file_v1.yaml                      # Risk tier classifier (Fred)
├── handoff_contracts_v1.md                  # RF-1→RF-6 contracts (Fred)
└── dashboard_v1_cut.md                      # Dashboard metrics scope (Fred)
```

**Acceptance:** `PE_REVIEW_FACTORY_SPEC_V1_OK` marker — all 10 docs checked in + signed off by Ned (5), Fred (5), Michael (final).

### 16.10 How to verify each acceptance marker

When AGY builds each RF slice, run this exact command pattern to prove the marker:

| Marker | Command (run from `/home/ubuntu/work/prismatic-engine/`) |
|---|---|
| `PE_REVIEW_FACTORY_SPEC_V1_OK` | `ls prismatic/review_factory/spec/*.md prismatic/review_factory/spec/*.yaml \| wc -l` should return `10` |
| `PE_REVIEW_FACTORY_QUEUE_OK` (RF-1) | `python -m pytest tests/test_review_jobs_queue.py -v` (3/3 PASS minimum) + `python -c "from prismatic.review_factory.queue import enqueue_completed_work; print(enqueue_completed_work('GRO-4188-test-fixture'))"` returns a UUID |
| `PE_REVIEW_FACTORY_VERIFIER_OK` (RF-2) | `python -m pytest tests/test_verification_worker.py -v` (5/5 PASS minimum) + one demo run produces a valid `verification_receipts` row |
| `PE_REVIEW_FACTORY_AGY_REVIEWER_OK` (RF-3) | `python -m pytest tests/test_reviewer_capability.py tests/test_adversarial_fixtures.py -v` (all PASS, including adversarial blocks) + `pip install prismatic-review-worker==0.1.0` succeeds |
| `PE_REVIEW_FACTORY_MERGE_EXECUTOR_OK` (RF-4) | `python -m pytest tests/test_merge_executor.py -v` (4/4 PASS minimum) + dry-run on a fixture candidate produces a valid `merge_authorizations` row |
| `PE_REVIEW_FACTORY_DASHBOARD_OK` (RF-5) | Manual: open `http://localhost:8000/dashboard` → new "Review Factory" tab → queue + auth state visible → desktop + mobile screenshot proof saved to `docs/dashboard-review-factory.png` |
| `PE_REVIEW_FACTORY_BACKLOG_IMPORT_OK` (RF-6) | `python -m pytest tests/test_backlog_importer.py -v` + dry-run with `merge_candidate_manifest` fixture ingests 20 entries idempotently |

### 16.11 Debugging tips (Antigravity, when stuck)

| Symptom | First thing to check |
|---|---|
| Queue row not appearing | Verify `agy_completed_work.py:retain_completed_work_evidence` succeeded; check `default_db_path()` returns the right path |
| Reviewer can't lease a job | Check `review_jobs.state` transition table in `lease_semantics_v1.md`; verify lease_expires_at is in the future |
| Verifier runs but no receipt | Check `verification_receipts.immutable_archive_id` matches `git rev-parse HEAD^{tree}` of the archive; check log file integrity via `verify_log_file_integrity` |
| Tier classifier gives wrong tier | Re-run `policy_file_v1.yaml` against changed_paths; check `fail_closed_on_policy_miss` is on |
| Merge executor blocks | Verify `merge_authorizations.consumed_at IS NULL` and `expires_at > NOW()`; check idempotency_key matches |
| Dashboard tab is blank | Check `prismatic/gateway/server.py` for the new route registration; verify `prismatic/gateway/dashboard_src/tabs/review_factory.html` is in the tabs list |
| 20-fixture acceptance fails | Distinguish: production-like failures (legit, fix the factory) vs adversarial-fixture passes (good, the canary works) |

### 16.12 "Did I miss anything?" checklist for AGY

Before declaring any RF slice done, AGY must verify:

- [ ] All canonical record schemas match §5 of this OKF
- [ ] No new database created (RF-1 reuses agy_completed_work)
- [ ] No code in `pr_reviewer.py` was replaced (RF-3 wraps it)
- [ ] No web UI added for approval (RF-4 uses CLI)
- [ ] No Michael-click required for Tier 0/1 merge authorization
- [ ] All adversarial fixtures in `tests/test_adversarial_fixtures.py` PASS (RF-3)
- [ ] 20-fixture acceptance test for the slice passes
- [ ] The corresponding `PE_REVIEW_FACTORY_*_OK` marker command from §16.10 succeeds
- [ ] No push, PR, merge, deploy, or Linear mutation done without separate authority
- [ ] Counter discipline maintained (100% bounded moves silent)
- [ ] No lane crossing (Fred's lane = scripts/dashboard/capability; Ned's lane = PE core/queue/verifier/merge)

**If any checkbox is unchecked, the slice is not done.**

---

## 17. Quick-reference card (Antigravity, print this)

```
REVIEW/MERGE FACTORY V1 — QUICK REFERENCE
==========================================
Build owner:        Antigravity 2.0 (Michael's laptop)
Source repo:        github.com/mbgulden/prismatic-engine
Base branch:        origin/main @ 21be7812 (post-PR-382)
Working dir:        /home/ubuntu/work/prismatic-engine/
Spec freeze dir:    prismatic/review_factory/spec/ (RF-0)
Implementation:     RF-1 queue, RF-2 verifier, RF-3 reviewer,
                    RF-4 merge, RF-5 dashboard, RF-6 backlog

LEGACY BASELINE:    PR #382 took 8 days, 11 commits, George hand-review
FACTORY TARGET:     Tier 1 PR <15min wall-clock, <10% George touch

OWNERSHIP:
  RF-0:  Ned (5) + Fred (5)
  RF-1:  Ned
  RF-2:  Ned (worker) + Fred (check defs)
  RF-3:  Fred
  RF-4:  Ned
  RF-5:  Fred
  RF-6:  Ned
  X-A:   Fred + Ned
  X-B:   Fred
  X-C:   Fred
  X-D:   Fred

DO NOT:
  X Create parallel completed-work DB
  X Replace pr_reviewer.py
  X Add web UI for merge approval
  X Require Michael click for Tier 0/1
  X Run 2 reviewers per candidate by default
  X Silently retry product failures
  X Mutate producer worktree

VERIFICATION:
  Spec:   ls prismatic/review_factory/spec/*.{md,yaml} | wc -l -> 10
  RF-1:   pytest tests/test_review_jobs_queue.py 3/3 + enqueue returns UUID
  RF-2:   pytest tests/test_verification_worker.py 5/5 + receipt row written
  RF-3:   pytest tests/test_reviewer_capability.py + test_adversarial_fixtures.py
  RF-4:   pytest tests/test_merge_executor.py 4/4 + merge_authorizations row
  RF-5:   dashboard tab visible, desktop+mobile screenshot saved
  RF-6:   pytest tests/test_backlog_importer.py + 20 fixtures idempotent

COUNTER:             91/91=100%
ONE-LINE:            PR #382 MERGED (legacy exemplar). Factory OKF awaits sign-off.
IN-FLIGHT:           Review Factory V1 OKF sign-off (Michael/Ned/Fred/George)
PENDING DECISIONS:   (1) Approve OKF (2) Merge feature/gro-3306 (3) Cleanup 7 .bak files
```

**END OF OKF.**
