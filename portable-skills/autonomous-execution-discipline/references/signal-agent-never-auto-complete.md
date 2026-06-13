# Signal-Polling Agent Rule: Route, Never Complete

## The Bug

Kai's `nudge_poller.py` (lines 252-267, June 2026) was auto-transitioning `agent:kai` → `agent:done` immediately upon receiving a nudge signal — before Kai ever touched the task. The poller was posting "✅ Kai completed processing" as its acknowledgment message.

**Root cause:** The poller conflated "signal received" with "work completed." It treated nudge acknowledgment as task execution.

## The Rule

**A signal-polling agent (nudge poller, cron worker, dispatcher) must NEVER transition its own agent label to Done.**

Signal agents have exactly one job: ROUTE work to the execution agent. They are not the execution agent.

| Action | Allowed? | Who does it |
|--------|----------|-------------|
| Read nudge signal | ✅ Signal agent | nudge_poller.py |
| Post acknowledgment comment | ✅ Signal agent | nudge_poller.py |
| Route to execution agent (keep label) | ✅ Signal agent | nudge_poller.py |
| Mark agent:done | ❌ NEVER | Only the execution agent after verification |
| Post "completed processing" | ❌ NEVER | Only the execution agent after actual work |

## The Fix Pattern

```python
# CORRECT — nudge poller routes but never completes
if LABEL_AGENT_KAI in label_names:
    # Signal routed — issue stays agent:kai for Kai to execute
    add_comment(issue_id, "📬 Signal received. Task queued for execution.")
    # DO NOT transition to agent:done. DO NOT post "completed."
else:
    add_comment(issue_id, "👀 Inspected. No agent label found.")

# WRONG — auto-transition steals execution from the agent
if LABEL_AGENT_KAI in label_names:
    transition_to_done(issue_id)  # NEVER DO THIS
    add_comment(issue_id, "✅ Completed processing")  # NEVER DO THIS
```

## Detection

Any `nudge_poller.py` or signal-polling script that contains BOTH:
- `agent:<target>` → `agent:done` transition
- A "completed processing" or similar confirmation message

...is auto-completing tasks without execution. This is a bug.

## Real Case

Kai reported: "The 5-min cron transitions agent:kai→agent:done when it processes a nudge. Tasks arrive marked Done without us ever touching them." The fix removed lines 252-267 of `/home/ubuntu/.hermes/profiles/kai/scripts/nudge_poller.py` — the auto-transition block. Kai now receives nudges with the issue remaining at `agent:kai` for explicit execution.
