---
name: prismatic-engine-operations
description: Use this skill when operating, diagnosing, or changing Prismatic Engine with Antigravity/AGY; it defines the repository front doors, authority boundaries, and evidence-first workflow.
---

# Prismatic Engine Operations

## Start here

1. Read repository `AGENTS.md` or `GEMINI.md` files and `.agents/rules/` before acting.
2. Run `prismatic status` for engine diagnostics.
3. Run `prismatic agy contract` before constructing or reviewing an AGY launch.
4. Read the exact task file and inspect the current Git branch, status, head, and worktree ownership.
5. State the intended scope, prohibited external actions, acceptance commands, and non-claims.

## Operating contract

- Prismatic Core owns admission, lifecycle, governance, durable evidence, provider projection, and release gates.
- AGY is a provider/runtime. It does not own merge, deploy, Linear/GitHub mutation, financial/public sends, or concurrency changes.
- New work enters through the approved dashboard/event-queue contract. Do not replace event-driven control with polling or direct-launch shortcuts.
- Preserve existing dashboard adapters and good product assets. Port paths deliberately; never blind-reset or rebuild a fallback as the primary surface.

## Completion

A producer result is evidence, not acceptance. Require:

- exact files changed;
- exact commands and outcomes;
- durable log paths;
- commit/tree identity when applicable;
- explicit boundaries and non-claims;
- independent exact-artifact review for governed work.

If any required proof is missing, report `PARTIAL` or `BLOCKED`, not done.
