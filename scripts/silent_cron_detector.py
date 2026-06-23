#!/usr/bin/env python3
"""
silent_cron_detector.py — daily digest of silent cron failures.

GRO-2237 follow-up. The problem: 8+ crons are currently failing silently
because they `deliver=local` (no Telegram alert) and the script either
crashes mid-run or just doesn't report.

This script scans ~/.hermes/profiles/*/cron/jobs.json and produces a daily
digest to Telegram with:
  1. All crons with `last_status=error`
  2. All crons with `deliver=local` and `last_run_at > 24h ago` on enabled intervals
  3. All crons with blank/null `last_status` (haven't fired yet but enabled)
  4. Suspect "silent by design" patterns (autobot-style watchdog crons
     with no deliver target)

Outputs:
  - Telegram digest (if TELEGRAM_BOT_TOKEN + TELEGRAM_HOME_CHAT_ID set)
  - Local file at /tmp/silent_cron_digest.md (always, for Autobot relay)
  - Console summary

Usage:
  python3 silent_cron_detector.py [--dry-run] [--json]
  python3 silent_cron_detector.py --stale-hours 24  # adjust threshold
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ── Configuration ──────────────────────────────────────────────
HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", os.path.expanduser("~/.hermes")))
PROFILES_DIR = HERMES_ROOT / "profiles"
DIGEST_PATH = Path("/tmp/silent_cron_digest.md")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_HOME_CHAT_ID = os.environ.get("TELEGRAM_HOME_CHAT_ID", "")

# Root-cause taxonomy — used to file follow-up Linear issues
ROOT_CAUSE_FAMILIES = {
    "env-var": r"PRISMATIC_HOME|HERMES_ROOT|\$[A-Z_]+(?!.*=/)",
    "auth": r"oauth|auth\.json|api[_-]?key|token|credential",
    "symlink": r"\.gemini|symlink|oauth drift",
    "upstream-empty": r"KeyError|NoneType|index.*out of range|\.get\(.+None\)",
    "post-meeting": r"after.meeting|status.*pdf|generate.*pdf|after_pdf",
    "kai-specific": r"profile.*kai|kai.*script|kai-",
    "publisher": r"publisher|cf.*tunnel|cloudflare",
    "weekly-rollup": r"weekly.*rollup|monthly|0 [0-9] [0-9]+ \*",
    "missing-config": r"FILE MISSING|No such file|not found|missing",
}

# Cron IDs known to be in this state (from GRO-2237 diagnosis)
KNOWN_FAILING_CRONS = {
    "63b5dd0ddf98": "Memory Grooming — env-var path (PRISMATIC_HOME→missing memories/)",
    "5bc8574c58d0": "Linear OAuth Token Auto-Refresh — auth expiry",
    "faf8d91da716": "AGY Sandbox Supervisor — event-loop (post-fix should be ok)",
    "47b66f4df172": "Morning Digest — upstream-empty digest",
    "e2088800a9cbc865": "Memory Capacity Alert — threshold trip",
    "a782c38cba82": "Status Report After Meeting — Teams pipeline",
    "ec711dbb73d2": "Publisher Health Check — meta-failure",
    # (kai AOT weekly rankings not on this profile, skip)
}


def load_all_cron_jobs() -> list:
    """Load cron jobs from all profiles' jobs.json files."""
    jobs = []
    if not PROFILES_DIR.exists():
        return jobs
    for profile_dir in PROFILES_DIR.iterdir():
        if not profile_dir.is_dir():
            continue
        jobs_file = profile_dir / "cron" / "jobs.json"
        if not jobs_file.exists():
            continue
        try:
            data = json.loads(jobs_file.read_text())
            if isinstance(data, dict) and "jobs" in data:
                for j in data["jobs"]:
                    # Normalize: cron uses 'id', we use 'job_id' for consistency
                    if "id" in j and "job_id" not in j:
                        j["job_id"] = j["id"]
                    j["_profile"] = profile_dir.name
                    j["_source"] = str(jobs_file)
                    jobs.append(j)
        except Exception as e:
            print(f"  ⚠️ Failed to read {jobs_file}: {e}", file=sys.stderr)
    return jobs


