# Autonomous Delegation Pattern

## The Pattern
When the user says "keep working" or the session has idle time, spawn async workers and monitor them:

```
Main Agent
  ├── Jules CLI → code generation (async, background)
  │     └── Cron monitor (every 30m): pull completed, push to GitHub
  ├── AGY CLI → research/analysis (async, background)
  │     └── Cron monitor (every 60m): check output, update Linear
  └── Cron sync (every 60m): cross-project golden thread health
```

## Jules CLI — Code Generation
- `jules new "task description"` — creates async session
- `jules remote list --session` — list all sessions
- `jules remote pull --session ID --apply` — pull and apply completed work
- Runs in remote cloud VM — non-blocking
- Best for: building endpoints, creating files, writing tests

## AGY CLI — Research/Analysis  
- `agy --print "prompt" --print-timeout 600s` — non-interactive run
- `agy --continue` — resume last conversation
- Best for: reading documents, competitive analysis, summarizing

## Cron Monitor Pattern
```bash
# Jules monitor — every 30m
hermes cron create --name "Jules Monitor" --schedule "every 30m" \
  --prompt "Check jules sessions, pull completed, push to GitHub, update Linear"

# AGY monitor — every 60m  
hermes cron create --name "AGY Monitor" --schedule "every 60m" \
  --prompt "Check AGY output file, update Linear issues with findings"
```

## Key Principle
Never just wait. If you're between user messages, check the registry for stalled projects, spawn a worker, or run autonomous work from the idle queue.
