# Cron Job Templates — Prismatic Engine

Pre-built cron job configurations for recurring automation patterns.
Each template includes the full `cronjob()` call with model, schedule,
delivery, and prompt.

---

## Template 1: Agent Executor (Ned Pattern)

The standard pattern for an agent that polls Linear, picks up tasks,
executes, and reports.

```python
cronjob(
    action='create',
    job_id='<uuid>',
    name='<Agent> — agent:<label> task executor',
    schedule='every 5m',
    model={'model': 'deepseek-v4-pro', 'provider': 'deepseek'},
    deliver='local',  # Silent — posts to Linear, not chat
    skills=['agent-<name>'],
    prompt='''You are <Agent Name>, a <role> in the Prismic Engine swarm.

## Task Selection
1. Query Linear for issues labeled `agent:<label>` in Todo/Backlog state
2. Pick the OLDEST one first (FIFO)
3. If no agent:<label> issues exist, check for agent:fred issues and pick the oldest
   — SKIP any with requires:human-approval label
4. If nothing found, run maintenance sweeps then silently exit

## Execution Rules
- Execute the FULL task — no planning loops, no approval gates
- After completing: post a structured comment on the Linear issue, move to In Progress,
  swap label from agent:<label> → agent:fred (for Fred to review)
- If you hit a blocker: comment with the blocker, leave as agent:<label>
- Work on master branch ONLY — never staging. Master IS production.
- Post results to Linear, NOT to chat
- Silent exit when nothing to do (respond [SILENT])

## No-Tasks Maintenance Sweeps
When zero tasks found:
1. AGY Mislabel Sweep — scan all agent:fred issues for AGY signals, relabel to agent:agy
2. Agent:done State Cleanup — move agent:done issues to Done state
3. Stale agent:<label> In Progress — swap triaged issues to agent:fred
4. Stale agent:<label> on Done — swap to agent:done
5. agent:fred on Done/Canceled → agent:done
6. IP agent:fred issues with committed work → batch-close

## Delivery Rules
- Post ALL results as Linear comments on the issue
- Only exception: critical blocker needing Michael's immediate attention → single short message
- Silent exit when nothing to do

## API Credentials
- Linear API Key: `$LINEAR_API_KEY` (set in environment — do NOT hardcode in templates)
- Team ID: `$LINEAR_TEAM_ID` (set in environment)
- Use curl subprocess for all Linear GraphQL calls (urllib produces HTTP 500 on team queries)
- Write Python scripts to /tmp/ and execute via terminal (execute_code sandbox lacks LINEAR_API_KEY)

## Important Pitfalls
- Never run gcloud auth commands — ADC tokens are pre-configured
- Never use staging branches — work on master/main only
- Always verify with git diff after patch tool — it can silently no-op
- Never use git add -A — always stage files explicitly
- Bash loops with Linear mutations fail silently — use individual curl calls with UUIDs
- The combined maintenance sweep script is at scripts/maintenance-sweep.py'''
)
```

---

## Template 2: Health Check Monitor

**Schedule:** Every 5 minutes
**Purpose:** Monitor system health (services, ports, disk, backups) and alert on failures.

```python
cronjob(
    action='create',
    job_id='<uuid>',
    name='Prismatic Engine — Health Check Monitor',
    schedule='every 5m',
    model={'model': 'deepseek-v4-pro', 'provider': 'deepseek'},
    deliver='local',
    prompt='''You are a health check monitor for the Prismatic Engine infrastructure.

## Checks (run every tick)
1. **Systemd services:** systemctl list-units --type=service --state=running | grep -E 'hde|hd-|payment|report|api|mcp|prismatic'
2. **Port listeners:** ss -tlnp | grep -E '800[0-9]|808[0-9]|300[0-9]|500[0-9]'
3. **Health endpoints:** curl -s http://localhost:<port>/ping on each active port
4. **Disk usage:** df -h / | tail -1 — alert if >85%
5. **Cron job health:** cronjob(action='list') — verify all jobs are running
6. **Memory:** free -h — alert if available < 2GB

## Alert Thresholds
- Service down > 5 minutes → post Linear comment on health-tracker issue
- Port not listening → check if orphan process holds port (ss -tlnp | grep PORT)
- Disk > 85% → post Linear comment with du -sh /var/log/ /tmp/ ~/.hermes/
- Cron job failing > 3 cycles → post Linear comment with job_id

## Recovery Actions (autonomous)
- Orphan process on port: sudo kill <PID> && sudo systemctl restart <service>
- Full disk: clean old log files, docker images, pip caches
- Stale locks: check /home/ubuntu/.antigravity/swarm_locks.json — kill locks > 10 min

## Silent Mode
If all checks pass: respond [SILENT]. Only report when something is wrong.'''
)
```

---

## Template 3: Golden Thread Daily Review

**Schedule:** Daily at 9am MT (17:00 UTC)
**Purpose:** Pull full Linear state, score thread health, produce prioritized action plan.