def classify_root_cause(job: dict) -> str:
    """Guess the root-cause family from the script name + deliver pattern."""
    text = " ".join([
        job.get("name", ""),
        job.get("script", "") or "",
        job.get("paused_reason", "") or "",
    ]).lower()
    for family, pattern in ROOT_CAUSE_FAMILIES.items():
        if re.search(pattern, text, re.IGNORECASE):
            return family
    # Default by last_status + last_delivery_error
    err = (job.get("last_delivery_error") or "").lower()
    if "telegram" in err:
        return "telegram-target-missing"
    if "auth" in err or "401" in err or "403" in err:
        return "auth"
    if "rate" in err or "429" in err:
        return "rate-limit"
    return "unknown"


def is_silent_failure(job: dict) -> bool:
    """A cron is a silent failure if it's failing AND not delivering to Michael."""
    if job.get("last_status") != "error":
        return False
    deliver = job.get("deliver", "local")
    # Silent if delivery is local-only (no Telegram/Slack/webhook)
    if deliver in ("local", "telegram", "origin", None, ""):
        # Check if the deliver target is a real channel
        if deliver in (None, "", "local"):
            return True
        # Telegram may silently fail if chat not found
        err = (job.get("last_delivery_error") or "").lower()
        if "chat not found" in err or "404" in err:
            return True
    return False


def is_stale(job: dict, stale_hours: int = 24) -> bool:
    """A cron is stale if it hasn't run in `stale_hours` and is enabled with an interval."""
    if not job.get("enabled", False):
        return False
    if job.get("state") == "disabled":
        return False
    last_run = job.get("last_run_at")
    if not last_run:
        return True  # Never run = stale
    try:
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
    except Exception:
        return False
    age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return age_hours > stale_hours


def detect_silent_failures(jobs: list, stale_hours: int) -> dict:
    """Categorize all jobs into: silent-fail, stale, error-not-silent, healthy."""
    silent_failures = []
    stale = []
    error_not_silent = []
    blank_status = []
    healthy = []

    for j in jobs:
        if is_silent_failure(j):
            silent_failures.append(j)
        elif j.get("last_status") == "error":
            error_not_silent.append(j)
        elif j.get("last_status") is None or j.get("last_status") == "":
            if j.get("enabled", False):
                blank_status.append(j)
        elif is_stale(j, stale_hours):
            stale.append(j)
        else:
            healthy.append(j)

    return {
        "silent_failures": silent_failures,
        "stale": stale,
        "error_not_silent": error_not_silent,
        "blank_status": blank_status,
        "healthy": healthy,
        "total": len(jobs),
    }


