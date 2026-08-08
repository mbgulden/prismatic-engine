---
name: agy-prismatic-engine-authoring
description: "AGY authoring inside Prismatic Engine: kernel-first design, harness-agnostic naming, lane locks, branch rules, docs-on-the-fly, tests, and additive transformative workflow."
tags: []
related_skills:
  - prismatic-engine-operations
  - agy-documentation-on-the-fly
  - agy-as-coder
  - agy-as-architect
---

# agy-prismatic-engine-authoring

## Purpose

Make AGY build Prismatic as an engine kernel, not a Hermes plugin pile.

## When to Use

Use for any AGY work inside Prismatic Engine, portable skills, providers, attach points, command center, dispatchers, or engine CLIs.

## Operating Loop

1. Recon first: git status, in-flight inventory, tests.
2. Lock files before edits where required.
3. Branch feature/<scope>.
4. Keep changes additive.
5. Use prismatic-* / PRISMATIC_* naming for engine.
6. Tests and docs in same commit.
7. Verify externally.

## Required Artifacts

- Recon notes
- Locked files
- Branch/commit
- Test output
- Docs update
- Harness-agnostic naming check

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- No Hermes imports in engine modules.
- No harness-named canonical CLIs.
- No hardcoded /home/ubuntu paths in portable code.
- No teardown without inventory.

## Dispatch Skeleton

```text
Lane: agy-prismatic-engine-authoring
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
