---
name: agy-as-planner
description: "AGY as execution planner: turns a goal or architecture contract into bite-sized tasks with paths, acceptance criteria, sequencing, risk, rollback, and documentation-in-same-commit requirements."
tags: []
related_skills:
  - agy-as-architect
  - agy-as-coder
  - agy-documentation-on-the-fly
---

# agy-as-planner

## Purpose

Turn a big goal into executable work packets that another agent can pick up cold without translating.

## When to Use

Use after architecture, before multi-PR work, before cron/system rollout, or when a vision needs sequencing across agents/lanes.

## Operating Loop

1. Load goal/contract.
2. Identify lanes and dependencies.
3. Split by concern and branch.
4. Add acceptance criteria and verification for each task.
5. Add documentation update in same commit.
6. Add rollback/risk notes.

## Required Artifacts

- Plan summary
- Task graph
- One task per branch/concern
- Files likely touched
- Acceptance criteria
- Verification commands
- Documentation update section
- Rollback note

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- No mega-branches.
- No tasks without verification.
- No docs-later.
- If a task crosses lanes, split it.

## Dispatch Skeleton

```text
Lane: agy-as-planner
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
