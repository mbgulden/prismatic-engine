---
name: agy-runtime-contract-closure
description: "Tier 1 Core Oracle Skill: Enforces defect closure through black-box external reproductions, 6 Anti-Deception rules, decision-path tracing, pre-repair RED receipts, and exact-head proof."
tags: [oracle, closure, anti-deception, core, tier1]
related_skills:
  - agy-tdd-discipline
  - agy-installed-artifact-first
---

# agy-runtime-contract-closure

## Purpose

Prevent self-deceiving tests and false-positive completions. Enforce that every reported defect or feature contract is proven closed by running the exact external reproduction before repair (capturing RED output), tracing the authoritative decision path, applying the smallest responsible fix, and verifying against an immutable candidate.

---

## Trigger

Active as Tier 1 Core Operating Discipline for all coding, debugging, refactoring, and review passes.

---

## The 6 Anti-Deception Invariants

Antigravity MUST NOT mark any defect or task closed if any of these 6 failure modes occur:

| Anti-Deception Rule | Violation (Forbidden Practice) | Mandatory Correct Behavior |
| :--- | :--- | :--- |
| **1. Observable Execution Proof** | Creating a helper method/class (e.g. `_materialize_immutable_archive`) without invoking it, or relying solely on mock call counts (`assert call_count == 1`). | Call-count tracing supplements, but NEVER replaces, black-box observable state/return proof against the real target path. |
| **2. Route Surface Proof** | Declaring a router module in source without mounting it on the canonical running `app`. | Enumerate exact method/path pairs on a freshly instantiated `prismatic.gateway.server:app` instance and execute representative HTTP/WS requests. |
| **3. Distribution Closure** | Proving imports work inside the source tree while failing from an installed wheel package. | Build wheel, install in clean isolated venv (`mktemp -d`), remove `PYTHONPATH`, and test from an unrelated empty directory. |
| **4. Collision & Isolation** | Testing job A in isolation while multi-job requests sharing a git tree mutate job B. | Always test 2+ items sharing a key; assert job B remains untouched when job A is mutated/consumed. |
| **5. Boundary & Empty Fences** | Accepting shape-valid inputs containing empty/whitespace strings (e.g. `worker_id="   "`). | Enforce strict boundary validation at the authoritative boundary (reject empty/whitespace strings). |
| **6. Receipt Identity Truth** | Generating receipt strings derived solely from user inputs rather than actual returned artifacts. | Bind receipt identifiers strictly to the returned materialized object's SHA-256 digest or path. |

---

## Pre-Repair Workflow Protocol

Before writing any production code fix, Antigravity MUST execute this 4-step protocol:

### Step 1: Pre-Change Receipt
- **For Defects**: Freeze the reported defect as a named black-box reproduction test and execute it to capture the un-truncated RED failure output.
- **For New Features**: Write a failing executable contract test or explicit absence proof.
- **For Refactoring / Docs**: Run existing test suite to establish a GREEN baseline receipt.

```text
DEFECT_OR_FEATURE=<name>
PRE_REPAIR_COMMAND=<exact pytest command>
PRE_REPAIR_RESULT=FAIL_AS_EXPECTED
PRE_REPAIR_LOG=<absolute path to log>
```

### Step 2: Decision-Path Tracing
Document the authoritative entry point and state mutation path before editing code:
```text
AUTHORITY_FIELD=<normative identity, e.g. review_job_id>
VALIDATION_BOUNDARY=<where bad input is rejected, e.g. db.py line 210>
DECISION_PATH=<caller -> service -> query>
PERSISTED_EFFECT=<rows / files / runtime routes changed>
OBSERVABLE_PROOF=<black-box assertion verifying defect is fixed>
```

### Step 3: Minimal Production Fix
Apply the smallest responsible change directly to the authoritative decision path.

### Step 4: Immutable Candidate Verification
Run the exact same reproduction command against an immutable checkout or archive of the repaired candidate SHA to prove GREEN.

---

## Mutation Sanity Check Requirement

For high-risk regressions, Antigravity MUST temporarily revert or bypass the fix in local memory/test execution to confirm that the test fails (proving the test is sensitive to the actual defect, not a helper stub).

---

## Standardized 11-Field Proof Packet

```text
COMMAND=<exact build/test command>
RESULT=<PASS|FAIL|BLOCKED>
LOG=<absolute path to log>
SCOPE=<bounded domain/file scope>
AD_HOC_OR_CANONICAL=<ad-hoc targeted|canonical suite>
NOT_CLAIMING=<explicit non-claims, e.g. production_deployment, merge_authorization>
HEAD=<exact 40-char commit sha>
TREE=<exact 40-char tree sha>
ARTIFACT_SHA256=<file sha256 digest when applicable>
ACTIVATION_TRACE=<which tiers loaded and why>
MARKER=AGY_RUNTIME_CONTRACT_CLOSURE_OK
```
