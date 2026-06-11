# Dispatcher Comment Spam — Silent Executor Failure Loop Detection

## The Problem

When the nudge executor enters a silent retry loop, it produces identical dispatcher routing comments on the Linear issue but never posts agent output, never updates labels, and never deletes the trigger file. The trigger file survives → next cron cycle re-reads it → dispatcher re-routes → another "routed to" comment → no agent output → loop.

The user sees no execution happening. The Linear issue accumulates noise. The trigger file never gets cleaned up.

## The Canonical Examples

### GRO-151 (archived) — Pre-completed work, silent failure loop

GRO-151 (Smart Lock IoT Bridge MVP) accumulated **47 identical comments** (`"📡 Dispatcher: task GRO-151 routed to Fred."`) with zero agent output between them. The nudge executor had been running in a silent failure loop for an unknown number of cycles.

**Why it failed silently for 47 cycles:**
- The executor's Step 0.5 search_files spread across large directories likely timed out
- Or the executor crashed after posting a completion comment but BEFORE deleting the trigger file
- The trigger file survived → next cycle re-reads → dispatcher re-routes → another comment → repeat

**What the nudge executor did correctly on the 48th cycle (Jun 7, 2026):**
1. Read the trigger file
2. Queried Linear for the issue — issue was found (not archived)
3. Step 0.5 pre-verified: all artifacts existed on disk (14 files at `~/work/agentic-swarm-ops/docs/sovereign-sentinel/smart-lock-bridge/`)
4. Posted a completion comment documenting all pre-completed work
5. Removed `agent:fred` label, added `agent:done` label
6. Deleted `/tmp/trigger-fred-work`
7. Also checked for and cleaned up `/tmp/prismatic/nudge-*` files (none existed)

### GRO-666 — Implementing the fix, breaking the loop with `/agent` commands

GRO-666 ("Implement /agent comment commands in dispatcher") had **49 dispatcher routing comments** — the worst case yet. No execution had ever happened. The nudge executor broke the loop by delivering the issue's actual deliverable:

**What was different this time:** The issue title ("Implement /agent comment commands in dispatcher") described work the nudge executor could actually do — it wasn't waiting for AGY output or blocked on external resources. The executor:
1. Read the issue description referencing GRO-665 (swarm strategy)
2. Read GRO-665 for full context
3. Found and read the existing `agent_dispatcher.py` script (692 lines)
4. Implemented the `/agent` comment command system:
   - `_is_human_comment()` — bot-comment filtering
   - `_parse_agent_command()` — extract agent name + context from `/agent:<name>`
   - `process_agent_commands()` — scan all active issues, route via comment commands
5. Grew the script from 692 to 934 lines
6. Wired into `main()` before label-based dispatch
7. Verified Python syntax
8. Posted completion comment with full implementation summary
9. Transitioned `agent:fred` → `agent:done`, moved state to Done
10. Deleted all trigger files
11. Updated project registry

**Key insight:** The dispatcher that produced the 49 routing comments over 8+ hours is now the same dispatcher that processes `/agent` commands — resolving the root cause. The loop was broken by delivering the actual implementation the issue was created for.

**Validation:** After the fix, anyone can post `/agent:done` on a stuck issue and the dispatcher will close it on the next cycle. No trigger file needed. No nudge executor required.

## Detection Pattern

```python
# Pseudocode: when executing a nudge issue, check for silent loop
def detect_silent_failure_loop(issue_comments):
    dispatcher_comments = [c for c in issue_comments if "Dispatcher.*routed" in c.body]
    agent_output_comments = [c for c in issue_comments if not "Dispatcher" in c.body 
                                                          and c.body != ""
                                                          and "created" not in c.body.lower()]
    
    if len(dispatcher_comments) >= 5 and len(agent_output_comments) == 0:
        return True  # Silent failure loop detected
    return False
```

The heuristic: **5+ dispatcher routing comments with zero non-dispatcher, non-trivial comments = failure loop.**

## Root Causes

