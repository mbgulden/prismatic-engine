#!/usr/bin/env python3
"""
Google Docs OAuth Setup — Fixed redirect_uri for headless auth.
"""

import json
import os

CREDS_PATH = "/home/ubuntu/mounts/synology-photo/Antigravity/credentials.json"
TOKEN_PATH = "/home/ubuntu/work/prismatic-engine/.gdocs_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]

from google_auth_oauthlib.flow import InstalledAppFlow

# Load client config and manually set oob redirect
with open(CREDS_PATH) as f:
    client_config = json.load(f)

# Add the out-of-band redirect URI that lets Google show the code on screen
client_config["installed"]["redirect_uris"] = ["urn:ietf:wg:oauth:2.0:oob"]

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

# Generate auth URL
auth_url, _ = flow.authorization_url(
    access_type="offline",
    include_granted_scopes="true",
    prompt="consent",
    login_hint="mbgulden@gmail.com",
)

print("\n" + "=" * 70)
print("  AUTHORIZE HERE:")
print("=" * 70)
print(auth_url)
print("=" * 70)
print("\nAfter authorizing, Google will show you a code.")
print("Copy that code and paste it below.\n")

code = input("Paste authorization code: ").strip()
flow.fetch_token(code=code)

creds = flow.credentials

os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
with open(TOKEN_PATH, "w") as f:
    f.write(creds.to_json())

print(f"\n✅ Token saved to: {TOKEN_PATH}")
print(f"   Scopes: {creds.scopes}")
print(f"   Has refresh token: {bool(creds.refresh_token)}")
