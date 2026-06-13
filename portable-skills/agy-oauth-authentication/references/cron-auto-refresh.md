# Cron-Based OAuth Auto-Refresh (Jun 2026)

## Setup

Create a cron job that refreshes the AGY OAuth token every 55 minutes (tokens last ~60 min):

```python
# cronjob action='create'
# schedule: every 55m
# deliver: local (silent — no user notification)
# prompt: "Refresh AGY OAuth token using programmatic flow..."
```

## Refresh Script Pattern

```python
import urllib.request, urllib.parse, json, os, time
from datetime import datetime, timezone

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "$GOOGLE_OAUTH_CLIENT_SECRET"

TOKEN_PATHS = [
    os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/antigravity-oauth-token",
    os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/.gemini/antigravity-cli/antigravity-oauth-token",
]

# Find first existing token file
token_file = next((p for p in TOKEN_PATHS if os.path.exists(p)), None)
if not token_file:
    raise FileNotFoundError("No token file found")

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

# Build new token
expiry_dt = datetime.fromtimestamp(time.time() + result["expires_in"], tz=timezone.utc)
new_token = {
    "token": {
        "access_token": result["access_token"],
        "token_type": "Bearer",
        "refresh_token": refresh_token,
        "expiry": expiry_dt.isoformat()
    },
    "auth_method": "consumer"
}

# Write to BOTH paths
for path in TOKEN_PATHS:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(new_token, f, indent=2)
```

## Cron Job Reference

Job ID: `efa2b53bcedd`
Schedule: every 55m
Delivery: local
Status: active as of Jun 9 2026

## Token Expiry Detection

Watchdog `agy_watchdog.py` reports OAuth status. If expired: `🔴 OAuth Expired — AGY is grounded`. If ok: `OAuth: ok (NNNNs)`.
