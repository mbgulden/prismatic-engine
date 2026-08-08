---
name: agy-systematic-debug
description: "AGY systematic debugging method: reproduce, narrow, hypothesize, instrument, isolate, repair, regression-test. Use with agy-as-debugger."
tags: []
related_skills:
  - agy-as-debugger
---

# agy-systematic-debug

## Purpose

A concrete debugging method AGY follows so it does not thrash or guess.

## When to Use

Use whenever the bug is not already root-caused, especially production failures, flaky behavior, regressions, and performance issues.

## Operating Loop

1. Observe exact error.
2. Reproduce smallest case.
3. Narrow the surface.
4. Hypothesize with disproof probes.
5. Probe.
6. Fix minimally.
7. Prove with regression.
8. Clean temporary probes.

## Required Artifacts

- Observation log
- Repro
- Hypothesis/probe table
- Root cause
- Regression proof

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- Do not change multiple variables at once.
- Do not upgrade dependencies as first move.
- Do not delete code until the error disappears while breaking the feature.

## Dispatch Skeleton

```text
Lane: agy-systematic-debug
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
