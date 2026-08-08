---
name: agy-documentation-on-the-fly
description: "AGY docs-on-the-fly discipline: docs, READMEs, specs, runbooks, and skill updates ship in the same commit as code; no follow-up documentation debt."
tags: []
related_skills:
  - agy-as-coder
  - agy-as-reviewer
  - agy-as-planner
---

# agy-documentation-on-the-fly

## Purpose

Ensure every code/behavior change updates docs immediately so future agents inherit the truth.

## When to Use

Use for any implementation that changes behavior, contracts, commands, config, deploy steps, architecture, or procedures.

## Operating Loop

1. Identify doc file and section before implementation.
2. Update doc in same branch/commit.
3. Include what changed, what is out of scope, and verification.
4. Reviewer checks docs before approval.

## Required Artifacts

- Updated README/spec/runbook/API help/skill
- Summary naming changed doc section
- Verification command/operator note

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- No docs later.
- No implied by code.
- No stale contradictory docs.
- Reviewer must reject missing docs when required.

## Dispatch Skeleton

```text
Lane: agy-documentation-on-the-fly
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
