---
name: agy-tdd-discipline
description: "AGY test-first discipline: RED → GREEN → REFACTOR, with focused tests before code for behavior changes and regression tests for bugs."
tags: []
related_skills:
  - agy-as-coder
---

# agy-tdd-discipline

## Purpose

Make AGY prove behavior before and after implementation. Tests are the contract becoming executable.

## When to Use

Use for new functions/modules/APIs/CLI commands, bug fixes, payment/auth/credential logic, parsers, migrations, and game logic state transitions.

## Operating Loop

1. RED: add/update focused failing test.
2. GREEN: implement smallest code that passes.
3. REFACTOR: improve structure without behavior change.
4. VERIFY: targeted test then relevant suite.

## Required Artifacts

- Failing test output
- Implementation summary
- Passing test output
- Broader regression command/output

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- No tests later.
- No weakening tests to go green.
- Exceptions allowed only for typo/docs-only or pure visual/config changes with explicit smoke proof.

## Dispatch Skeleton

```text
Lane: agy-tdd-discipline
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
