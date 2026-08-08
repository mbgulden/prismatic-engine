---
name: agy-tdd-discipline
description: "AGY test-first discipline: RED → GREEN → REFACTOR, enforcing the 5-Point Counterexample Matrix before production code edits."
tags: [tdd, testing, quality, discipline, verification]
related_skills:
  - agy-runtime-contract-closure
  - agy-systematic-debug
---

# agy-tdd-discipline

## Purpose

Make AGY prove behavior before and after implementation. Tests are the contract becoming executable. No code modification occurs without a prior, verified, failing test receipt.

---

## Trigger

Active as Tier 1 Core Operating Discipline for all coding, debugging, refactoring, and review passes.

---

## The Applicability-Aware 5-Point Counterexample Matrix

Every TDD test plan MUST evaluate counterexamples before happy-path examples. For identity, authorization, isolation, routing, and persistence contracts, all applicable dimensions are mandatory. Non-applicable dimensions require an explicit 1-line `N/A` justification.

1. **Positive Case**: Valid input succeeds and produces expected state/return value.
2. **Direct Negative Case**: Invalid input (e.g. malformed key, bad token) fails closed.
3. **Collision / Isolation Case**: Multi-job or multi-item requests sharing a key (e.g. 2 jobs sharing a git tree) leave item 2 untouched when item 1 is mutated/consumed.
4. **Boundary & Empty Case**: Empty strings (`""`), whitespace-only strings (`"   "`), `None`, or missing fields fail closed at the authoritative boundary.
5. **Bypass-Path Case**: Alternative call paths (e.g. secondary import paths, direct DB calls, unauthenticated HTTP headers) fail closed.

---

## Canonical 7-Step Operating Loop

For every task, fix, or review pass, AGY MUST strictly adhere to the following 7-step sequence:

1. **Add the Counterexample Reproduction as a failing test first**:
   - Write a dedicated test case that reproduces the exact bug or missing feature contract covering all applicable counterexample cases.
2. **Run it and preserve the RED output**:
   - Execute the test immediately BEFORE editing production code.
   - Capture and log the un-truncated RED test failure stack trace in context.
3. **Implement the smallest production fix**:
   - Modify only the production code required to satisfy the failing test contract.
4. **Prove that exact test turns GREEN**:
   - Re-run the exact failing test and capture the GREEN pass log.
5. **Run the broader test suite**:
   - Execute the full domain test suite (e.g. `pytest prismatic/review_factory/tests/`) to verify zero regressions.
6. **Run independent adversarial proof**:
   - Perform independent verification using separate test drivers, live API routes, or CLI invocations outside unit test mocks.
7. **Do not weaken assertions or substitute implementation-shaped tests**:
   - NEVER alter test assertions to match broken code behavior.

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
MARKER=AGY_TDD_DISCIPLINE_OK
```
