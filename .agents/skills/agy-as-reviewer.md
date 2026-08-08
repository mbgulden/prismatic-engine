---
name: agy-as-reviewer
description: "AGY as reviewer: read-only PR/spec/architecture/security review with explicit APPROVE / NEEDS_CHANGES / BLOCKED verdict and evidence-backed findings."
tags: []
related_skills:
  - agy-secure-coding
  - agy-documentation-on-the-fly
  - prismatic-validation-pipeline
---

# agy-as-reviewer

## Purpose

AGY produces a verdict, not commentary. Reviewer mode is read-only unless a separate fix task is created.

## When to Use

Use for PR review, spec review, architecture validation, post-merge audit, security/payment/auth review, and final publish gates.

## Operating Loop

1. Load contract/diff/tests/docs.
2. Review correctness, tests, security, compatibility, maintainability, operations.
3. Classify findings by severity.
4. Issue verdict: APPROVE, NEEDS_CHANGES, or BLOCKED.
5. Provide re-review checklist.

## Required Artifacts

- Verdict
- Evidence reviewed
- Critical/high/medium/low findings
- Required fixes
- Nice-to-haves
- Re-review checklist

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- No approval if behavior changed without tests.
- No approval if docs were required but missing.
- No file edits in reviewer mode.
- Do not confuse transport failure with review verdict.

## Dispatch Skeleton

```text
Lane: agy-as-reviewer
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
