# Agent Run Records System

**Location**: `agentic-swarm-ops/` — files under the project repo root

## Why It Exists

Built by the nudge executor (GRO-668, June 7 2026) to create a durable audit trail of every agent execution instance. Answers "what happened while Michael was away?"

## Files

| File | Purpose |
|------|---------|
| `schemas/agent-run-record-schema.json` | JSON Schema defining the run record format |
| `ops/agent-run-records/run_records.py` | Python module + CLI for create/read/update/list |
| `docs/agent-run-records.md` | Full documentation with CLI usage, Python API, integration patterns |
| `agent-runs/<agent>-<timestamp>-<shortid>.json` | One JSON file per agent run |

## Record Structure

Each record tracks: `run_id`, `agent`, `task_id`, `title`, source (system + reference), `started_at`, `completed_at`, `state` (pending/running/completed/failed/timed_out/cancelled), `status_detail`, `artifacts`, `trigger_type`, `duration_seconds`, `failure_count`, `metadata`.

## CLI Usage (from `agentic-swarm-ops/`)

```bash
python3 ops/agent-run-records/run_records.py start \
  --agent hermes \
  --task "GRO-XXX" \
  --title "What I'm doing" \
  --source linear \
  --ref "GRO-XXX: title" \
  --trigger nudge

python3 ops/agent-run-records/run_records.py complete <run_id> \
  --status completed \
  --detail "Summary of what was done"
```

## Python API (for integration in scripts)

```python
from ops.agent_run_records.run_records import create_run, complete_run

record = create_run(agent="hermes", task_id="GRO-XXX", title="...",
                    source_system="dispatcher", source_reference="...",
                    trigger_type="dispatcher")

# ... do the work ...

complete_run(record["run_id"], status="completed", status_detail="Done",
             artifacts=[{"path": "relative/path", "type": "file"}])
```

## When the Nudge Executor Should Use This

- **Always**: Record your own run when processing a nudge trigger. This creates a self-documenting audit trail.
- **At start**: Call `create_run()` after accepting the trigger file
- **At completion**: Call `complete_run()` with the final status and artifact paths
- **This run record becomes discoverable** by future nudge executors via Step 0.5 pre-verification if they search for the topic

**Important**: The Linear comment on the source issue and the run record file serve DIFFERENT purposes. The comment is human-readable pipeline state. The run record is machine-readable execution data for aggregation, failure analysis, and throughput reporting. Do both — they complement each other.
