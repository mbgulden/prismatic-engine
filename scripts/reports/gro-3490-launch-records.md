# GRO-3490 — Launch records and sandbox handles

Ned implementation note for the Prismatic dispatcher launch ledger.

## Behavior

Every subprocess-backed worker launch now writes a durable row to the `launch_records` SQLite table before the dispatcher reports the launch as successful.

Each row includes:

- `run_id` — globally unique launch identifier.
- `issue_id` / `identifier` — Linear UUID and human identifier when available.
- `agent_name` and `pid` — worker lane and spawned process handle.
- `command_json` — exact argv used for the launch.
- `handle_type` / `handle` — durable execution handle, preferring sandbox path, then worktree path, then JSON execution context.
- `sandbox_path`, `worktree_path`, `branch`, `execution_context` — trace fields for later recovery.
- `labels_json`, `cycle_id`, `request_id`, `created_at`, `status` — routing context and uniqueness metadata.

## Configuration

The ledger defaults to the dispatcher state database (`event_router.db`) and can be redirected with:

```bash
PRISMATIC_LAUNCH_RECORDS_DB_PATH=/path/to/event_router.db
```

Optional trace overrides:

```bash
PRISMATIC_SANDBOX_PATH=/tmp/agy_sandboxes/GRO-XXXX
PRISMATIC_WORKTREE_PATH=/tmp/prismatic-worktrees/GRO-XXXX
PRISMATIC_BRANCH=ned/GRO-XXXX
```

## Verification

Focused tests live at `prismatic/test_launch_records.py` and verify:

1. two calls produce two unique `run_id` values for the same issue;
2. sandbox handles are persisted when supplied;
3. `launch_agy()` persists pid, argv, worktree handle, cycle id, request id, and labels.
