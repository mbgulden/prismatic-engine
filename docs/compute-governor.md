# Distributed compute governor

The distributed compute governor prevents multiple dispatcher processes from
over-scheduling scarce execution agents such as AGY, Jules, and Codex.

## State file

By default allocations are stored at:

```text
${PRISMATIC_HOME:-~}/.antigravity/agent_status.json
```

Pass an explicit `status_path` to `DistributedComputeGovernor` in tests or custom
embedders. A sidecar lock file serializes cross-process writes:

```text
agent_status.json.lock
```

The lock is acquired with atomic `O_EXCL` creation and stale lock files are
removed after the governor's `lock_ttl_seconds` window.

## Allocation lifecycle

Dispatcher launchers now follow this lifecycle:

1. Prune stale allocations using `PRISMATIC_GOVERNOR_TTL_SECONDS` at the
   beginning of each dispatch cycle so crash leftovers do not starve the
   current cycle.
2. Check the agent's current active allocations.
3. Refuse launch if the agent is at `max_concurrent`, or if the same
   agent/task allocation is already active.
4. Launch the subprocess.
5. Store the process PID in the allocation.
6. Heartbeat live child processes during dispatch cycles.
7. Release allocations when child processes exit.

The default capacity is one concurrent task for Jules/Codex and two for AGY,
unless the agent config provides `max_concurrent`.

## Failure behavior

- If allocation acquisition fails because capacity is full or the same
  agent/task allocation is already active, the dispatcher defers the task and
  returns `None` from the launcher. The task is not marked as successfully
  dispatched.
- If process launch fails after allocation, the launcher releases the allocation.
- If PID tracking fails after `Popen` succeeds, the launcher terminates the child
  process, waits briefly, escalates to `kill()` if the child ignores SIGTERM,
  releases the allocation, and returns `None` instead of leaking an untracked
  process.
- If stale AGY cleanup kills a process, the governor releases allocations for
  that PID.
- If a dispatcher crashes, the next cycle can prune the stale heartbeat.

## Verification

Focused regression coverage lives in:

```text
tests/test_governor.py
```

The tests cover:

- acquire/release and duplicate same-task launch deferral
- capacity limits
- stale/invalid heartbeat pruning
- PID-based release
- simultaneous multi-process acquisition contention
- dispatcher active-process heartbeat/release behavior
- post-`Popen` tracking failure cleanup, including SIGTERM-resistant child
  force-kill behavior
- dispatch-cycle prune-before-launch ordering
