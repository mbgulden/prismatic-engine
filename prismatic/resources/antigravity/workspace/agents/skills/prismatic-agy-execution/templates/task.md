# Prismatic AGY Task

## Identity

- Task ID: `${TASK_ID}`
- Workspace: `${WORKSPACE}`
- Candidate branch/worktree: `${CANDIDATE_WORKTREE}`
- Result path: `${RESULT_PATH}`

## Objective

`${OBJECTIVE}`

## Allowed scope

- `${ALLOWED_PATH_OR_ACTION}`

## Preserve

- Existing good product surfaces and user work.
- Unrelated dirty state, branches, worktrees, and runtime artifacts.

## Forbidden without explicit authority

- Merge, deployment, restart, release, or production mutation.
- Direct Linear/GitHub/external messaging writes.
- Secret, OAuth, model-account, or global configuration changes.
- Broad resets, unrelated cleanup, or concurrency changes.

## Acceptance criteria

- `${ACCEPTANCE_CRITERION}`

## Verification contract

- Focused commands: `${FOCUSED_COMMANDS}`
- Canonical command: `${CANONICAL_COMMAND}`
- Required independent review: `${REVIEW_REQUIREMENT}`
- Evidence directory: `${EVIDENCE_PATH}`

## Completion boundary

Producer completion is not acceptance. Write the durable result at `${RESULT_PATH}` and leave merge/deploy/publication decisions to the governed reviewer/operator.
