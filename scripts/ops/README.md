# Scripts operations reports

This directory holds small operational probes and report generators that Ned can run without mutating product/content lanes.

## Label debt and stale queue drift

`label_debt_queue_drift.py` measures Linear queue hygiene:

- quantifies label debt by reason (`multiple_agent_labels`, `dispatch_ready_without_agent`, stale dispatchable work, terminal-state agent labels, and blocked dispatch labels),
- reports open issue counts by state and active agent label,
- appends JSONL snapshots with `--append-history` so repeated runs show drift from the previous snapshot.

Example:

```bash
python3 scripts/ops/label_debt_queue_drift.py --append-history
python3 scripts/ops/label_debt_queue_drift.py --json --history-path /tmp/prismatic_state/label_debt_queue_drift.jsonl
```

The default history path is `$PRISMATIC_STATE_DIR/label_debt_queue_drift.jsonl`, falling back to `/tmp/prismatic_state/label_debt_queue_drift.jsonl`.
