# Nudge Executor — Pipeline Handoff Pattern

## The Pattern

When the nudge executor (LLM-driven cron `c2cce4fec4ed`) picks up a task that is part of a **pipeline** (`pipeline:*` label), it must hand off to the next agent cleanly rather than closing the loop itself.

This is the bridge between the nudge system and the pipeline review rule.

## Pre-Step: Check for Pre-Completed Work by the Dead Agent

**Before doing any work**, verify if the target agent already completed the scope but never updated Linear state:

1. Read ALL comments on the issue
2. Look for a walkthrough comment with artifact paths
3. Check those paths exist on disk with `ls -la` + `wc -l`
4. Map issue deliverables → artifacts

If ALL deliverables are met: the work is pre-completed. Skip directly to Step 5 (sibling cleanup) and Step 6 (delete trigger). See `references/nudge-executor-precompleted-work-detection.md` for the full pattern with the GRO-751 case study.

**Common pitfall:** An issue in "Todo" does NOT mean the work is unstarted. Agents can complete work on disk and never update Linear state. Always pre-verify before building.

## Steps (after successfully completing your pipeline step)

| # | Action | Why |
|---|--------|-----|
| 1 | **Save deliverable artifacts** to a project-relative path (`plans/`, `docs/`, `schema/` under the project repo) | Artifacts persist across agent sessions. The plan doc is the deliverable even if the Linear comment gets buried. |
| 2 | **Post a comment** on the Linear issue with: (a) what was completed, (b) where the artifacts live, (c) what's next | The Linear issue is the single source of truth for pipeline state. Every agent chained into the pipeline reads the latest comment for context. |
| 3 | **Move the issue to In Progress** (not Done) — the step is done but the pipeline isn't | The issue stays visible in active queues. If you mark it Done prematurely, the next agent won't find it in their Todo list. |
| 4 | **Update the agent:* label** if needed | Clear labeling prevents the dispatcher from re-routing the same issue to the same agent. |
| 5 | **Clean up sibling issues** — query the team for Todo/Backlog issues with overlapping keywords. For each sibling whose scope is a subset of the completed original: comment "Superseded by GRO-XXX" and move to Won't Do. Skip unrelated issues. | After repeated dispatches to a dead agent, the dispatcher spawns new issues instead of re-routing the original. These become stale duplicates unless cleaned up. See `references/nudge-executor-sibling-cleanup.md`. |
| 6 | **Delete the trigger file** | If you don't delete `/tmp/trigger-fred-work`, the next cron tick re-processes the same issue. |

## Common Pitfalls

- ❌ **Marking issue Done after a pipeline step** — the issue is NOT done, just this step is. Move to In Progress so the next agent finds it.
- ❌ **Not saving artifacts to disk** — a comment-only deliverable is fragile. If the Linear issue gets archived or the comment is long, the next agent has to re-derive everything. Always save a file.
- ❌ **Omitting the "what's next" section in the comment** — each agent in the pipeline needs to know who picks up next.
- ❌ **Forgetting to delete the trigger file** — the trigger file is a filesystem semaphore; if it exists, every 5-minute cron tick re-runs the same task. Always `rm -f /tmp/trigger-fred-work` on success.
