---
name: prismatic-agy-execution
description: Use this skill when preparing, launching, monitoring, recovering, or reviewing an Antigravity/AGY run through Prismatic Engine's canonical admitted workflow.
---

# Canonical Prismatic AGY Execution

## Bundled workflow templates

Copy and complete these relative templates instead of inventing an unbounded prompt or result format:

- [`templates/task.md`](templates/task.md) — frozen task identity, scope, authority, and acceptance criteria;
- [`templates/implementation-plan.md`](templates/implementation-plan.md) — plan-before-edit, preservation, risk, and verification ladder;
- [`templates/result.md`](templates/result.md) — durable evidence packet with exact non-claims.

Keep completed task/plan/result artifacts under the approved workspace or artifact boundary and bind them into the admitted run.

## Platform prerequisites

The canonical supervised runtime currently requires Linux (or a Linux container/WSL environment), `/proc`, `prctl` descendant containment, and `tmux`. It also requires an immutable reviewed AGY executable and a governed admission receipt. On an unsupported host, use the bundle for planning/review, report runtime execution as blocked, and do not substitute raw `agy`, detached processes, or an uncontained launcher.

## Required sequence

1. Inspect the contract:
   ```bash
   prismatic agy contract
   ```
2. Prepare a bounded workspace and a detailed task file. Record its digest.
3. Define separate plan, result, stdout, stderr, and diagnostic paths beneath the approved artifact boundary.
4. Use `prismatic agy render --help` and render the exact launch specification. Bind the task, workspace, model, AGY home, executable path, executable digest, and output paths.
5. Obtain the exact Prismatic admission receipt through the governed admission path. Never synthesize or reuse one.
6. Launch only through `prismatic agy launch ... --admission-receipt ... --runtime-dir ... --execute`.
7. Preserve the launch receipt and monitor the exact run through durable process/activity evidence.
8. Use `prismatic agy wait --receipt ...` to book-end the run without imposing a Prismatic wall-clock kill.
9. Validate the durable result marker, logs, changed paths, verification results, and commit/tree before requesting independent review.

## Runtime behavior

- A quiet filesystem is not proof of inactivity. CPU, I/O, logs, descendants, and artifacts are separate signals.
- Do not auto-kill based on elapsed time or silence. Explicit cancellation must target the exact admitted run.
- After cleanup, every exact PID/start-tick descendant must be gone before the slot is released or retried.
- A vanished transport, digest drift, missing marker, or incomplete evidence blocks acceptance and preserves artifacts for diagnosis.

## Authority boundary

The AGY process may implement and verify within its bounded workspace. It must not merge, deploy, restart services, mutate external trackers, or expand concurrency unless the exact task carries a separate governed authorization for that action.
