# Parallel delegate_task for Multi-Deliverable Synthesis

## When to Use

When a task requires producing **multiple independent deliverables** (reports, design docs, specs) that all draw from the **same set of source documents**, use `delegate_task` with the `tasks` array (batch/parallel mode) to write them simultaneously.

## Pattern

1. Read all source documents into the orchestrator's context (or use `search_files` to verify they exist)
2. Construct a `tasks` array — one task per deliverable, each with:
   - `goal`: "Write X deliverable for ISSUE-ID. Save to /path/to/output.md."
   - `context`: A compact summary of the specific sections from each source document relevant to THIS deliverable. Include enough that the subagent doesn't need to re-read everything, but not the full text.
   - `toolsets: ["file", "terminal"]` — file I/O and verification only
3. All subagents run in parallel — no shared mutable state, no conflicts
4. After they return, verify each output file exists and has reasonable size (`wc -l`)

## Why It Works

- **No shared state**: each subagent writes to a different output file
- **Same source context**: each gets a condensed version of the same source docs
- **Independent reasoning**: each subagent reasons about its own section of the problem
- **Linear speedup**: N deliverables in ~1/N wall time vs serial

## Example: GRO-820 (Jun 2026)

Three deliverables (Capability Registry, Alchemy Mode, Instance Scheduler) all drawing from the same 5 source documents:

```
Source docs (read by orchestrator):
  1. alchemy-mode-fractal-complexity.md (Kai)
  2. agy-core-boundary-validation.md (AGY)
  3. agy-claude-code-build-pattern.md (AGY)
  4. agy-implementation-plan.md (AGY)
  5. dispatcher.py (Fred)

Subagent 1: Capability Registry Design (820 lines)
Subagent 2: Alchemy Mode Design (1,130 lines)
Subagent 3: Instance Scheduler Design (1,060 lines)

Total: 3,010 lines in ~4.5 minutes wall time
```

## Pitfalls

- **Don't make subagents re-read source docs**: pass condensed context in each task's `context` field. Subagents with `toolsets: ["file", "terminal"]` can read files, but it duplicates I/O across all 3.
- **Verify output sizes**: subagents self-report success but their output may be truncated or wrong. Always `wc -l` each file after they return.
- **Don't use for sequentially-dependent deliverables**: if Deliverable B depends on Deliverable A's conclusions, write A first in serial, then B. This pattern is for independent deliverables only.
- **Cap at 3 tasks**: the `max_concurrent_children` setting limits parallel subagents. If you have 5+ deliverables, batch them in groups of 3.