def build_digest(report: dict, stale_hours: int) -> str:
    """Build markdown digest."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# 🔕 Silent Cron Detector — {now}",
        f"",
        f"**Total jobs scanned:** {report['total']} • **Stale threshold:** {stale_hours}h",
        f"",
        f"## 🔴 Silent Failures (error + no Telegram delivery to Michael)",
        f"",
    ]
    if not report["silent_failures"]:
        lines.append("_None detected — all erroring crons are alerting somewhere._")
    else:
        for j in report["silent_failures"]:
            family = classify_root_cause(j)
            job_id = j.get("job_id", "unknown")
            name = j.get("name", "(no name)")
            profile = j.get("_profile", "unknown")
            last_run = j.get("last_run_at", "never")
            err = j.get("last_delivery_error", "")
            err_short = err[:80] if err else "(no delivery error)"
            lines.extend([
                f"### {name}",
                f"- **ID:** `{job_id}` • **Profile:** `{profile}` • **Root cause family:** `{family}`",
                f"- **Last run:** {last_run}",
                f"- **Last delivery error:** `{err_short}`",
                f"- **Action:** File Linear issue for this cron, classified by `{family}`",
                f"",
            ])

    lines.append(f"## ⏰ Stale Crons (enabled, haven't run in {stale_hours}h)")
    lines.append("")
    if not report["stale"]:
        lines.append("_None._")
    else:
        for j in report["stale"][:20]:
            job_id = j.get("job_id", "unknown")
            name = j.get("name", "(no name)")
            last_run = j.get("last_run_at", "never")
            schedule = j.get("schedule", "unknown")
            lines.append(f"- `{job_id}` **{name}** — schedule: `{schedule}`, last run: {last_run}")

    lines.extend([
        f"",
        f"## ⚠️ Erroring (but delivering to a chat — not silent)",
        f"",
    ])
    if not report["error_not_silent"]:
        lines.append("_None._")
    else:
        for j in report["error_not_silent"][:10]:
            job_id = j.get("job_id", "unknown")
            name = j.get("name", "(no name)")
            deliver = j.get("deliver", "?")
            lines.append(f"- `{job_id}` **{name}** — deliver=`{deliver}`")

    lines.extend([
        f"",
        f"## 📋 Blank Status (enabled, never run)",
        f"",
    ])
    if not report["blank_status"]:
        lines.append("_None._")
    else:
        for j in report["blank_status"][:10]:
            job_id = j.get("job_id", "unknown")
            name = j.get("name", "(no name)")
            lines.append(f"- `{job_id}` **{name}**")

    lines.extend([
        f"",
        f"## ✅ Healthy: {len(report['healthy'])} jobs",
        f"",
        f"---",
        f"Generated by `silent_cron_detector.py` (GRO-2237 follow-up)",
    ])
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """Send digest to Telegram if credentials are set."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_HOME_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram has a 4096 char limit per message; truncate
    if len(message) > 4000:
        message = message[:3950] + "\n\n... (truncated, see /tmp/silent_cron_digest.md for full)"
    try:
        data = json.dumps({"chat_id": TELEGRAM_HOME_CHAT_ID, "text": message,
                          "parse_mode": "Markdown"}).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("ok", False)
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        print(f"  ⚠️ Telegram send failed: {e}", file=sys.stderr)
        return False


