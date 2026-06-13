# Fred Autonomous Session — Cron-Driven Orchestrator Work Loop

**Created:** Jun 12, 2026  
**Cron Job ID:** 92af25aa9c46  
**Schedule:** Every 2 hours  
**Deliver:** Telegram → Michael (chat_id: 8190664947)  
**Duration:** ~15 minutes per session

## Purpose

Enables the orchestrator (Fred) to execute autonomously without Michael's presence. This is the key to reducing Michael's interaction time to 1 hour/day while getting 100x more output from the swarm.

## Work Loop (priority order)

### 1. Process Trigger Files
- Check `/tmp/trigger-fred-work`
- Execute whatever it asks, delete the file afterward
- These come from the nudge escalator — stalled tasks that need orchestrator attention

### 2. Drain agent:fred Queue
- Query Linear for issues with `agent:fred` label in Todo/In Progress
- Code review (Ned's work): verify changes on master, merge to staging
- Decision items: add to "decisions needed" list for Michael
- AGY research results: read comments, verify deliverables exist on disk, move to Done or create follow-ups
- Move completed items to Done with `agent:done` label

### 3. Feed AGY (2 tasks per session max)
- Query Linear for `agent:agy` issues in Todo/Backlog
- Pick top 2 by priority
- Write task files: `/tmp/agy-feed-{ISSUE}.txt`
- Launch AGY foreground PTY: `agy --print "Read task file..." --print-timeout 300s --add-dir /tmp`
- Wait for completion (up to 360s)
- Post results as Linear comment, move issue to In Progress
- Never more than 2 concurrent AGY sessions

### 4. Drain agent:kai Queue
- Query Linear for `agent:kai` issues in Todo/Backlog
- Simple content/SEO tasks: execute directly
- Complex tasks: create AGY review sub-issues

### 5. Idle Revenue Work
When queues are empty, pick one:
- Generate missing alt text for AOT pages
- Inject schema markup on high-traffic AOT pages
- Research one new Idaho MSP for AI consulting outreach
- Fix AOT broken links
- Generate SEO content pages

### 6. Send Decision Digest
One Telegram message summarizing:
- Issues processed this session
- AGY tasks launched (and results if complete)
- Decisions needed from Michael (max 3, each one sentence)
- What runs next session

## Constraints
- 15-minute session window — don't try to do everything
- AGY requires foreground + PTY (background = SIGTERM 143)
- Post Linear comments for every action taken
- If error, log it and move on — don't get stuck

## Interaction Model with Michael
- Michael checks Telegram digest (1-2 min)
- Makes 3-5 splenic decisions (2-3 min)
- Swarm executes for 2 hours
- Repeat
- Total interaction: ~1 hour/day, 100x output
