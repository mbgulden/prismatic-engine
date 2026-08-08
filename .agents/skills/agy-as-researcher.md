---
name: agy-as-researcher
description: "AGY as quick targeted researcher: bounded lookup, cited answer, source quality, confidence labels. Use for fast research; use agy-research-metabolizer for deep multi-source report bundles."
tags: []
related_skills:
  - agy-research-metabolizer
  - agy-lane-taxonomy
---

# agy-as-researcher

## Purpose

Use AGY for quick, bounded research with citations and confidence labels. This is the fast sibling to the deeper agy-research-metabolizer lane.

## When to Use

Use for current docs/version/API facts, finding canonical references, validating a claim, collecting 3-7 high-quality links, or answering a narrow question with citations.

## Operating Loop

1. Restate the question and downstream decision.
2. Find authoritative sources first: official docs, repos, specs, maintainers.
3. Add secondary sources only when useful.
4. Compare sources for important claims.
5. Return answer, citations, confidence, and gaps.

## Required Artifacts

- Direct answer
- Source list with why each matters
- Confidence labels
- What changed recently
- What remains uncertain

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- Do not produce a top-10 link dump.
- Do not over-trust forum posts.
- Do not treat quick research as deep strategy; escalate to agy-research-metabolizer when synthesis is needed.

## Dispatch Skeleton

```text
Lane: agy-as-researcher
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
