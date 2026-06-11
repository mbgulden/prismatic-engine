# Autonomous Worker Cron Pattern (Ned)

## When to Use
When the orchestrator (Fred) is the only agent handling both chat AND task execution, creating a bottleneck. Michael wants to talk to Fred while tasks get done in the background. The solution: a dedicated worker cron that autonomously processes `agent:fred` Linear issues.

## Architecture

```
Michael → Fred (chat orchestration, strategy, conversation)
         └─ Ned (autonomous execution cron, every 5 min)
              ├─ Queries Linear for agent:fred Todo issues
              ├─ Processes one issue per run
              ├─ Reports completion to Telegram
              └─ Self-learns (creates skills from completed work)
```

## Setup Recipe

### 1. Create the cron job

```python
cronjob(action='create',
    name='Ned — Autonomous agent:fred task executor',
    schedule='every 5m',
    deliver='telegram:<chat_id>',
    skills=['all-skills'],  # Inherit ALL orchestrator skills
    prompt='''You are Ned, Michael's autonomous task execution agent...

## Self-Learning Rule
After completing any complex task (5+ tool calls, or something you figured out
that wasn't obvious), use skill_manage(action='create') to save the approach
as a new skill. Ned gets smarter every run.

## Process
1. Query Linear for oldest Todo issue with label agent:fred
2. If none found, query Backlog, move oldest to Todo, then execute
3. If still none, respond with [SILENT]
4. **Pre-verify artifacts** — check project repo for existing deliverable files matching the issue
5. If pre-existing: post \"pre-completed\" comment, skip to step 7
6. If new: post \"Ned executing this\" comment, load relevant skills, read issue + repo files, execute autonomously
7. Commit and push to GitHub
8. **Transition label from `agent:fred` to `agent:done`**
9. Move issue to Done state
10. Post summary comment with commit hash
11. Report what was built''')
```

### 2. Queue tasks

Label issues with `agent:fred` and move to Todo. The worker picks them up automatically.

```python
# Linear API: add agent:fred label + move to Todo
issueUpdate(id='...', input={
    stateId: '<TODO_STATE_ID>',
    labelIds: ['<AGENT_FRED_LABEL_ID>']
})
```

### 3. Verify

Check the worker's delivery to Telegram. First run may take 5 minutes. Manually trigger with `cronjob(action='run', job_id='...')` to test immediately.

## Key Design Decisions

### Why a cron job, not a separate Hermes profile?
- Simpler: no new profile, gateway, port, or .env to manage
- Same model: inherits orchestrator's deepseek-v4-pro
- Same tools: terminal, file, web, search, skills, delegation
- Same skills: symlinked or explicitly listed
- Delivery: reports to Michael's Telegram directly

### Why every 5 minutes?
- Fast enough to feel responsive
- Slow enough to avoid quota issues
- Matches the nudge executor cadence

### Self-learning
The worker MUST have `skill_manage` capability. After completing complex tasks, it creates new skills — the library grows autonomously.

## Completion Steps (full cycle)

1. Query Linear for oldest `agent:fred` issue in Todo
2. **Pre-verify artifacts** — check whether deliverable files already exist in the project repo. **First: `git log --oneline | grep <ISSUE-IDENTIFIER>`** — if the issue number appears in a commit message, the work may already be done. Use `git show <hash> --stat` to verify the commit covers the issue's requirements before deciding it's pre-completed.
3. Post \"Ned executing this\" comment (only if no pre-existing work found)
4. Execute fully — read files, write code, run commands
5. Commit and push to GitHub
6. **Transition the `agent:fred` label to `agent:done`** — prevents confusion for systems that scan by label rather than state
7. Move issue to Done state
8. Post summary comment with commit hash and what was built
9. Seed next Backlog item to Todo (if any) so the next tick has work

⚠️ Some cron prompts may omit the label transition (step 6) — always do it even if not explicitly listed. An issue in Done with `agent:fred` still looks active to label-based scanners.

## Pitfalls

- Worker runs in a fresh session each tick — no memory of previous runs. Keep context in Linear comments and repo files.
- `[SILENT]` is the correct response when no work exists — suppresses delivery
- Skip `requires:human-approval` issues — those need Michael
- One issue per run. Don't batch.
- The cron job's model is pinned at creation time. If changing orchestrator models, update the cron.
- **Label transition omission** — moving to Done without changing `agent:fred` → `agent:done` leaves the issue visible to label-based scanners. Always transition the label as part of completion.
- **Backlog blindness — when Todo is empty, check Backlog.** Ned scopes to `agent:fred` in Todo state. If all such issues are in Backlog, Ned cycles silently doing nothing. The prompt MUST include a fallback: if 0 Todo results, query Backlog, move the oldest to Todo, and execute it. Without this, the worker looks healthy (`last_status: ok`) but produces zero output.
- **Pre-verify before posting "executing" comment.** Backlog items have higher pre-completion probability (they sit longer; another session may have done the work without updating Linear). Always check existing deliverable files in the project repo BEFORE posting "Ned executing this." If work is already done, post "pre-completed work detected" instead, move to Done, and seed the next item.
- **Git log is the fastest pre-verification.** `git log --oneline | grep GRO-NNN` tells you in one command whether the issue was already committed. GRO-1009 (June 2026): a 297-line commit implemented all ending logic but the Linear issue stayed in Todo. Caught by git log, moved to Done immediately.
