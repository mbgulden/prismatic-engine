---
name: transactional-merge-execution-rollback-and-audit
description: "Safe transactional merge execution, expected vs actual merge-tree verification, post-merge failure propagation, atomic rollback, and audit logging."
tags: [merge-execution, rollback, audit, git, verification]
related_skills:
  - agy-tdd-discipline
  - prismatic-full-feature-delivery-gate
---

# transactional-merge-execution-rollback-and-audit

## Purpose

Enforce strict transactional merge execution, verifying live target base SHAs, comparing actual integration merge-tree results against expected trees, propagating post-merge test failures, executing verified atomic rollbacks, and writing immutable audit trails.

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Trigger

Load for merge orchestration, integration tests, merge-tree verification, authorization token consumption, rollback execution, or merge audit logging.

---

## Product Outcome

A merge is marked successful only when the live target ref, actual integration merge result tree, required unit/integration tests, audit log persistence, authorization consumption, and state transition all agree. Every post-merge failure automatically restores the target ref to its exact pre-merge SHA and remains in a recoverable repair state.

---

## Existing Authority & Preservation Boundary

Inspect `prismatic/review_factory/merge_executor.py`, `prismatic/integrate.py`, and `prismatic/lock.py` before editing. Preserve atomic lock acquisition and pre-merge tree snapshots.

---

## Workflow Protocol

1. **Pre-Merge Target Snapshot**: Capture and record the exact pre-merge target SHA and tree (`git rev-parse main^{tree}`).
2. **Live Target Verification**: Fetch safely and verify that the live target ref matches the authorized base SHA before executing merge operations.
3. **Merge-Tree Calculation**: Compute expected merge-result tree where policy allows; compare candidate tree vs actual integration merge tree. Never confuse candidate tree with merge result tree.
4. **Atomic Authorization Claim**: Atomically claim authorization token before executing merge side effects.
5. **Controlled Execution**: Execute merge in a disposable, controlled temporary workspace or worktree.
6. **Integration Test Verification**: Preserve failed integration status; require `integration_manifest.is_success()` and all required test suites green.
7. **Merge Result Tree Audit**: Verify actual merge SHA and git tree against expected target state.
8. **Automatic Rollback**: On any post-merge test, build, or verification failure, execute atomic reset/rollback (`git reset --hard pre_merge_sha`) to restore target ref to exact pre-merge state.
9. **Rollback Verification**: Verify that target ref is cleanly restored post-rollback before releasing locks.
10. **State Machine Fail-Closed**: Do not consume authorization token or mark job merged on failure; transition job to `REPAIR_REQUIRED` with durable failure audit rows.

---

## Anti-Stub Gate

Block completion if:
- Merge function returns `True` without durable audit records.
- Tests mock out all Git calls without executing against a real test repository.
- Integration phase marks job complete regardless of test exit codes.
- Rollback logic is documented in code comments but never executed and verified during failures.

---

## Adversarial Test Requirements

- **Real Disposable Git Repository Test**: Execute merge against an intentional failing test case in a real temporary git repository:
  1. Executor returns `success = False`.
  2. Target ref is verified cleanly restored to exact pre-merge SHA.
  3. Merge authorization token is NOT consumed.
  4. Job enters `REPAIR_REQUIRED` state automatically.
  5. Durable failure and rollback audit logs are written to database.

---

## Required Proof Packet

```text
COMMAND=pytest prismatic/review_factory/tests/test_merge_executor.py -v
RESULT=<PASS|FAIL|BLOCKED>
LOG=<path to log>
SCOPE=transactional-merge-execution-rollback-and-audit
HEAD=<commit sha>
TREE=<tree sha>
MERGE_TREE_VERIFIED=true
ATOMIC_ROLLBACK_VERIFIED=true
MARKER=PE_TRANSACTIONAL_MERGE_OK
```
