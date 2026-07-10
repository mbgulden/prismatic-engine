#!/usr/bin/env python3
"""OKF Google Drive Drift Check.

Monitors the OKF (operational knowledge folder) for drift between the local
repo and the published copy on Google Drive. Runs daily via cron.

Cron contract: healthy runs may stay local, but incomplete Drive metadata
checks are errors. A run must not report HEALTHY if auth/network/API failures
prevent checking every mirrored document.

Exit codes:
  0 = no drift
  1 = drift detected
  2 = error (cannot enumerate/authenticate)
"""

from __future__ import annotations

import os
import sys
import re
import json
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Ensure user site packages are in path when run via cron without a full shell env.
for p in [
    os.path.expanduser("~/.local/lib/python3.12/site-packages"),
    "/home/ubuntu/.local/lib/python3.12/site-packages",
]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

# Note: These libraries are standard in the prismatic env.
import google.auth.transport.requests  # noqa: E402
from google.oauth2.credentials import Credentials  # noqa: E402

OKF_ROOT = Path("/home/ubuntu/work/growthwebdev-knowledge/okf")
OKF_BASE = OKF_ROOT.parent
OAUTH_KEYS_PATH = "/home/ubuntu/.config/mcp-gdrive/gcp-oauth.keys.json"
TOKEN_PATH = "/home/ubuntu/.config/mcp-gdrive/.gdrive-server-credentials.json"


