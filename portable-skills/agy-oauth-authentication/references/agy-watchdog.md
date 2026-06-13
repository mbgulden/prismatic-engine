# AGY Watchdog (v2)

**Location:** `$PRISMATIC_HOME/work/agentic-swarm-ops/ops/agy_watchdog.py`

**Invocation:** `python3 $PRISMATIC_HOME/work/agentic-swarm-ops/ops/agy_watchdog.py` (workdir doesn't matter)

**Exit code semantics:** The script returns `exit(len(alerts))` — exit 0 = all clear, exit 1 = one alert, exit 2+ = multiple alerts. The exit code is an alert *count*, not a severity code. Always check the output text to understand *what* the alerts are.

## Output Format

Multiple lines, each prefixed with a severity emoji. Final line always shows `OAuth: <status>`.

### All-Clear (exit 0)

```
🟢 No AGY processes running
OAuth: ok (3596s)
```

### Alert Patterns (exit ≥1)

| Output Pattern | Severity | Meaning | Action |
|---------------|----------|---------|--------|
| `🔴 OAuth token expired` | Critical | Token fully expired | Run `refresh_token.py` immediately |
| `🔴 OAuth token expiring_soon (Ns)` | Critical | Token expires in <300s | Run `refresh_token.py` preemptively |
| `🔴 OAuth token missing` | Critical | No token file found | Interactive re-auth required (see SKILL.md Step 1–3) |
| `🔴 PID N: Stalled Ns in ep_poll — transcript stuck` | Critical | AGY process frozen >300s with no transcript progress | Kill candidate — use exact PID: `kill N`, or `kill $(ps -e -o pid,comm \| awk '/agy-bin/{print $1}')` (avoids `pkill -f agy-bin` self-kill pitfall) |
| `🟡 PID N: No transcript progress for Ns` | Warning | Transcript stalled >120s but <300s | Monitor; may self-resolve |
| `🟡 PID N: Waiting on API (ep_poll) for Ns` | Warning | API call taking >180s | Check API quota/rate limits |
| `🟡 Log signal: permission_prompt_unapproved` | Warning | AGY stuck waiting for permission confirmation | Kill stuck AGY process |
| `🟡 Log signal: abnormal_shutdown` | Warning | AGY terminated unexpectedly | Check for truncated token file at native path |
| `🟡 Log signal: history_truncation_error` | Info | Context management failure — two sub-cases: (A) `history.jsonl: no such file or directory` = first-run or post-crash clean state, **benign/ignore** — file will be created on next AGY launch; (B) actual truncation failure mid-session = session may be too long, start fresh | For case A: no action (file auto-created). For case B: restart AGY session |
| `🟡 Log signal: model_output_error` | Info | API/model failure mid-generation | Check quota, retry |

### Inactivity Recovery Patterns (v2 — ported from Hub Watchdog.ts)

| Output Pattern | What Happened |
|---------------|---------------|
| `🟡 {agent}/{task_id}: Running for {N}s without completion` | Run record in 'running' state >180s — warn threshold |
| `🔴 {agent}/{task_id}: {N}s running, {F} prior failures. Initiating recovery (attempt {F+1}/3)...` | Exceeded kill threshold (300s) — SIGTERM sent |
| `🟡 ... Recovery attempted ({F+1}/3). Will retry if stall persists.` | Recovery completed, record marked failed |
| `🔴 ... Recovery exhausted (3 attempts). Escalation nudge created.` | Max retries hit — nudge file written to `/tmp/prismatic/nudge-fred` |

## Log Signal Detection

Beyond process and OAuth checks, the watchdog scans the most recent AGY log (`LOG_DIR/cli-*.log`) for these error signals:

| Signal | Meaning | Action |
|--------|---------|--------|
| `permission_prompt_unapproved` | "Tool confirmation" in log without "approved=true" — AGY stuck waiting for permission | Kill the stuck AGY process |
| `history_truncation_error` | "failed to truncate history" — conversation context management failing | Session may be too long; start a fresh one |
| `abnormal_shutdown` | "signal: aborted" — AGY terminated unexpectedly | Check for truncated token file at native path (see pitfall in SKILL.md). The native path (`~/.gemini/antigravity-cli/`) is directly written by AGY and vulnerable to mid-write truncation during crashes. The hermes profile path (written only by `refresh_token.py`) typically stays intact. `refresh_token.py` checks the hermes profile path first, so it naturally picks the intact copy. |
| `model_output_error` | "Model output error" — API/model failure mid-generation | Check API quota, retry with new session |

The log scan covers the last 50 lines of the current log file. These signals are informational — they won't appear in stdout unless the watchdog is run in verbose mode or there are active alerts.

## Stall Tracker

The watchdog writes stalled issue state to `/tmp/agy_stall_tracker.json`. Structure per entry:

```json
{
  "GRO-993": {
    "first_seen": "2026-06-10T00:16:04.040150+00:00",
    "cycle_count": 1,
    "retry_count": 0,
    "escalated": false,
    "last_seen": "2026-06-10T00:16:04.040150+00:00"
  }
}
```

Keyed by Linear issue ID. Tracks how many watchdog cycles an issue has been stalled, whether recovery has been attempted, and whether it's been escalated. Stale entries persist across runs — they need manual cleanup or issue resolution. Check this file to see what AGY was working on when auth died.

## Interpretation

- **No processes + ok OAuth → exit 0** → idle and ready. Normal state.
- **Expired/expiring token** → run `scripts/refresh_token.py`, then re-run watchdog to confirm green. **Even epoch-0 tokens (`expiry: 1970-01-01T00:00:00`) are recoverable** — `refresh_token.py` reads the `refresh_token` field from the JSON, not the expiry field, so a corrupted expiry doesn't block refresh. Only a missing or corrupted `refresh_token` string requires full interactive re-auth.
- **Stuck process** → kill with `pkill -f agy-bin`, re-launch per main skill steps.
- The watchdog reads tokens from the Hermes-profile path (`~/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/`) — not the AGY native path. This is why `refresh_token.py` writes to both.
- **Stale tmux sessions** are NOT checked by the watchdog but are a common companion finding. Run `tmux list-sessions | grep agy` — idle sessions without active AGY processes can be cleaned with `tmux kill-session -t <name>`.
- **Stale log signals (false positive)**: The watchdog's `check_recent_logs()` scans the most recent log file regardless of whether the AGY process that wrote it is still alive. When **zero AGY processes are running**, any log signals (`model_output_error`, `history_truncation_error`, `abnormal_shutdown`, `permission_prompt_unapproved`) are from a prior terminated session — **stale and benign**. No action needed beyond noting them. Only escalate log signals when they coincide with actively running AGY processes.

- **`pkill -f agy-bin` self-kill hazard in Hermes terminal**: When running `pkill -f agy-bin` or `kill $(pgrep -f agy-bin)` inside the Hermes terminal tool, the wrapping shell process's full command line contains the pattern `agy-bin` (because the `-f` flag matches against `/proc/PID/cmdline` which includes the full argument text). This means the shell process kills itself — the terminal receives SIGTERM and exits code -15. **Workaround**: use `kill $(ps -e -o pid,comm | awk '/agy-bin/{print $1}')` which matches against the COMM name field only, or `pgrep -x agy-bin` (exact match, no `-f`). Prefer killing by specific PID: `ps -e -o pid,comm | grep agy-bin`, then `kill <PID>` each.

## Manual Deep-Dive (when watchdog output is too sparse)

When the watchdog prints only alerts without per-process detail (because no AGY processes are running), or when you need richer context around log signals, run this sweep:

```bash
# 1. OAuth — read expiry directly
python3 -c "
import json, time; from datetime import datetime, timezone
p = '${PRISMATIC_HOME}/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/antigravity-oauth-token'
with open(p) as f: d = json.load(f)
exp = datetime.fromisoformat(d['token']['expiry'])
print(f'Expires: {exp.isoformat()}')
print(f'Remaining: {(exp - datetime.now(timezone.utc)).total_seconds():.0f}s')
"

# 2. Agent run records — any stuck in 'running' state?
python3 -c "
import json, os; from datetime import datetime, timezone
d = '${PRISMATIC_HOME}/work/agentic-swarm-ops/agent-runs'
if os.path.isdir(d):
    for f in sorted(os.listdir(d)):
        if not f.endswith('.json'): continue
        r = json.loads(open(os.path.join(d,f)).read())
        if r.get('state')=='running':
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(r['started_at'])).total_seconds()
            print(f\"STUCK: {r.get('agent','?')}/{r.get('task_id','?')} — {int(age)}s — {r.get('title','?')[:60]}\")
    else: print('No stuck records')
"

# 3. Brain transcript — last activity timestamp
python3 -c "
import os, time
brain = '${PRISMATIC_HOME}/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/brain'
latest = 0
for d in os.listdir(brain):
    t = os.path.join(brain, d, '.system_generated', 'logs', 'transcript.jsonl')
    if os.path.exists(t):
        m = os.path.getmtime(t)
        if m > latest: latest = m
if latest:
    print(f'Last transcript: {int(time.time()-latest)}s ago')
else:
    print('No transcripts')
"

# 4. Log file signals with line context (not just signal names)
python3 -c "
import os
logdir = '${PRISMATIC_HOME}/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/log'
logs = sorted([f for f in os.listdir(logdir) if f.startswith('cli-')], reverse=True)
if logs:
    with open(os.path.join(logdir, logs[0])) as f:
        lines = f.readlines()
    for l in lines[-30:]:
        if any(kw in l for kw in ['Model output error', 'failed to truncate', 'signal: aborted', 'Tool confirmation', 'Error']):
            print(l.strip()[:200])
"
```

Each probe answers a specific question: *can AGY authenticate? are there ghost sessions? when was the last real work? were the log signals from a live session or a dead one?* Run all four for a complete picture; run individual probes when you only need one answer.

## Inactivity Recovery (v2)

Port of Hub Watchdog.ts — monitors agent run records (`$PRISMATIC_HOME/work/agentic-swarm-ops/agent-runs/`) for records stuck in `running` state:

| Threshold | Behavior |
|-----------|----------|
| <120s | Grace period — no alert |
| 120–180s | No alert yet (still in grace period) |
| 180–300s | 🟡 Warning: log it, no kill |
| >300s | 🔴 Kill: SIGTERM → wait 3s → SIGKILL. Updates run record failure_count |
| >300s + 3 failures | 🔴 Escalation: writes nudge to `/tmp/prismatic/nudge-fred` for dispatcher |

Recovery only applies to agent types in `RECOVERY_RETRY_RECORD_TYPES` (agy, jules, codex). Hermes records are excluded — they're handled by Hermes' own lifecycle.

The watchdog script itself runs recovery on each tick — no separate cron job needed. If a record has exceeded max retries, the nudge file is created atomically and the dispatcher picks it up on its next cycle.
