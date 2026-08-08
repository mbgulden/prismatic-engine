---
name: agy-lane-taxonomy
description: "Canonical AGY operating taxonomy: lanes, launch patterns, companion skills, artifacts, and dispatch decision tree. Load before dispatching AGY so it can meet its greatness instead of being treated like a snippet generator."
tags: []
related_skills:
  - agy-delegate-goals-not-tasks
  - antigravity-cli-orchestration
---

# agy-lane-taxonomy

## Purpose

Pick the lane before writing the prompt. The lane determines launch pattern, allowed workspace access, expected artifacts, and quality bar. AGY is a high-agency operator; the taxonomy prevents us from shrinking it into a snippet generator.

## When to Use

Load before any non-trivial AGY dispatch, especially when routing Linear issues, creating AGY tasks, or deciding whether AGY should research, architect, code, debug, review, generate assets, or work on game playability.

## Operating Loop

1. Identify the desired outcome.
2. Choose exactly one primary lane.
3. Add cross-cutting execution skills as needed.
4. Choose launch pattern: /tmp no --add-dir for research/architecture/review/assets; repo + --add-dir for coding/game implementation.
5. Require artifacts and verification.
6. Verify the artifacts exist before reporting done.

## Required Artifacts

- Lane selection
- Companion skill list
- Launch command pattern
- Required outputs
- Verification method

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- Do not blend deep research and code mutation unless explicitly requested.
- Do not use AGY for mechanical bulk enumeration; precompute data deterministically, then let AGY synthesize.
- Do not launch research from a repo or with --add-dir unless repo inspection is required and no-modify is explicit.

## Dispatch Skeleton

```text
Lane: agy-lane-taxonomy
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
