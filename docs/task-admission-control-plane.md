# Dashboard task-admission control plane

The task-admission API records authenticated, exact operator intent without polling Linear and without launching a producer in the browser request.

## Routes

All three routes require a control credential carrying the `operator` role, including GET readback:

- `POST /api/dashboard/task-admissions`
- `GET /api/dashboard/task-admissions?limit=50`
- `GET /api/dashboard/task-admissions/{task_id}`

The POST requires an operator authorization header, `Content-Type: application/json`, and an `Idempotency-Key` header exactly matching the body field.

## Policy configuration

Set `PRISMATIC_TASK_ADMISSION_POLICY_FILE` to an owner-readable JSON file with mode `0600` (no group/other permissions):

```json
{
  "worktrees": ["/absolute/canonical/task/worktree"],
  "producers": ["agy-pnv6"],
  "max_age_seconds": 300
}
```

The admission database uses `PRISMATIC_BUS_DB` so the ledger and outbox live beside the canonical event bus. The database, WAL, and shared-memory files must be owner-owned regular files with mode `0600`; unsafe permissions, path aliases, and symlinks fail closed. Admission uses dedicated tables; it does not publish into the legacy generic `events` table because current consumers would mark unknown topics processed. Authenticated readback remains available if the admission policy file is temporarily absent; new admissions do not.

## Durable transaction

A successful first request atomically inserts:

1. immutable admission identity and canonical payload;
2. one pending `dashboard.task.admitted.v1` outbox record;
3. one append-only audit record.

Exact replay returns the original record with HTTP 200 and creates no second outbox event. Conflicting idempotency keys or duplicate task IDs return HTTP 409. Storage failures roll back the complete transaction.

## Validation boundary

Admission fails closed unless:

- the JSON schema is exact and contains no duplicate/unknown fields;
- task ID, commit, tree, SHA-256, timestamp, and idempotency formats are valid;
- writer cap is integer `1` (not boolean);
- producer and canonical worktree are allowlisted;
- the worktree resolves to the exact commit and tree, has no tracked-file changes, and remains on the same snapshot before and after task-file hashing;
- the relative task file is opened descriptor-relatively with no-follow semantics, is a regular file no larger than 1 MiB, remains unchanged while it is hashed in bounded chunks, and matches its SHA-256;
- the request timestamp is within the configured freshness window;
- authenticated actor identity comes from middleware, never the body.

The ledger never stores bearer tokens, authorization headers, task contents, environment values, or Git command output.

## Dashboard behavior

The dashboard Task Admission panel collects exact fields and a transient password-type bearer input. The token is used only for the immediate POST/readback request and is cleared in `finally`. It is never stored in browser storage, URLs, rendered proof, or telemetry.

The confirmation explicitly states: **Records durable intent only; does not launch a producer.**

## Deliberate non-claims

This slice does not:

- consume or claim pending outbox rows;
- launch a producer;
- create a worktree;
- contact Linear;
- poll Telegram or any task manager;
- deploy or restart the gateway.

A later consumer slice must transactionally claim one outbox row, revalidate every exact binding, enforce one writer lease, and record lifecycle in a separate append-only table before launch. It must not rewrite the immutable admission row.
