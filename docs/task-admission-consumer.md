# One-shot dashboard task-admission consumer

This consumer is a separate process from the HTTP gateway. The admission request remains launch-free. One invocation claims and processes at most one `dashboard.task.admitted.v1` row.

## Safety contract

Before invoking a launcher, `TaskAdmissionConsumer`:

1. starts `BEGIN IMMEDIATE` and atomically claims one pending row;
2. acquires singleton writer slot `1` with a bounded renewable lease;
3. binds a unique claim ID and monotonically increasing attempt count;
4. verifies the immutable admission row, outbox envelope, payload digest, event ID, actor, task ID, writer cap, and topic;
5. reloads the owner-only admission policy;
6. verifies the allowlisted producer and canonical worktree;
7. verifies clean and stable Git HEAD/tree/status before and after descriptor-relative task-file hashing;
8. repeats complete tuple revalidation immediately before launch;
9. appends immutable lifecycle events for claim, validation, launch start, completion, failure, and recovery;
10. calls the launcher with the stable outbox event ID as its required idempotency key.

The consumer maintains a lease heartbeat during a long launch and proves lease ownership once more before recording completion. A second consumer cannot claim any task while writer slot `1` has an unexpired lease.

## Crash recovery and idempotency

If a process stops after claiming a row, a later one-shot invocation may recover it only after the lease expires. Recovery creates a new claim ID and increments the attempt count, while the launcher idempotency key remains the original immutable event ID.

A crash can occur after a launcher accepted work but before the consumer persisted the receipt. Therefore the configured launcher **must** durably deduplicate the stable event ID and return the same logical launch on replay. A launcher that cannot honor this contract is not eligible for production configuration.

Validation failures are terminal and leave the outbox row `failed`. Launcher/receipt/heartbeat failures are retryable and return the outbox row to `pending`. The singleton lease is released in either explicit failure path. Unexpected process death relies on lease expiry and recovery.

## Launcher protocol

The owner-only launcher configuration is strict JSON:

```json
{
  "version": 1,
  "producers": {
    "agy-pnv6": {
      "command": ["/absolute/canonical/executable", "arg1"],
      "timeout_seconds": 300
    }
  }
}
```

The executable must be an absolute canonical regular executable and must not be group/world writable. The consumer uses no shell. It writes one bounded JSON request to stdin and expects exactly:

```json
{
  "accepted": true,
  "idempotency_key": "task-admission:<stable digest>",
  "launch_id": "durable provider launch identifier"
}
```

The receipt has no extension keys. Output is limited to 64 KiB. Only the strict receipt is persisted; lifecycle rows retain hashes rather than arbitrary launcher detail.

## One-shot invocation

```text
python -m prismatic.task_admission_consumer \
  --db /absolute/owner-only/event_log.sqlite \
  --policy /absolute/owner-only/task-admission.json \
  --launcher-config /absolute/owner-only/task-admission-launchers.json \
  --identity prismatic-task-admission-consumer@host \
  --lease-seconds 300
```

An idle invocation prints `{"status": "idle"}` and exits successfully. It does not poll or schedule itself.

## Deployment gate

Source, merge, and release acceptance do not authorize deployment. Before enabling a timer or invoking the consumer against a real admission:

- independently review the exact consumer candidate;
- deploy only from a detached verified release;
- use a fixture admission and an idempotent no-op launcher first;
- prove atomic claim, cap-one behavior, lease heartbeat, crash recovery, stable replay, immutable lifecycle, and no legacy event consumption;
- keep GRO-4210 unadmitted and active producers at zero until that live fixture proof passes.