```python
cronjob(
    action='create',
    job_id='<uuid>',
    name='Prismatic Engine — Golden Thread Daily Review',
    schedule='0 17 * * *',  # 9am MT = 5pm UTC
    model={'model': 'deepseek-v4-pro', 'provider': 'deepseek'},
    deliver='origin',  # Deliver to chat so Michael sees it
    skills=['golden-thread'],
    prompt='''You are running the daily Golden Thread review for Michael's ventures.

## Review Steps
1. **Load the registry:** Read /home/ubuntu/work/project-registry.json
2. **Pull Linear state:** Query all GRO issues with identifier, title, priority, state, labels, project
3. **Group by Golden Thread:** Map each issue to its primary thread (HD Engine, Active Oahu, Sentinel ITAD, etc.)
4. **Score each thread:** Green (active), Yellow (stalled), Red (cold)
5. **Find duplicates:** Issues with similar titles/descriptions
6. **Identify stale items:** Created >30 days with no activity
7. **Produce report:** Formatted as golden-thread review with Thread Analysis, Recommended Actions, and Waiting On You sections

## Report Format
```
# 🔭 Golden Thread Review — [date]

## State of the Swarm
[total issues, by state counts]

## Thread Analysis (one per thread)
- Status emoji + name
- Done count / Total count
- Key wins this session
- Missing pieces (top 3)

## Recommended Actions
### Immediate (today/tomorrow)
### This Week
### Defer (needs user input)

## 🫵 Waiting On You, Michael
[Top 3-5 blocked items with exact actions needed]
```

## Delivery
Post the full review to the current chat. Include the "Waiting On You" section at the bottom.'''
)
```

---

## Template 4: Sync / Backup Job

**Schedule:** Hourly
**Purpose:** Sync project registry, backup critical state, run data pipelines.

```python
cronjob(
    action='create',
    job_id='<uuid>',
    name='Prismatic Engine — State Sync & Backup',
    schedule='@hourly',
    model={'model': 'deepseek-v4-pro', 'provider': 'deepseek'},
    deliver='local',
    prompt='''You are a state sync and backup job for the Prismatic Engine.

## Sync Tasks (every hour)
1. **Project registry sync:**
   - Read /home/ubuntu/work/project-registry.json
   - Query Linear for all issues created/modified in the last hour
   - Update registry with new issue IDs and next_actions
   - Validate JSON: python3 -c "import json; json.load(open('/home/ubuntu/work/project-registry.json'))"

2. **GitHub sync:**
   - For each active repo in the registry: git fetch origin
   - Log any repos with uncommitted changes: git status --short
   - Report repos where local is behind origin: git log --oneline HEAD..origin/main | wc -l

3. **Linear board hygiene:**
   - Count issues per project — flag empty projects (0 issues)
   - Count issues without project assignment (orphans)
   - Count issues stuck in In Progress > 48h

4. **Cron job health:**
   - cronjob(action='list') → verify all expected jobs are present
   - Check for duplicate cron jobs (same name/schedule)

5. **Disk and resource snapshot:**
   - df -h / → log disk usage
   - free -h → log memory
   - uptime → log system uptime

## Output
Write a sync log to /home/ubuntu/work/prismatic-engine/reports/sync-$(date +%Y%m%d-%H%M).log
with each task's status (OK/FAIL) and any anomalies found.

## Silent Mode
If all syncs pass without anomalies: respond [SILENT].'''
)
```

---

## Quick Reference: cronjob() API

```python
# Create
cronjob(action='create', job_id='<uuid>', name='<name>', schedule='<cron>',
    model={'model': '...', 'provider': '...'}, deliver='local|origin', prompt='...',
    skills=['skill1', 'skill2'])

# List all
cronjob(action='list')

# Update
cronjob(action='update', job_id='<uuid>', schedule='<new-cron>',
    model={'model': '...', 'provider': '...'})

# Delete
cronjob(action='delete', job_id='<uuid>')

# Pause
cronjob(action='pause', job_id='<uuid>')

# Resume
cronjob(action='resume', job_id='<uuid>')
```

## Schedule Reference

| Pattern | Meaning |
|---|---|
| `every 5m` | Every 5 minutes |
| `@hourly` | Every hour |
| `0 17 * * *` | Daily at 5pm UTC (9am MT) |
| `0 9 * * 1` | Mondays at 9am |
| `*/30 * * * *` | Every 30 minutes |
| `0 0 1 * *` | First day of every month |

## Delivery Modes

| Mode | Behavior |
|---|---|
| `local` | Output goes to cron log only — Michael never sees it. Use for execution agents that post to Linear. |
| `origin` | Delivered to the current chat. Use for reviews, digests, and alerts. |

## Model Configuration

| Provider | Model | Status |
|---|---|---|
| `deepseek` | `deepseek-v4-pro` | ✅ Primary (reliable) |
| `openai-codex` | `gpt-5.5` | ⚠️ Fallback (OAuth frequently 429) |

Always verify OAuth status before switching to openai-codex:
```bash
hermes auth list | grep codex
```

If all creds show `rate-limited (429)`, stay on deepseek.
