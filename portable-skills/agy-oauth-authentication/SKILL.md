---
name: agy-oauth-authentication
description: >
  Authenticate AGY (Google Antigravity CLI) when the OAuth token expires.
  AGY has NO built-in login command accessible from headless mode — 
  auth requires gcloud SDK + application-default credentials + interactive PTY login.
  Use this skill when AGY returns SIGTERM -15 with zero output on ALL prompts.
triggers:
  - AGY returns SIGTERM -15 or zero output
  - AGY auth or login or authentication
  - antigravity-oauth-token expired or expiring_soon
  - AGY backend unresponsive
  - agy_watchdog.py reports OAuth expiring_soon/expired/missing
  - Cron watchdog alert (autonomous recovery — `refresh_token.py` is fully automatable)
---

# AGY OAuth Authentication

## Hermes Invocation (Fred/Ned use this)

## AGY-Native Invocation (AGY loads this skill)

When AGY detects OAuth expiry during autonomous execution:
1. Run `agy login` and follow browser redirect on port 8090
2. If headless (no browser), run `python3 <skill-dir>/scripts/refresh_token.py`
3. Verify with `agy models` — should return model list
4. If all fails, escalate to orchestrator with SIGTERM-15 context

## Architecture

AGY stores its OAuth token at `~/.gemini/antigravity-cli/antigravity-oauth-token`.

AGY uses TWO client IDs (found in the agy-bin binary):
- `884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com`
- `1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com`

Client secrets:
- `$GOOGLE_OAUTH_CLIENT_SECRET` (for 1071006060591-...)
- `$GOOGLE_OAUTH_CLIENT_SECRET` (for 884354919052-...)

API endpoint: `cloudcode-pa.googleapis.com`

## Working Auth Method (June 2026)

AGY's `--print` mode is broken — uses a different auth provider. Use `--prompt-interactive` with PTY:

### Step 1: Ensure gcloud is authenticated
```bash
~/google-cloud-sdk/bin/gcloud auth login --no-launch-browser
# Michael opens URL, gets verification code, sends to Fred
# Fred pipes code to gcloud
```

### Step 2: Copy ADC credentials
```bash
cp ~/.config/gcloud/legacy_credentials/mbgulden@gmail.com/adc.json \
   ~/.config/gcloud/application_default_credentials.json
```

### Step 3: Launch AGY with PTY
```bash
pkill -f agy-bin; sleep 2
export GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json
echo "your prompt" | agy --prompt-interactive "initial message"
```

### Step 4: For the dispatcher, send prompt + /exit
```bash
echo -e "Do X, Y, Z. Reply DONE when finished.\n/exit" | \
  agy --prompt-interactive "Working on Linear issue GRO-XXX"
```

## Token Refresh (programmatic)

Working pair for refresh:
- Client ID: `1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com`
- Client Secret: `$GOOGLE_OAUTH_CLIENT_SECRET`

Full refresh flow (also in `scripts/refresh_token.py`):
1. Read the existing token file to extract `token.refresh_token`
2. POST to `https://oauth2.googleapis.com/token` with `grant_type=refresh_token`
3. Build a new token object preserving `auth_method: "consumer"` and the existing `refresh_token`
4. **Write to BOTH paths** — AGY uses `~/.gemini/antigravity-cli/` but the watchdog reads from `~/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/`
5. Expiry MUST be ISO 8601 with timezone and microseconds (e.g. `2026-06-06T06:09:48.272828+00:00`) — generated via `datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()`. The watchdog parses with `datetime.fromisoformat()` which rejects the literal `%f` placeholder.

```python
import urllib.request, urllib.parse, json, os, time
from datetime import datetime, timezone

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "$GOOGLE_OAUTH_CLIENT_SECRET"

# Read existing refresh_token — try both paths (watchdog path first, as it's
# the canonical source after previous refreshes; fall back to AGY native path).
# Use ABSOLUTE paths — os.path.expanduser('~') resolves to the Hermes profile's
# sandboxed home, NOT the system /home/ubuntu, in cron-job and subagent contexts.
TOKEN_PATHS = [
    "/home/ubuntu/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/antigravity-oauth-token",
    "/home/ubuntu/.gemini/antigravity-cli/antigravity-oauth-token",
]
token_file = next((p for p in TOKEN_PATHS if os.path.exists(p)), None)
if not token_file:
    raise FileNotFoundError("No token file found at either path")
with open(token_file) as f:
    old = json.load(f)
refresh_token = old["token"]["refresh_token"]

# Refresh
body = urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": refresh_token,
    "grant_type": "refresh_token",
}).encode()
req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
req.add_header("Content-Type", "application/x-www-form-urlencoded")
response = urllib.request.urlopen(req, timeout=10)
result = json.loads(response.read())
if "error" in result:
    raise RuntimeError(f"Token refresh rejected: {result}")

# Build new token — MUST use correct ISO format
expiry_dt = datetime.fromtimestamp(time.time() + result["expires_in"], tz=timezone.utc)
new_token = {
    "token": {
        "access_token": result["access_token"],
        "token_type": "Bearer",
        "refresh_token": refresh_token,
        "expiry": expiry_dt.isoformat()  # ISO 8601 with microseconds + tz
    },
    "auth_method": "consumer"
}

# Write to BOTH paths (absolute, not expanduser)
for path in TOKEN_PATHS:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(new_token, f, indent=2)
print(f"Token refreshed. New expiry: {expiry_dt.isoformat()}")
```