def parse_frontmatter(content: str) -> dict[str, str]:
    """Parse key-value pairs from markdown frontmatter."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip().lower()] = v.strip().strip('"').strip("'")
    return fm


def parse_iso_datetime(dt_str: str) -> datetime | None:
    """Parse an ISO 8601 datetime string, replacing Z with UTC offset."""
    if not dt_str:
        return None
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        try:
            return datetime.strptime(dt_str.split(".")[0], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def main() -> int:
    if not OKF_ROOT.exists():
        print(f"[drift-check] OKF_ROOT not found: {OKF_ROOT}", file=sys.stderr)
        return 2

    # 1. Authenticate with Google Drive API
    if not os.path.exists(OAUTH_KEYS_PATH) or not os.path.exists(TOKEN_PATH):
        print("[drift-check] Error: OAuth credentials or keys missing", file=sys.stderr)
        return 2

    try:
        with open(OAUTH_KEYS_PATH) as f:
            keys = json.load(f)
        cfg = keys.get("installed") or keys.get("web")
        if not cfg:
            print("[drift-check] Error: Invalid OAuth keys format", file=sys.stderr)
            return 2

        with open(TOKEN_PATH) as f:
            token = json.load(f)

        creds = Credentials(
            token=token.get("access_token"),
            refresh_token=token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg.get("client_id"),
            client_secret=cfg.get("client_secret"),
        )

        req = google.auth.transport.requests.Request()
        if not creds.valid:
            creds.refresh(req)
        if not creds.token:
            raise RuntimeError("no access token after refresh")
    except Exception as e:
        print(f"[drift-check] Authentication failed: {e}", file=sys.stderr)
        return 2

    # 2. Scan local OKF directory for mirrored files
    mirrored_files = []
    for path in OKF_ROOT.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            content = path.read_text(errors="ignore")
            fm = parse_frontmatter(content)
            doc_id = fm.get("plugin_doc_id") or fm.get("drive_file_id")
            if doc_id:
                mirrored_files.append(
                    {
                        "path": path,
                        "id": doc_id,
                        "title": fm.get("title") or path.stem,
                        "timestamp_local": fm.get("timestamp"),
                    }
                )
        except Exception as e:
            print(
                f"[drift-check] Warning: Failed to parse {path}: {e}", file=sys.stderr
            )

    print(f"Found {len(mirrored_files)} local files linked to Google Drive.")
    if not mirrored_files:
        print("No mirrored files found to check. Status: HEALTHY.")
        return 0

    # 3. Query Google Drive for each file's current modifiedTime
    drifts = []
    missing_on_drive = []
    query_errors = []
    checked_count = 0
    drift_detected = False

    for item in mirrored_files:
        fid = item["id"]
        path = item["path"]
        title = item["title"]
        local_ts = parse_iso_datetime(item["timestamp_local"])

        checked_count += 1
        try:
            api = f"https://www.googleapis.com/drive/v3/files/{fid}?fields=id,name,modifiedTime,trashed"
            req = Request(api, headers={"Authorization": f"Bearer {creds.token}"})
            with urlopen(req, timeout=20) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
            if meta.get("trashed"):
                missing_on_drive.append((item, "trashed in drive"))
                drift_detected = True
            else:
                drive_modified_str = meta.get("modifiedTime")
                drive_ts = parse_iso_datetime(drive_modified_str)

                # Check for timestamp drift
                if drive_ts and local_ts:
                    diff = (drive_ts - local_ts).total_seconds()
                    # Apply 2-second tolerance for time precision differences
                    if diff > 2:
                        drifts.append(
                            {
                                "item": item,
                                "local_modified": item["timestamp_local"],
                                "drive_modified": drive_modified_str,
                                "diff_seconds": diff,
                            }
                        )
                        drift_detected = True
                elif drive_ts and not local_ts:
                    drifts.append(
                        {
                            "item": item,
                            "local_modified": "None",
                            "drive_modified": drive_modified_str,
                            "diff_seconds": 0,
                        }
                    )
                    drift_detected = True
        except HTTPError as e:
            if e.code == 404:
                missing_on_drive.append((item, "not found on drive / 404"))
                drift_detected = True
            else:
                query_errors.append((item, f"HTTP {e.code}: {e.reason}"))
                print(
                    f"[drift-check] Error: Failed to query metadata for {title} ({fid}): {e}",
                    file=sys.stderr,
                )
        except URLError as e:
            query_errors.append((item, str(e)))
            print(
                f"[drift-check] Error: Failed to query metadata for {title} ({fid}): {e}",
                file=sys.stderr,
            )

    # 4. Report check results
    print("=== OKF Google Drive Drift Check ===")
    print(f"Total files checked: {checked_count}")

    if drifts:
        print(
            f"\n❌ DRIFT DETECTED: {len(drifts)} files have newer versions on Google Drive:"
        )
        for d in drifts:
            item = d["item"]
            rel_path = item["path"].relative_to(OKF_BASE)
            print(f"  • {item['title']} (ID: {item['id']})")
            print(f"    Local mirror timestamp:  {d['local_modified']}")
            print(f"    Google Drive modified:   {d['drive_modified']}")
            print(f"    File: {rel_path}")

    if missing_on_drive:
        print(
            f"\n❌ MISSING/TRASHED ON DRIVE: {len(missing_on_drive)} files exist locally but are missing on Drive:"
        )
        for item, reason in missing_on_drive:
            rel_path = item["path"].relative_to(OKF_BASE)
            print(f"  • {item['title']} (ID: {item['id']}) — {reason}")
            print(f"    File: {rel_path}")

    if query_errors:
        print(
            f"\n❌ QUERY ERRORS: {len(query_errors)} Google Drive metadata lookups failed:"
        )
        for item, reason in query_errors[:10]:
            rel_path = item["path"].relative_to(OKF_BASE)
            print(f"  • {item['title']} (ID: {item['id']}) — {reason}")
            print(f"    File: {rel_path}")
        if len(query_errors) > 10:
            print(f"  … {len(query_errors) - 10} more query errors omitted")
        print("\n❌ STATUS: ERROR. Google Drive metadata could not be fully checked.")
        return 2

    if not drift_detected:
        print("\n✅ STATUS: HEALTHY. All local files are in sync with Google Drive.")
        return 0
    else:
        print("\n❌ STATUS: DRIFT DETECTED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
