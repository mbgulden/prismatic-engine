# Swarm Monitor Pattern

## Problem

Multiple operational cron jobs produce noisy output every few minutes:
- Dispatcher (`every 15m`): "Found 0 issues with agent:agy... Found 35 issues with agent:fred..."
- Watchdog (`every 5m`): "PID 12345: wchan=ep_poll transcript_age=12s"

Routing these directly to the user's main chat is noise pollution. But routing them to `local` means nobody sees real alerts.

## Solution

Three-tier routing:

1. **Noisy operational jobs → `local`** — they write to `~/.hermes/profiles/<profile>/cron/output/<job_id>/` as timestamped `.md` files
2. **Swarm Monitor** — a `no_agent: true` script that reads those output files, filters for actionable items only
3. **Swarm Monitor → user's preferred chat** — delivers via the appropriate bot (Autobot for Michael)

## Script Design

```python
#!/usr/bin/env python3
"""
Reads cron outputs from ~/.hermes/profiles/<profile>/cron/output/<job_id>/
Only prints when there's something actionable. Silence = all clear.
"""

import os, glob
from datetime import datetime, timezone

CRON_OUTPUT_DIR = "/home/ubuntu/.hermes/profiles/orchestrator/cron/output"

def get_latest_output(job_id):
    """Get the most recent cron output from its subdirectory."""
    job_dir = os.path.join(CRON_OUTPUT_DIR, job_id)
    if not os.path.isdir(job_dir):
        return None, None
    for ext in [".md", ".txt"]:
        files = sorted(glob.glob(os.path.join(job_dir, f"*{ext}")),
                       key=os.path.getmtime, reverse=True)
        if files:
            with open(files[0]) as f:
                return f.read(), os.path.getmtime(files[0])
    return None, None

def main():
    now = datetime.now(timezone.utc)
    reports = []
    
    # Only check outputs younger than threshold
    for job_id, max_age, checker in [
        ("e2f1a3b4c5d6", 1800, check_dispatcher),   # 30 min
        ("500749c7949d", 900,  check_watchdog),       # 15 min
    ]:
        content, mtime = get_latest_output(job_id)
        if content and mtime and (now.timestamp() - mtime) < max_age:
            report = checker(content)
            if report:
                reports.append(report)
    
    if reports:
        print("## 🤖 Swarm\n")
        print("\n".join(reports))
    # Empty stdout = silent delivery. User sees nothing.
```

## Key Rules

- **Print nothing when all green** — `no_agent: true` with empty stdout = no message delivered
- **Filter aggressively** — ignore known false positives (Codex empty output, "0 launched" cycles)
- **Age-gate the outputs** — don't report on stale files from hours ago
- **Route through the right bot** — Michael uses @Autob0tautob0t_bot for automation; the Swarm Monitor cron runs on the Autobot profile so messages appear in the right chat

## Wiring

```bash
# Create the cron on the Autobot profile (not orchestrator)
hermes --profile autobot cron create "every 15m" \
  --name "Swarm Monitor" \
  --script "swarm_monitor.py" \
  --no-agent \
  --deliver "telegram:8190664947"
```

## Pitfall: Dispatcher Misleading Counts

The dispatcher's "X launched, Y errors" summary counts `agent:fred` signals (print statements, not real launches) and Codex empty-output as "errors." Filter these in the monitor — only count actual subprocess launches (AGY `subprocess.Popen`, Jules CLI invocation).