## Pitfalls

- **`os.path.expanduser('~')` in Hermes context**: When running as a cron job or subagent, the Hermes agent sets `HOME` to the profile's sandboxed home (`/home/ubuntu/.hermes/profiles/orchestrator/home/`), NOT the system `/home/ubuntu/`. This means `os.path.expanduser('~/.hermes/...')` produces a DOUBLED path like `/home/ubuntu/.hermes/profiles/orchestrator/home/.hermes/...`. Always use absolute paths starting with `/home/ubuntu/` in any Python snippet that writes files outside the sandbox. The terminal tool is unaffected — its `$HOME` is the real system home.
- **`--print` mode returns SIGTERM -15** even with valid tokens. Use `--prompt-interactive` + PTY.
- **Token format**: `auth_method: "consumer"`, standard OAuth2 Bearer token.
- **ADC fallback**: AGY's interactive mode falls back to gcloud ADC when its own auth provider fails.
- **`GOOGLE_APPLICATION_CREDENTIALS`** env var must be set.
- **Stale processes**: Always `pkill -f agy-bin; sleep 2` before launching.
- **`pkill -f agy-bin` / `kill $(pgrep -f agy-bin)` can SELF-KILL in Hermes terminal context**: The Hermes terminal tool wraps every command in a long bash invocation that includes the original command text in `argv`. Since `pgrep -f` and `pkill -f` match against the full command line, the wrapping shell process matches the pattern `agy-bin` and gets killed — the terminal receives SIGTERM and exits with code -15. **Workaround**: use `kill $(ps -e -o pid,comm | awk '/agy-bin/{print $1}')` which matches against the process COMM name only (not the full cmdline), or kill by specific PID. Run `ps -e -o pid,comm | grep agy-bin` first to confirm the actual binary PIDs, then `kill <PID>` each. Alternatively, `pgrep -x agy-bin` matches exact binary name (no `-f` flag) and avoids self-matching.
- **Watchdog reference**: See `references/agy-watchdog.md` for invocation, output format, and status interpretation.
- **Dual path requirement**: AGY reads the token from `~/.gemini/antigravity-cli/antigravity-oauth-token`, but the watchdog (`agy_watchdog.py`) reads from `~/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/antigravity-oauth-token`. After any token refresh, **write to both paths** or the watchdog will continue reporting stale/expired tokens even after a successful refresh.
- **Truncated token file after abnormal shutdown**: When AGY terminates with signal: aborted (e.g. MCP manager gdrive crash), the token file at the **native AGY path** (~/.gemini/antigravity-cli/) can be truncated mid-write (observed: 538 bytes, cut off mid-JSON at the expiry field). The hermes profile path is written only by refresh_token.py and typically stays intact. refresh_token.py checks the hermes profile path first, so it naturally picks the intact copy. If both are truncated, regex-extract the refresh_token then run the programmatic refresh flow. The scripts/refresh_token.py script handles this automatically — it falls back to regex extraction when json.load() fails, prints a warning, then proceeds with the refresh.
- **Cron interval too tight at 55 minutes**: Token validity is ~60 minutes. A 55-minute refresh interval leaves only ~5 minutes of margin. Scheduler jitter, system load, or network delays can push the refresh past expiry (observed 2026-06-10: token expired ~2 minutes before the 55-minute cron fired). **Use 45-minute intervals** — provides a safe 15-minute margin. Update existing cron jobs that use the old 55-minute recommendation.

## Autonomous Recovery (Cron-Safe)

The full recovery flow requires zero user interaction:
1. Run watchdog: python3 /home/ubuntu/work/agentic-swarm-ops/ops/agy_watchdog.py
2. If OAuth expired or expiring_soon: python3 <skill-dir>/scripts/refresh_token.py
3. **Kill stuck AGY processes**: `kill $(ps -e -o pid,comm | awk '/agy-bin/{print $1}') 2>/dev/null; sleep 2` — OAuth expiry causes all running AGY processes to deadlock in `futex_wait_queue`. They will **never self-recover**, even after token refresh. Kill them all. Uses `ps -e -o pid,comm` instead of `pgrep -f` to avoid the self-kill pitfall in Hermes terminal context.
4. Re-run watchdog to confirm green (zero processes + OAuth ok)

This works from cron jobs, subagents, and headless sessions. refresh_token.py handles truncated files, missing paths, and writes to both required locations automatically. Token validity is ~60 minutes per refresh.

**Cron interval**: Use every 45 minutes, NOT every 55 minutes. A 55-minute interval leaves only ~5 minutes of slack — scheduler jitter, system load, or network delays can push the refresh past expiry. Two confirmed near-misses prove this: (1) 2026-06-10: token expired ~2 minutes before a 55-minute cron would have fired. (2) 2026-06-11: watchdog caught token at **29 seconds** from expiry — if the watchdog cron had fired 30 seconds later, any running AGY task would have deadlocked. 45 minutes provides a safe 15-minute margin and prevents these fire-drills.

### Cron Auto-Refresh (Production — June 2026)

**Status: ACTIVE.** The auto-refresh cron was created 2026-06-13 as job `d8660aee2fb0` — runs `refresh_token.py` every 45 minutes with `deliver=local` (silent). Do NOT create a duplicate. If the token still expires between cycles, the watchdog (`500749c7949d`, every 5 min) catches it and the nudge executor triggers a manual refresh.

To verify it's still running: `cronjob action=list | grep d8660aee2fb0`