| Cause | Symptom | Fix |
|-------|---------|-----|
| Step 0.5 search_files timeout on large directory | Trigger file survives, no agent comment | Use targeted search first (research/ subdirs), fall back to broad search only if no results |
| Crash after comment post but before trigger file deletion | Agent comment EXISTS but trigger file still on disk | Double-verify trigger file deletion with `ls` after `rm` |
| `deliver: local` makes output invisible | Agent ran successfully every time but nobody saw it | Audit delivery routing: LLM-driven crons MUST deliver to `origin` |
| Model timeout (main model used for cheap cron) | Executor starts but never produces output | Nudge executor must use cheap/local model (deepseek-v4-flash or ollama-qwen:32b) |
| Trigger file format mismatch (prismatic vs legacy) | Dispatcher writes to path A, executor checks path B | Always delete BOTH `/tmp/trigger-fred-work` AND `/tmp/prismatic/nudge-*` |

## Prevention

When processing any nudge issue:

1. **Count dispatcher comments before doing work.** If >= 5 with no agent output, note it in your completion comment: "Breaking a 47-comment silent failure loop."
2. **Run Step 0.5 first.** Pre-verify artifacts before building anything. The prior N-1 cycles may have produced output that was never captured in Linear.
3. **Double-verify trigger file deletion.** After `rm -f /tmp/trigger-fred-work`, run `ls /tmp/trigger-fred-work 2>&1` and confirm "No such file or directory." Do this for ALL trigger file locations:
   ```bash
   rm -f /tmp/trigger-fred-work /tmp/prismatic/nudge-fred /tmp/prismatic/nudge-*
   ```
4. **Post a completion comment even for Case 2 (pre-completed work).** This marks the loop as broken so future cycles see an agent output comment and the heuristic doesn't trigger again.
5. **Label transition aggressively.** Remove `agent:fred` immediately and add `agent:done`. Don't leave the agent label in place for the dispatcher to re-route again.
6. **Adjacent issue scan (project-level pattern detection).** After cleaning up the loop, query the same project for OTHER issues with the same stuck pattern. Use:
   ```graphql
   query($project: String!) {
     project(id: $project) {
       issues(filter: {state: {name: {in: ["In Progress", "Todo", "Backlog"]}}}) {
         nodes { id identifier title state { name } labels { nodes { name } } comments { nodes { id body createdAt } } }
       }
     }
   }
   ```
   Then filter client-side for two patterns:
   - **`agent:done` stuck In Progress** — issues where the agent already finished but the Linear card was never moved to Done. This happens when a prior nudge executor completed the work and set `agent:done` but the state transition failed or was forgotten.
   - **Dispatched-but-no-output** — issues with 5+ dispatcher routing comments and zero agent output comments. These are parallel failure loops in the same project.
   
   For each match: post a breaking-the-loop comment, move to Done (if artifacts verified or agent:done already set), and update labels. The GRO-678 case (Jun 2026) is the canonical example: found during GRO-679 cleanup, 14 dispatcher comments with `agent:done` already set but stuck In Progress for 24+ hours. Phase 4 deliverable (GitHub repo `prismatic-engine`) was verified on disk and the issue was moved to Done.
   
   **Exception**: Skip issues that are clearly active work (recent agent output comments, In Progress with recent activity timestamps, or pipeline steps waiting on a different agent type).

## Pre-Dedup vs Post-Dedup Issues (Important Context)

The dispatcher dedup database (GRO-669, deployed Jun 2026) prevents new dispatcher spam loops from forming. The 4 canonical examples (GRO-151, GRO-666, GRO-678, GRO-679) all accumulated comments BEFORE the dedup DB was active. **New issues should not reach 5+ dispatcher comments.** If you find an issue with 5+ comments TODAY, it is a pre-dedup issue that was never cleaned up — treat it as a high-priority cleanup target.

**Corollary:** The adjacent issue scan (Step 6) is most valuable on projects where pre-dedup issues accumulated. The Prismatic Hub phases (GRO-670–680 range) are the primary set. Once those are cleared, the scan will return fewer results.

## Related

- `nudge-executor-pipeline-handoff.md` — Full pipeline handoff pattern
- `archived-issue-precompleted-work.md` — GRO-151 case study (archived issue, pre-completed work)
- `stale-archived-issue-handling.md` — Archive boundary handling
