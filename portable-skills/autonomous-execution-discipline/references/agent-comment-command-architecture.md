# /agent Comment Command Architecture

## What It Is

An alternative signal pathway in `agent_dispatcher.py` that lets users route Linear issues by posting `/agent:<name>` in a comment, rather than needing label-based routing. This makes the dispatcher bidirectional:

```
Label-based:   Issue created with agent:fred label → dispatcher routes → agent
Comment-based: User posts "/agent:fred" on any issue → dispatcher parses → routes
```

## Supported Commands

| Comment | Effect | Pipeline Next |
|---------|--------|---------------|
| `/agent:fred` | Route to Fred (orchestrator) | `agent:done` |
| `/agent:agy` | Route to AGY (research/design) | `agent:fred` |
| `/agent:jules` | Route to Jules (async PRs) | `agent:done` |
| `/agent:kai` | Route to Kai (Active Oahu) | `agent:done` |
| `/agent:codex` | Route to Codex (code review) | `agent:fred` |
| `/agent:autobot` | Route to Autobot (notifications) | `agent:done` |
| `/agent:done` | Mark issue as Done | — |

The optional context text after the command (e.g., `/agent:fred fix the nav`) is captured and included in the confirmation comment.

## Bot-Comment Filtering

Not every comment should trigger command parsing. The dispatcher filters out comments that start with known bot prefixes:

- `📡 Dispatcher:` — Standard routing notifications
- `🚨 AGY failed` / `⚠️ AGY stalled` — Stall recovery
- `## 🔍 Review:` / `## ✅ Review Complete:` — Pipeline reviews
- `Nudge Executor —` — Executor output
- `🤖 Pipeline` — Pipeline router
- `Dispatch complete:` — Log output
- Comments shorter than 5 characters

Comments written by humans (anything not matching these prefixes) are candidates for command parsing.

## Integration with Label-Based Dispatch

The `/agent` command processor runs **before** the standard label-based dispatch in the main loop:

```python
def main():
    # 1. Cleanup stale AGY
    # 2. Recover stalled AGY
    # 3. Process /agent commands  ← runs BEFORE label dispatch
    cmd_count = process_agent_commands()
    # 4. Initialize pipeline issues
    # 5. Standard label-based dispatch
```

This means a command posted in a comment can be processed on the same cycle — the comment is parsed, labels are updated, and then the standard label-based dispatch picks up the newly-labeled issue in the same run.

## How It Breaks the Dispatcher Comment Spam Loop

**Problem:** Issues with `agent:fred` label but no trigger file get "routed to Fred" comments every 15 minutes with no execution. The issue accumulates noise and nobody processes it.

**Fix with `/agent:done`:** A user (or the nudge executor) posts `/agent:done` on the issue. The dispatcher:
1. Parses the command on the next cycle
2. Strips all `agent:*` labels
3. Adds `agent:done` label
4. Moves the issue to the **Done** state
5. Posts a confirmation comment: "✅ Marked done via /agent:done command."

No more dispatcher routing. No more trigger files. The loop is broken definitively.

## Worked Example: GRO-666

GRO-666 ("Implement /agent comment commands in dispatcher") had **49 dispatcher routing comments** with zero agent output — the worst-case silent failure loop. The issue was complete when the nudge executor:

1. Detected the 49-comment loop (exceeded the 5-comment threshold)
2. Read the description: "Blueprint: NAS synology-agentic-context/agentic-swarm-ops/ docs"
3. Read GRO-665 for the full swarm strategy context
4. Implemented the `/agent` comment command system in `agent_dispatcher.py`:
   - 3 new functions: `_is_human_comment()`, `_parse_agent_command()`, `process_agent_commands()`
   - File grew from 692 to 934 lines
   - Wired into `main()` before label-based dispatch
5. Posted a completion comment with implementation summary
6. Transitioned `agent:fred` → `agent:done`
7. Moved issue state to Done
8. Deleted all trigger files
9. Updated project registry

The same dispatcher that was producing the spam became the tool that processes `/agent` commands — resolving the root cause of the loop.

## Key Code Pattern

```python
# In process_agent_commands():
# 1. Query all non-completed issues
# 2. For each issue, scan last 5 comments
# 3. For each comment (reverse order):
#    a. Skip if it matches DISPATCHER_PREFIXES (bot filter)
#    b. Parse for /agent:<name> pattern
#    c. If found:
#       - Strip all existing agent:* labels
#       - Add the target agent label
#       - Post confirmation comment
#       - Break (only first command per issue per cycle)

# Bot comment detection:
DISPATCHER_PREFIXES = [
    "📡 Dispatcher:",
    "🚨 AGY failed",
    "⚠️ AGY stalled",
    "## 🔍 Review:",
    "## ✅ Review Complete:",
    "Nudge Executor —",
    "🤖 Pipeline",
    "Dispatch complete:",
]

def _is_human_comment(body):
    # Stale check: dispatcher comments only
```

## Related

- `dispatcher-comment-spam-loop-detection.md` — Detection pattern for silent failure loops
- `agent-dispatcher-nudge-pattern.md` — The three-layer nudge architecture
- `nudge-executor-pipeline-handoff.md` — How the nudge executor processes issues
