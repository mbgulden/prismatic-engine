---
name: agy-as-debugger
description: "AGY as root-cause debugger: reproduce, form hypotheses, instrument, isolate, fix, and add regression tests. Use for unknown bugs before coding a fix."
tags: []
related_skills:
  - agy-systematic-debug
  - agy-as-coder
  - agy-tdd-discipline
---

# agy-as-debugger

## Purpose

AGY finds root cause before patching. Debugging is not symptom-editing; it is evidence-driven isolation followed by minimal repair and regression proof.

## When to Use

Use for blank pages, non-playable games, production/local mismatch, broken deploys, intermittent failures, race conditions, performance regressions, and any bug with unknown cause.

## Operating Loop

1. Reproduce exactly.
2. Bound affected surface.
3. Create ranked hypothesis table.
4. Instrument with logs/probes.
5. Isolate root cause.
6. Apply minimal fix.
7. Add regression test or smoke check.
8. Verify externally.

## Required Artifacts

- Reproduction steps
- Hypothesis table
- Evidence log
- Root-cause statement
- Patch summary
- Regression test/smoke check
- External verification proof

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- No probably fixed.
- No broad refactor while debugging.
- No editing before reproduction unless emergency rollback is required.
- No closing without regression proof.

## Dispatch Skeleton

```text
Lane: agy-as-debugger
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
