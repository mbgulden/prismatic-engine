#!/usr/bin/env python3
"""Refresh the AGY OAuth token and write to both paths (AGY + watchdog).

Usage: python3 refresh_token.py
Reads the existing refresh_token from the watchdog token file (or falls back to
AGY native path), calls Google's OAuth2 token endpoint, then writes the new
access token to both required locations.

IMPORTANT: Uses absolute paths, not os.path.expanduser('~'), because in Hermes
agent contexts (cron jobs, subagents) HOME is set to the profile's sandboxed
home, not the system /home/ubuntu.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "$GOOGLE_OAUTH_CLIENT_SECRET"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Absolute paths — not os.path.expanduser('~') (sandboxed in Hermes contexts)
TOKEN_PATHS = [
    "/home/ubuntu/.hermes/profiles/orchestrator/home/.gemini/antigravity-cli/antigravity-oauth-token",
    "/home/ubuntu/.gemini/antigravity-cli/antigravity-oauth-token",
]


def find_existing_token():
    """Return (path, data, raw) for the first existing token file, or (None, None, None).

    Handles truncated JSON (common after signal:aborted shutdowns) by regex-extracting
    the refresh_token when json.load fails.
    """
    for p in TOKEN_PATHS:
        if os.path.exists(p):
            with open(p) as f:
                raw = f.read()
            try:
                return p, json.loads(raw), raw
            except json.JSONDecodeError:
                # Truncated file — try regex extraction of refresh_token
                import re
                match = re.search(r'"refresh_token":\s*"([^"]+)"', raw)
                if match:
                    rt = match.group(1)
                    # Build minimal data dict with just the refresh_token
                    data = {"token": {"refresh_token": rt}}
                    return p, data, raw
                return p, None, raw
    return None, None, None


def main():
    # 1. Read existing token
    source_path, old, raw = find_existing_token()
    if old is None:
        print("ERROR: No token file found at either path", file=sys.stderr)
        for p in TOKEN_PATHS:
            print(f"  (checked) {p}", file=sys.stderr)
        sys.exit(1)

    # Report if we recovered from a truncated file
    if raw and "refresh_token" in raw and old.get("token", {}).get("access_token") is None:
        print("⚠️  Token file was truncated — recovered refresh_token via regex")

    refresh_token = old.get("token", {}).get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh_token in existing token file", file=sys.stderr)
        sys.exit(1)

    # 2. Call OAuth2 token endpoint
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(TOKEN_ENDPOINT, data=body)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if "error" in result:
        print(f"ERROR: Token endpoint returned error: {result}", file=sys.stderr)
        sys.exit(1)

    # 3. Build new token object
    expiry_dt = datetime.fromtimestamp(time.time() + result["expires_in"], tz=timezone.utc)
    new_token = {
        "token": {
            "access_token": result["access_token"],
            "token_type": "Bearer",
            "refresh_token": refresh_token,
            "expiry": expiry_dt.isoformat(),
        },
        "auth_method": "consumer",
    }

    # 4. Write to both paths (absolute)
    for path in TOKEN_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(new_token, f, indent=2)

    minutes = result["expires_in"] // 60
    print(f"Token refreshed — expires in ~{minutes} min ({expiry_dt.isoformat()})")
    for p in TOKEN_PATHS:
        print(f"  {'✓' if os.path.exists(p) else '✗'} {p}")


if __name__ == "__main__":
    main()
