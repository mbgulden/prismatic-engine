# Second Witness — Persistent AGY Review Terminal

Pattern discovered and proven Jun 2026 during Prismatic Engine Core Phase 1 build.

## Concept
A persistent AGY cron job that acts as an independent "second witness" reviewer for any project. It loads full project context on every run, scans Linear for issues needing review, verifies work against the architecture spec, and creates fix tasks when problems are found.

## Architecture

```
Second Witness Cron (every 30min)
    │  Loads project context file (blueprint + task map + label IDs)
    │  Scans Linear for issues in Review/Done states
    │  Reviews each against architecture spec
    │  Rates: APPROVED / NEEDS_CHANGES / BLOCKED
    │
    ├── APPROVED → no action
    └── NEEDS_CHANGES / BLOCKED → creates Linear fix task
         │  Design/spec issues → agent:agy
         │  Implementation issues → agent:fred
         │  Orchestration issues → agent:fred
         │
         ▼  Delivers timestamped report to user
```

## Context File Format
The Second Witness needs a context file with:
1. Project architecture blueprint path (to load on every run)
2. All task IDs and descriptions (to know what's expected)
3. Label IDs for assignment (agent:agy, agent:fred, etc.)
4. State IDs for the Linear team
5. Review protocol steps
6. Fix task creation rules

Template: `$PRISMATIC_HOME/work/prismatic-engine/specs/second-witness-context.md`

## Cron Setup
```yaml
name: "🔮 Second Witness — AGY review terminal"
schedule: "every 30m"
model: deepseek-v4-pro
deliver: telegram:<user_chat_id>
prompt: |
  You are the Second Witness review terminal.
  FIRST: Read <context-file-path> — this is your full operating procedure.
  THEN: Execute the Review Protocol exactly as specified.
  This runs every 30 minutes. Do NOT fabricate findings.
```

## Key Design Decisions
- Uses `deepseek-v4-pro` (not flash) — review quality matters
- Runs every 30min (not every 5min) — reviews need depth, not speed
- Creates fix tasks directly in Linear (not just reporting) — closes the loop
- Loads full architecture blueprint on every run — maintains context across sessions
- Reports via Telegram delivery — user sees findings without checking Linear

## Verification
After creating the cron, verify:
1. First run completes within 5 minutes
2. Report includes: issues reviewed count, verdict table, project health section
3. Fix tasks appear in Linear with correct labels and parent links
4. Subsequent runs don't re-report already-reviewed issues (state check)
