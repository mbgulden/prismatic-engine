# Research Queue Processing Pattern

## When to Use
When the Linear backlog is clear (or all remaining items require human input / physical hardware / dashboard access), pivot to the research queue at `~/work/research/queue.json`.

## Workflow

### Step 1: Check What Exists
Before processing ANY queue items, verify which outputs already exist on the REAL filesystem:

```python
import os
base = "/home/ubuntu/work/research"
# Check each item's output path
for item in queue["queue"]:
    path = os.path.expanduser(item["output"])
    exists = os.path.exists(path)
    # Only process items that are missing
```

**CRITICAL**: Do NOT use `execute_code` for this check — the sandbox resolves `~` to `~/.hermes/profiles/orchestrator/home/`, not the real `/home/ubuntu/`. Use `terminal()` with a Python inline script OR use absolute paths in `execute_code` (`/home/ubuntu/work/research/...`).

### Step 2: Batch by Priority
Queue domain priority: `hd-engine` > `active-oahu-tours` > `ai-consulting` > `hermes-infra`

Group missing items by domain, then launch parallel subagents (up to 3 at a time) using `delegate_task`:

```python
# Launch 2-3 parallel subagents, each handling one research item
delegate_task(tasks=[
    {"goal": "...", "context": "...", "toolsets": ["web", "terminal", "file"]},
    {"goal": "...", "context": "...", "toolsets": ["web", "terminal", "file"]},
    {"goal": "...", "context": "...", "toolsets": ["web", "terminal", "file"]},
])
```

### Step 3: Subagent Instructions
Each subagent task should include:
- **Goal**: One sentence describing the deliverable
- **Context**: Full details — output path, format requirements, data sources, tone/style
- **Output path**: Always absolute (`/home/ubuntu/work/research/...`)
- **Toolsets**: `["web", "terminal", "file"]` for research tasks (web for research, terminal for curl/scripting, file for output)

### Step 4: Verify Outputs
After subagents complete, verify each output file exists on the real filesystem with reasonable size:

```python
for path in expected_outputs:
    if os.path.exists(path) and os.path.getsize(path) > 100:
        print(f"✅ {path}")
    else:
        print(f"❌ {path} — missing or too small")
```

Subagents report success but may write to wrong paths or produce empty files. Always verify.

### Step 5: Update Queue
Rewrite `queue.json`:
- Move completed items to the `completed` array with timestamp and summary
- Remove completed items from `queue` array
- Update `_last_updated` timestamp

## Parallelism Strategy
- **Wave 1**: 2-3 highest-priority items (one per domain if possible)
- **Wave 2**: Next 2-3 items
- Don't run more than 3 subagents concurrently (configured limit)
- Each subagent typically takes 2-7 minutes for web research tasks
- Total processing for 4 items with 2 waves: ~8-15 minutes

## Pitfalls
- **Sandbox path isolation**: `execute_code` sees `~/.hermes/profiles/orchestrator/home/`, not `/home/ubuntu/`. Use absolute paths or `terminal()` for filesystem checks.
- **Subagent output not verified**: Subagents report "completed" even if files are empty or written to wrong paths. Always verify with `os.path.getsize()`.
- **Queue items already completed by prior run**: The cron runs nightly — prior sessions may have completed items. Check file existence before processing.
- **Domain priority**: Don't process `hermes-infra` items when `hd-engine` items are still pending. Revenue-first.
