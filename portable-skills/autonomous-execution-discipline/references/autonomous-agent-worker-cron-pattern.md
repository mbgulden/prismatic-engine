# Autonomous Agent Worker Cron Pattern (June 8, 2026)

## Problem
The orchestrator (Fred) is blocked during chat — no work gets done between messages. Tasks pile up.

## Solution
Create a dedicated cron job (Ned) that picks up `agent:fred` Linear issues autonomously.

## Setup

### 1. Create the cron job
```
cronjob(action='create', 
  name='Ned — Autonomous agent:fred task executor',
  schedule='every 5m',
  deliver='telegram:8190664947',
  skills=[ALL_ORCHESTRATOR_SKILLS])
```

### 2. Prompt Design
The prompt must be self-contained — the cron runs in a fresh session with no chat context.

Key elements:
- Identity: "You are Ned, equal to Fred, autonomous executor"
- Self-learning: use memory + skill_manage to grow over time
- Process: query Linear → pick oldest agent:fred Todo → execute → report → mark Done
- Silent exit: respond `[SILENT]` (exact text) when no tasks found
- Skip issues with `requires:human-approval`
- Include all project repos with absolute paths

### 3. Label Assignment
After creating Ned, assign `agent:fred` labels to Todo tasks:
```python
# Linear API: add agent:fred label + move to Todo
mutation { issueUpdate(id: "...", input: { labelIds: [...], stateId: "Todo" }) }
```

### 4. Verification
- Check `cronjob(action='list')` — Ned should appear with `last_status: ok`
- Manual trigger: `cronjob(action='run', job_id='10a73725c5cc')`
- Monitor: messages should appear in target Telegram chat within 5 minutes

## Naming Convention
- Fred = chat orchestrator (strategy, conversation)
- Ned = autonomous executor (picks up tasks, builds, deploys)
- Kai = Active Oahu Tours content
- AGY = research, design, file analysis
- Jules = async GitHub PR work

## Pitfalls
- Cron runs in fresh context — no memory of prior runs unless saved to memory tool
- Must explicitly tell worker to read daily journals for context
- One issue per run to keep messages concise
- Deliver to a dedicated chat/bot if output volume grows