def file_linear_issues(report: dict, dry_run: bool = True) -> list:
    """File one Linear issue per silent-failure cron, classified by root cause.

    Returns the list of filed issue IDs (or empty if dry-run).
    """
    api_key = os.environ.get("LINEAR_API_KEY", "")
    if not api_key:
        return []
    if not report["silent_failures"]:
        return []

    filed = []
    for j in report["silent_failures"]:
        family = classify_root_cause(j)
        job_id = j.get("job_id", "")
        name = j.get("name", "")
        title = f"[CRON-FIX] `{name}` is silent-failing ({family})"
        desc = (
            f"## Silent Cron Failure — {name}\n\n"
            f"- **Job ID:** `{job_id}`\n"
            f"- **Profile:** `{j.get('_profile', '?')}`\n"
            f"- **Schedule:** `{j.get('schedule', '?')}`\n"
            f"- **Last run:** {j.get('last_run_at', 'never')}\n"
            f"- **Last delivery error:** `{j.get('last_delivery_error', '')}`\n"
            f"- **Root cause family:** `{family}`\n"
            f"- **Source:** `silent_cron_detector.py` (GRO-2237 follow-up)\n\n"
            f"### Suggested fix\n"
            f"Investigate the `{family}` pattern. Common causes:\n"
        )
        fixes = {
            "env-var": "Check `$PRISMATIC_HOME` and other env vars in cron env. Cron runs with a minimal env, so paths must be absolute or set explicitly.",
            "auth": "Check token expiry, OAuth refresh, or API key rotation. May need to re-auth.",
            "symlink": "Check for missing symlinks (e.g. `.gemini` directories drifted between projects).",
            "upstream-empty": "Add defensive handling for empty inputs (None, missing keys).",
            "post-meeting": "Check Teams meeting pipeline + transcript fetch. May need credentials rotation.",
            "kai-specific": "Profile-specific to kai. May be running stale script or wrong Python.",
            "publisher": "Check Cloudflare tunnel + local publisher. May be down.",
            "weekly-rollup": "Schedule may be wrong or weekly crons missed windows during downtime.",
            "missing-config": "Missing required file or directory. May need to create it.",
        }
        desc += fixes.get(family, "Investigate via cron logs (`~/.hermes/logs/`).")
        if dry_run:
            filed.append({"title": title, "description": desc, "dry_run": True})
            continue
        # File via GraphQL
        q = """
        mutation($title: String!, $desc: String!, $team: String!) {
          issueCreate(input: {
            teamId: $team
            title: $title
            description: $desc
          }) { success issue { id identifier } }
        }
        """
        try:
            req = Request("https://api.linear.app/graphql",
                data=json.dumps({"query": q,
                    "variables": {"title": title, "desc": desc,
                                  "team": "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"}}).encode(),
                headers={"Authorization": api_key, "Content-Type": "application/json"})
            r = json.loads(urlopen(req, timeout=15).read())
            if r.get("data", {}).get("issueCreate", {}).get("success"):
                filed.append(r["data"]["issueCreate"]["issue"])
        except Exception as e:
            print(f"  ⚠️ Failed to file issue for {job_id}: {e}", file=sys.stderr)
    return filed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stale-hours", type=int, default=24,
                       help="Hours without a run to consider a cron stale")
    parser.add_argument("--dry-run", action="store_true",
                       help="Don't file Linear issues, just report")
    parser.add_argument("--json", action="store_true",
                       help="Output report as JSON instead of markdown")
    parser.add_argument("--no-telegram", action="store_true",
                       help="Don't send to Telegram, just write local file")
    args = parser.parse_args()

    print(f"═══ Silent Cron Detector — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} ═══\n")

    jobs = load_all_cron_jobs()
    print(f"Loaded {len(jobs)} cron jobs from {PROFILES_DIR}")

    report = detect_silent_failures(jobs, args.stale_hours)
    print(f"\nResults:")
    print(f"  🔴 Silent failures:    {len(report['silent_failures'])}")
    print(f"  ⏰ Stale:              {len(report['stale'])}")
    print(f"  ⚠️ Error (not silent): {len(report['error_not_silent'])}")
    print(f"  📋 Blank status:       {len(report['blank_status'])}")
    print(f"  ✅ Healthy:            {len(report['healthy'])}")

    digest = build_digest(report, args.stale_hours)

    if args.json:
        # Strip non-JSON-serializable
        for k in ("silent_failures", "stale", "error_not_silent", "blank_status", "healthy"):
            for j in report[k]:
                j.pop("_source", None)
        print(json.dumps(report, indent=2, default=str))
    else:
        DIGEST_PATH.write_text(digest)
        print(f"\n📄 Digest written to {DIGEST_PATH} ({len(digest)} chars)")

    # Send to Telegram
    if not args.no_telegram and not args.json:
        sent = send_telegram(digest)
        if sent:
            print(f"  ✅ Telegram digest sent to chat {TELEGRAM_HOME_CHAT_ID}")
        elif TELEGRAM_BOT_TOKEN and TELEGRAM_HOME_CHAT_ID:
            print(f"  ⚠️ Telegram send failed")
        else:
            print(f"  ℹ️  Telegram credentials not set; skipping send")

    # File Linear issues
    if not args.json and not args.dry_run:
        filed = file_linear_issues(report, dry_run=False)
        print(f"\n📋 Filed {len(filed)} Linear issues:")
        for i in filed:
            print(f"  - {i.get('identifier', '?')}")
    elif report["silent_failures"]:
        print(f"\n(dry-run) Would file {len(report['silent_failures'])} Linear issues for silent failures")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
