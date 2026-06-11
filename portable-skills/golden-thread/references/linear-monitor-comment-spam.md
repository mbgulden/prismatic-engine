# Linear Monitor Comment Spam — Canonical Examples

## GRO-106: FastAPI Wrapper (June 2026)

**Issue**: GRO-106 accumulated 50 comments, but 45+ were identical Jules Session Monitor
posts fired every ~30 minutes. The actual signal was buried.

**Monitor pattern**: Jules Session Monitor cron posts to the issue:
- "Checked jules remote list --session for hd-platform: 1 session (GRO-100) — Completed, already applied"
- Identical content, different timestamps, every 25-35 minutes
- Zero actionable information after the first few posts

**Where the real signal was**:
- Comment #2 (May 31 10:54 AM): Full status — `api/routes/natal.py` fully implemented,
  Pydantic models validated, API-key auth active, committed and pushed.
- Comment ~#15 (Jun 1 06:07 AM): Noted server was NOT running yet, port 8000 DOWN.
- By Jun 1 3PM: Server actually running (started May 31), verified via curl.

**Impact**: Reading 50 comments took significant token budget and produced zero new
information beyond what the first 3 and last 3 comments contained.

## Remediation Options

1. **Move monitor to dedicated issue**: Create a "Jules Session Monitor — hd-platform" issue
   and have the cron post there, not on the working issue.
2. **Silence-on-no-change**: If the monitor has nothing new (same session, same state),
   skip posting. Only post when a session completes, a new session starts, or state changes.
3. **Daily digest only**: Post at most once per day per issue.
4. **Slack/email alert instead**: Don't pollute Linear comments at all.

## Detection Heuristic

When reading Linear comments on a stale issue:
- If comments > 20, scan first 5 and last 5
- If middle comments all share the same first 80 chars, they're monitor spam — skip
- Look for structural differences: "✅", "MERGED", "DONE", "deployed" are signal;
  "Checked", "Session", "monitor", "no new" are noise
