---
name: prismatic-context-discipline
description: Use this skill when shaping AGY task context, prompts, plans, and handoffs so long-running Prismatic work remains bounded, durable, and recoverable without context bloat.
---

# Prismatic Context Discipline

## Shape the task

- Put detailed requirements in a durable task file, not an oversized inline prompt.
- Keep the inline goal short: task identifier, task-file path, workspace, plan path, result path, evidence contract, and prohibited actions.
- Hash-bind the task file and launch specification before admission.
- Separate facts, requirements, assumptions, risks, and non-claims.

## Plan before edits

For code changes, write the implementation plan before modifying files. The plan should include:

1. current architecture and reuse points;
2. exact path scope;
3. security and authority boundaries;
4. successful and fail-closed paths;
5. focused and canonical verification;
6. packaging/installed-distribution implications;
7. rollback and handoff evidence.

## Progressive disclosure

- Read only the rule, skill, reference, or source file needed for the current decision.
- Keep verbose command output in durable logs and summarize only high-signal proof.
- When context is incomplete, inspect the canonical source instead of filling gaps from memory.
- Preserve continuation pointers: task digest, head/tree, plan/result paths, launch receipt, logs, and the exact next gate.

## Avoid context traps

Do not spend the implementation window repeatedly researching already-settled architecture. Once the contract and acceptance tests are explicit, build and verify. New evidence may revise the plan; speculative expansion may not silently expand scope.
