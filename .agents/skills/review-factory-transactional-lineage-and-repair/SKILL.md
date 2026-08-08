---
name: review-factory-transactional-lineage-and-repair
description: "Review Factory transactional aggregate identity, database migrations, stale evidence invalidation, atomic lease fencing, and repair packet consumption."
tags: [review-factory, transactional, lineage, database, repair]
related_skills:
  - agy-tdd-discipline
  - prismatic-full-feature-delivery-gate
---

# review-factory-transactional-lineage-and-repair

## Purpose

Enforce strict transactional aggregate identity and lifecycle state transitions across Review Factory jobs, leases, verification receipts, review decisions, repair packets, witness counts, attempts, and merge authorizations.

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Trigger

Load for Review Factory schemas, database migrations, leases, receipts, decisions, repair packets, witness counts, attempts, authorizations, or state transitions.

---

## Product Outcome

Every repair and review artifact belongs to exactly one review job, candidate commit/tree, manifest, and attempt. Repairing a candidate atomically retires stale authority/evidence in a single database transaction and leaves a recoverable, fully audited lifecycle.

---

## Existing Authority & Preservation Boundary

Inspect `prismatic/review_factory/db.py`, `prismatic/review_factory/queue.py`, and SQLite schema invariants before editing. Preserve foreign key constraints (`review_decisions` -> `verification_receipts` -> `review_jobs`).

---

## Workflow Protocol

1. **Aggregate Identity**: Model the aggregate identity: `(review_job_id, candidate_commit, candidate_tree, attempt)`.
2. **Schema & Migrations**: Add real forward and rollback migrations, foreign keys, uniqueness (`completed_work_id UNIQUE`), and query indexes.
3. **Transactional Repair Consumption**: Make repair consumption job-bound, old-candidate-bound, and transactional (`consume_repair_and_requeue_job`).
4. **Stale Evidence Retirement**: In the same SQL transaction, delete/supersede old receipts, verdicts, witnesses, authorizations, leases, and attempt-local evidence in valid FK order (decisions before receipts).
5. **Current-Candidate Filtering**: Make current-candidate queries the default; historical queries must be explicit and audit-oriented.
6. **Lease & Worker Fencing**: Require nonempty worker/reviewer identity or unforgeable lease token when accepting lease renewals, completions, or verdicts.
7. **Atomic Validation**: Atomically validate state, owner/token, parseable expiry, candidate binding, manifest digest, artifact ID, and attempt before insert/transition.
8. **Automated Repair State Machine**: Add explicit state-machine transitions from merge-verification failure to repair-required/queued; no manual database edits.
9. **Durable Audit Logging**: Persist audit events for repair, supersession, rejected stale evidence, and recovery transitions.

---

## Anti-Stub Gate

Block completion if:
- A database migration exists without transactionally updated readers/writers.
- `review_job_id` is added to tables but queries still fetch or count by shared tree.
- A state enum contains unreachable, untested transition states.
- A `is_stale` or `superseded` flag exists but callers do not filter by default.
- A lease completion or verdict is accepted without fencing the worker identity and expiry.

---

## Adversarial Test Requirements

- **Cross-Job Isolation**: Two jobs share one git tree; repairing job B cannot consume job A's repair packet or transition job A.
- **Stale Witness Invalidated**: Old CLEAN witness plus one new witness cannot satisfy quorum after a repair cycle.
- **Fail-Closed Validation**: Empty/malformed identity, parseable expiry, candidate fields, or digest fails closed.
- **Attempt Isolation**: Old-attempt receipts and cross-job/cross-candidate decisions fail.
- **Automated Repair Loop**: Failed merge verification enters a tested repair cycle automatically.

---

## Required Proof Packet

```text
COMMAND=pytest prismatic/review_factory/tests/ -v
RESULT=<PASS|FAIL|BLOCKED>
LOG=<path to log>
SCOPE=review-factory-transactional-lineage-and-repair
HEAD=<commit sha>
TREE=<tree sha>
STALE_EVIDENCE_INVALIDATED=true
CROSS_JOB_ISOLATION_VERIFIED=true
MARKER=PE_RF_TRANSACTIONAL_LINEAGE_OK
```
