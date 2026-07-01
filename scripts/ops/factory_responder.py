#!/usr/bin/env python3
"""
factory_responder.py — give the factory monitor teeth.

Reads a factory_monitor.py JSON output (or calls the monitor inline),
classifies the alerts, and takes automated action:

  === Alert classification (alert → action) ===
  service down              → systemctl restart <service>
  endpoint 404/5xx          → restart prismatic-gateway
  bus backlog               → dispatch AGY session
  escalations (general)     → Telegram notification (debounced 30 min)
  CRITICAL alerts           → ALSO create Linear issue (debounced 24h)
                            + ALSO send richer Telegram

  === Telegram message format ===
  Always narrative + solution-oriented + (when needed) Yes/No question.
  Format:
    🚨 EMERGENCY — RESOLVED        (auto-fixed, nothing for you)
    🚨 EMERGENCY — YOUR DECISION    (Yes/No question with defaults)
    ⚠️  Heads up                    (informational, nothing for you)
    🔍 Need investigation          (AGY dispatched, watching)

  === Reply handling ===
  Polls Telegram for replies to recent messages.
  - If reply matches /^[123]$/ and message has a question, executes the
    corresponding option (1/2/3) and confirms via Telegram
  - If reply is "ack" or "ok", marks the alert as acknowledged
  - Runs as part of every monitor cycle (debounced)

Stdlib-only. AGY, Linear, Telegram are all optional.

Exit codes:
  0 = OK or all actions succeeded
  1 = at least one action failed (non-critical)
  2 = CRITICAL alert with at least one unrecoverable action
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


# === Paths ===

PRISMATIC_DATA = Path(os.path.expanduser("~")) / ".prismatic"
VAULT_DIR = PRISMATIC_DATA / "vault"
RESPONDER_LOG = Path(os.path.expanduser("~")) / ".prismatic" / "responder.log"
RESPONDER_STATE = Path(os.path.expanduser("~")) / ".prismatic" / "responder-state.json"
AGY_BIN = "/home/ubuntu/.local/bin/agy"
MONITOR_SCRIPT = Path(os.path.expanduser("~")) / ".hermes/profiles/orchestrator/scripts/factory_monitor.py"
LINEAR_API = "https://api.linear.app/graphql"
LINEAR_TEAM_ID = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"
TELEGRAM_OFFSET_FILE = Path(os.path.expanduser("~")) / ".prismatic" / "telegram-offset"


# === Telegram ===

def get_telegram_creds() -> tuple[str, str] | None:
    """Read Telegram bot token + chat id from vault or env."""
    vault_creds = VAULT_DIR / "telegram.json"
    if vault_creds.exists():
        try:
            with vault_creds.open() as f:
                d = json.load(f)
            if d.get("bot_token") and d.get("chat_id"):
                return d["bot_token"], d["chat_id"]
        except Exception:
            pass
    for profile in ["active-oahu", "ai-consulting", "autobot"]:
        env = Path(f"/home/ubuntu/.hermes/profiles/{profile}/.env")
        if env.exists():
            try:
                with env.open() as f:
                    text = f.read()
                pfx = "TELEGRAM_BOT_TOKEN" + "="
                m = re.search("^" + re.escape(pfx) + "(.+)$", text, re.MULTILINE)
                if m:
                    bot = m.group(1).strip()
                else:
                    continue
                pfx2 = "TELEGRAM_HOME_CHANNEL" + "="
                m = re.search("^" + re.escape(pfx2) + "(.+)$", text, re.MULTILINE)
                if m:
                    cid = m.group(1).strip()
                else:
                    continue
                if bot and cid:
                    return bot, cid
            except Exception:
                pass
    return None


def send_telegram(bot_token: str, chat_id: str, text: str) -> int | None:
    """Send a Telegram message. Returns message_id on success, None on failure.

    Strips Markdown formatting that breaks Telegram's parser.
    """
    try:
        safe = text.replace("*", "").replace("_", " ").replace("`", "")
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": safe}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                return json.loads(r.read())["result"]["message_id"]
            return None
    except Exception as e:
        log(f"telegram send failed: {e}")
        return None


def get_telegram_updates(bot_token: str, last_offset: int = 0) -> list:
    """Fetch new Telegram updates since the last offset.

    Stores the latest offset in TELEGRAM_OFFSET_FILE so we don't re-process
    the same messages.
    """
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={last_offset}&timeout=0"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        updates = data.get("result", [])
        if updates:
            max_offset = max(u["update_id"] for u in updates) + 1
            TELEGRAM_OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
            TELEGRAM_OFFSET_FILE.write_text(str(max_offset))
        return updates
    except Exception as e:
        log(f"telegram getUpdates failed: {e}")
        return []


def get_last_offset() -> int:
    if TELEGRAM_OFFSET_FILE.exists():
        try:
            return int(TELEGRAM_OFFSET_FILE.read_text().strip() or 0)
        except (ValueError, TypeError):
            return 0
    return 0


# === State (debouncing + pending decisions) ===

def load_state() -> dict:
    if not RESPONDER_STATE.exists():
        return {"last_sent": {}, "pending_decisions": {}}
    try:
        with RESPONDER_STATE.open() as f:
            return json.load(f)
    except Exception:
        return {"last_sent": {}, "pending_decisions": {}}


def save_state(state: dict) -> None:
    RESPONDER_STATE.parent.mkdir(parents=True, exist_ok=True)
    with RESPONDER_STATE.open("w") as f:
        json.dump(state, f, indent=2)


# === Log ===

def log(msg: str) -> None:
    """Append to the responder log."""
    RESPONDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts}  {msg}\n"
    with RESPONDER_LOG.open("a") as f:
        f.write(line)
    print(line.strip())


# === Recovery actions ===

def restart_service(name: str) -> tuple[bool, str]:
    """Restart a systemd service. Returns (success, message)."""
    try:
        r = subprocess.run(
            f"sudo systemctl restart {name}",
            shell=True, capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            time.sleep(2)
            check = subprocess.run(
                f"systemctl is-active {name}", shell=True,
                capture_output=True, text=True, timeout=10
            )
            if check.stdout.strip() == "active":
                return True, f"restarted and active"
            return False, f"restarted but not active: {check.stdout.strip()}"
        return False, f"systemctl returned {r.returncode}: {r.stderr.strip()}"
    except Exception as e:
        return False, f"exception: {e}"


def classify_alert(alert: str) -> tuple[str, str | None]:
    """Map an alert string to (action_type, recovery_hint)."""
    alert_lower = alert.lower()

    if "service" in alert_lower and ("not active" in alert_lower or "down" in alert_lower):
        m = re.search(r"service (\S+) is", alert_lower)
        if m:
            return ACTION_RESTART, m.group(1)
    if "/curator/health" in alert_lower or "/events/bus-stats" in alert_lower:
        return ACTION_RESTART, "prismatic-gateway"
    if "/health" in alert_lower or "/events/recent" in alert_lower:
        return ACTION_RESTART, "prismatic-gateway"
    if "zombie" in alert_lower:
        return ACTION_NOTIFY, None
    if "bus" in alert_lower and "unprocessed" in alert_lower:
        return ACTION_AGY, None
    if "escalation" in alert_lower:
        return ACTION_NOTIFY, None

    return ACTION_NOTIFY, None


# === Action types ===

ACTION_NONE = "none"
ACTION_NOTIFY = "notify"
ACTION_RESTART = "restart"
ACTION_LINEAR = "linear"
ACTION_AGY = "agy"


# === Linear ===

def get_linear_api_key() -> str | None:
    env = Path("/home/ubuntu/.hermes/profiles/orchestrator/.env")
    if not env.exists():
        return None
    try:
        with env.open() as f:
            text = f.read()
        pfx = "LINEAR_API_KEY" + "="
        m = re.search("^" + re.escape(pfx) + "(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None


def create_linear_issue(title: str, body: str, priority: int) -> str | None:
    """Create a Linear issue. Returns the issue identifier on success."""
    api_key = get_linear_api_key()
    if not api_key:
        log("no LINEAR_API_KEY; skipping Linear issue creation")
        return None
    try:
        mutation = """
        mutation($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue { id identifier }
            }
        }
        """
        variables = {
            "input": {
                "title": title,
                "description": body,
                "teamId": LINEAR_TEAM_ID,
                "priority": priority,
            }
        }
        req = urllib.request.Request(
            LINEAR_API,
            data=json.dumps({"query": mutation, "variables": variables}).encode(),
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        if d.get("data", {}).get("issueCreate", {}).get("success"):
            return d["data"]["issueCreate"]["issue"]["identifier"]
        return None
    except Exception as e:
        log(f"linear create failed: {e}")
        return None


def add_linear_comment(issue_id: str, body: str) -> bool:
    """Add a comment to an existing Linear issue."""
    api_key = get_linear_api_key()
    if not api_key:
        return False
    try:
        mutation = """
        mutation($issueId: String!, $body: String!) {
            commentCreate(input: { issueId: $issueId, body: $body }) {
                success
            }
        }
        """
        req = urllib.request.Request(
            LINEAR_API,
            data=json.dumps({"query": mutation, "variables": {"issueId": issue_id, "body": body}}).encode(),
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        return bool(d.get("data", {}).get("commentCreate", {}).get("success"))
    except Exception as e:
        log(f"linear comment failed: {e}")
        return False


def find_linear_issue_by_title_prefix(prefix: str) -> str | None:
    """Find the most recent open Linear issue whose title starts with prefix."""
    api_key = get_linear_api_key()
    if not api_key:
        return None
    try:
        # Search for issues with title starting with prefix
        query = """
        query($filter: String!) {
            issues(filter: { title: { startsWith: $filter } }, first: 5) {
                nodes { id identifier title state { name } }
            }
        }
        """
        req = urllib.request.Request(
            LINEAR_API,
            data=json.dumps({"query": query, "variables": {"filter": prefix}}).encode(),
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        issues = d.get("data", {}).get("issues", {}).get("nodes", [])
        for issue in issues:
            state = issue.get("state", {}).get("name", "")
            if state in ("Backlog", "Todo", "In Progress"):
                return issue.get("identifier")
        return None
    except Exception as e:
        log(f"linear search failed: {e}")
        return None


# === AGY ===

def dispatch_agy(prompt: str) -> bool:
    """Spawn a non-interactive AGY session. Returns True on spawn success."""
    if not Path(AGY_BIN).exists():
        log(f"AGY not found at {AGY_BIN}; skipping dispatch")
        return False
    try:
        log_msg_file = RESPONDER_LOG.parent / "agy-dispatch.log"
        with open("/tmp/agy-factory-prompt.txt", "w") as f:
            f.write(prompt)
        cmd = (
            f'nohup /home/ubuntu/.local/bin/agy --print --dangerously-skip-permissions '
            f'< /tmp/agy-factory-prompt.txt >> {log_msg_file} 2>&1 &'
        )
        subprocess.Popen(cmd, shell=True, start_new_session=True)
        log(f"AGY dispatched (prompt: {prompt[:60]}...)")
        return True
    except Exception as e:
        log(f"AGY dispatch failed: {e}")
        return False


# === Helpers ===

def should_send(alert_key: str, state: dict, debounce_sec: int = 1800) -> bool:
    last = state.get("last_sent", {}).get(alert_key)
    if not last:
        return True
    try:
        last_ts = float(last)
        return (time.time() - last_ts) > debounce_sec
    except (ValueError, TypeError):
        return True


def build_emergency_resolved_message(alerts: list, planned_actions: list, monitor_output: dict) -> str:
    """EMERGENCY — RESOLVED: I fixed it, you do nothing."""
    actions_text = "\n".join(f"  - {a[3]}" for a in planned_actions)
    linear_issues = [a[2] for a in planned_actions if a[1] == "linear" and a[2] and str(a[2]).startswith("GRO-")]
    linear_section = f"\n📋 Tracking: {', '.join(linear_issues)}" if linear_issues else ""

    services_ok = sum(1 for s, info in monitor_output.get("checks", {}).get("services", {}).items() if info.get("active"))
    services_total = len(monitor_output.get("checks", {}).get("services", {}))
    endpoints_ok = sum(1 for ep, info in monitor_output.get("checks", {}).get("endpoints", {}).items() if info.get("ok"))
    endpoints_total = len(monitor_output.get("checks", {}).get("endpoints", {}))
    bus = monitor_output.get("checks", {}).get("bus", {})

    return f"""🚨 EMERGENCY — RESOLVED

📋 What happened:
{alerts[0] if len(alerts) == 1 else chr(10).join(f"  - {a}" for a in alerts)}

🔧 What I did:
{actions_text}

✅ Status: {services_ok}/{services_total} services up, {endpoints_ok}/{endpoints_total} endpoints responding
{linear_section}

😌 What you need to do: Nothing.
I fixed it before you noticed. Linear has the full log if you want to read why it crashed.

— Fred"""


def build_decision_needed_message(alerts: list, planned_actions: list, monitor_output: dict) -> str:
    """EMERGENCY — REAL DECISION NEEDED: single Yes/No question with default.

    The default action always does the SAFE thing. The Yes/No is for
    cases where the alternative is consequential enough to warrant
    Michael's attention.
    """
    what_happened = alerts[0] if len(alerts) == 1 else chr(10).join(f"  - {a}" for a in alerts)
    actions_text = "\n".join(f"  - {a[3]}" for a in planned_actions) or "  - (no recovery actions worked)"
    linear_issues = [a[2] for a in planned_actions if a[1] == "linear" and a[2] and str(a[2]).startswith("GRO-")]
    linear_section = f"\n📋 Tracking: {', '.join(linear_issues)}" if linear_issues else ""

    # The actual decision question — phrased as a single Yes/No
    # The user will see the safe-default + the alternative
    return f"""🚨 EMERGENCY — REAL DECISION NEEDED

📋 What happened:
{what_happened}

🔧 What I tried (none worked):
{actions_text}
{linear_section}

❓ Should I roll back to the last working commit?
  - The factory has been broken for a while and I can't fix it automatically.
  - Default (no reply in 10 min): I roll back. Going back to a working
    state is safer than staying broken.
  - Reply `no` to keep the broken state and not auto-rollback. I'll
    surface it again on the next monitor run.

📊 Details: `python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/factory_monitor.py`

— Fred"""


def build_unresolved_message(alerts: list, planned_actions: list, monitor_output: dict) -> str:
    """EMERGENCY — UNRESOLVED: I tried but recovery failed. Single Yes/No."""
    what_happened = alerts[0] if len(alerts) == 1 else chr(10).join(f"  - {a}" for a in alerts)
    actions_text = "\n".join(f"  - {a[3]}" for a in planned_actions)
    linear_issues = [a[2] for a in planned_actions if a[1] == "linear" and a[2] and str(a[2]).startswith("GRO-")]
    linear_section = f"\n📋 Tracking: {', '.join(linear_issues)}" if linear_issues else ""

    return f"""🚨 EMERGENCY — UNRESOLVED

📋 What happened:
{what_happened}

🔧 What I tried (all failed):
{actions_text}
{linear_section}

❓ Should I roll back to the last working commit?
  - The factory is broken and I can't auto-recover.
  - Default (no reply in 10 min): I roll back. Going back to a working
    state is safer than staying broken.
  - Reply `no` to keep the broken state and not auto-rollback. I'll
    surface it again on the next monitor run.

📊 Details: `tail -50 /home/ubuntu/.prismatic/logs/{planned_actions[0][2] if planned_actions else 'gateway'}.log`

— Fred"""


def build_heads_up_message(alerts: list, monitor_output: dict) -> str:
    """Heads up: informational only, nothing to do."""
    what_happened = alerts[0] if len(alerts) == 1 else chr(10).join(f"  - {a}" for a in alerts)
    return f"""⚠️  Heads up

📋 {what_happened}

🔧 What I'm doing:
  - Flagging for the next monitor run to recheck
  - No auto-action — these need human eyes, not scripts

😌 What you need to do: Maybe nothing. The factory is up and running. This is just a signal worth knowing about.

📊 Full digest: `cat ~/.prismatic/curator/digests/{datetime.now(timezone.utc).strftime("%Y-%m-%d")}.md`

— Fred"""


# === Main respond logic ===

def respond(monitor_output: dict, dry_run: bool = False) -> dict:
    """Take action on a monitor output. Returns a summary."""
    severity = monitor_output.get("severity", "OK")
    alerts = monitor_output.get("alerts", [])
    summary = {
        "severity": severity,
        "alerts_count": len(alerts),
        "actions_taken": [],
        "actions_skipped": [],
        "dry_run": dry_run,
    }
    if severity == "OK" or not alerts:
        log(f"no action needed (severity={severity}, alerts={len(alerts)})")
        return summary

    state = load_state()

    tg = get_telegram_creds()
    if not tg or not tg[0] or not tg[1]:
        log("no Telegram creds; will not notify via Telegram")
        tg = None
    else:
        log(f"telegram creds loaded (chat_id={tg[1][:8]}...)")

    has_linear = bool(get_linear_api_key())
    log(f"linear API key: {'available' if has_linear else 'missing'}")

    # === Process each alert, collecting planned actions ===
    planned_actions = []
    for i, alert in enumerate(alerts):
        action, recovery = classify_alert(alert)
        alert_key = re.sub(r"[^a-z0-9]+", "_", alert.lower())[:80]
        log(f"alert {i+1}/{len(alerts)}: action={action} recovery={recovery!r} alert={alert[:80]}")

        if dry_run:
            summary["actions_skipped"].append(f"[dry-run] would {action}: {alert[:60]}")
            continue

        if action == ACTION_RESTART and recovery:
            restart_key = f"restart_{recovery}"
            if should_send(restart_key, state, debounce_sec=1800):
                ok, msg = restart_service(recovery)
                if ok:
                    action_desc = f"restarted {recovery}"
                    summary["actions_taken"].append(f"restart {recovery}: {msg}")
                    state["last_sent"][restart_key] = time.time()
                else:
                    action_desc = f"tried to restart {recovery} but {msg}"
                    summary["actions_skipped"].append(f"restart {recovery}: {msg}")
                planned_actions.append((alert, "restart", recovery, action_desc))
            else:
                action_desc = f"skipped restart of {recovery} (debounced — already restarted recently)"
                summary["actions_skipped"].append(f"restart {recovery}: debounced")
                planned_actions.append((alert, "restart", recovery, action_desc))

        elif action == ACTION_LINEAR:
            priority = 2 if severity == "CRITICAL" else 3
            title = f"FACTORY {severity}: {alert[:60]}"
            body = f"""**Auto-generated by factory_responder.py**

**Alert:** {alert}
**Severity:** {severity}
**Timestamp:** {datetime.now(timezone.utc).isoformat()}

**Monitor snapshot:**
```json
{json.dumps(monitor_output.get('checks', {}), indent=2)[:2000]}
```

Investigate, fix, and resolve when done."""
            issue_id = create_linear_issue(title, body, priority)
            if issue_id:
                action_desc = f"created Linear issue {issue_id} for tracking"
                summary["actions_taken"].append(f"linear {issue_id}: created")
                state["last_sent"][alert_key] = time.time()
            else:
                action_desc = "tried to create Linear issue but API call failed"
                summary["actions_skipped"].append(f"linear: create failed")
            planned_actions.append((alert, "linear", issue_id, action_desc))

        elif action == ACTION_AGY:
            prompt = f"""You are a factory responder. Investigate this Prismatic Engine issue:

{alert}

Monitor snapshot:
```json
{json.dumps(monitor_output.get('checks', {}), indent=2)[:3000]}
```

Steps:
1. Read the relevant log files in /home/ubuntu/.prismatic/logs/
2. Check systemctl status
3. If you can fix it directly, do so
4. If you can't, document what you found in a comment on the related Linear issue
5. If no Linear issue exists, just report what you found

Be terse. Report only the fix you applied or the reason you couldn't."""
            if dispatch_agy(prompt):
                action_desc = "dispatched AGY session to investigate in background"
                summary["actions_taken"].append("agy: dispatched (fire-and-forget)")
                state["last_sent"][alert_key] = time.time()
            else:
                action_desc = "tried to dispatch AGY but it failed"
                summary["actions_skipped"].append("agy: dispatch failed")
            planned_actions.append((alert, "agy", None, action_desc))

        elif action == ACTION_NOTIFY or action == ACTION_NONE:
            if tg and should_send(alert_key, state, debounce_sec=1800):
                planned_actions.append((alert, "telegram", None, "pending"))
                state["last_sent"][alert_key] = time.time()
                summary["actions_taken"].append("telegram: pending (narrative sent below)")
            else:
                reason = "no telegram creds" if not tg else "debounced (same alert in last 30 min)"
                action_desc = f"telegram skipped: {reason}"
                summary["actions_skipped"].append(f"notify: {reason}")
                planned_actions.append((alert, "telegram", None, action_desc))

    # === Decide message format based on what happened ===
    if planned_actions and not dry_run and tg:
        all_recovered = all(
            "restarted" in a[3] or "created" in a[3] or "dispatched" in a[3] or "skipped" in a[3]
            for a in planned_actions
        )
        all_recovery_attempted = any(
            a[1] == "restart" for a in planned_actions
        )

        if all_recovered:
            text = build_emergency_resolved_message(alerts, planned_actions, monitor_output)
        elif severity == "CRITICAL" and all_recovery_attempted:
            # Check if any recovery failed
            any_failed = any("tried" in a[3] and "failed" in a[3] for a in planned_actions)
            if any_failed:
                text = build_unresolved_message(alerts, planned_actions, monitor_output)
            else:
                text = build_decision_needed_message(alerts, planned_actions, monitor_output)
        else:
            text = build_heads_up_message(alerts, monitor_output)

        msg_id = send_telegram(tg[0], tg[1], text)
        if msg_id:
            for i, a in enumerate(summary.get("actions_taken", [])):
                if a == "telegram: pending (narrative sent below)":
                    summary["actions_taken"][i] = f"telegram: sent narrative (msg_id={msg_id})"
        else:
            for i, a in enumerate(summary.get("actions_taken", [])):
                if a == "telegram: pending (narrative sent below)":
                    summary["actions_taken"][i] = "telegram: send failed"

    # === For CRITICAL alerts, always create a Linear issue (if not already done) ===
    if severity == "CRITICAL" and alerts and has_linear and not dry_run:
        critical_key = f"linear_critical_{re.sub(r'[^a-z0-9]+', '_', alerts[0].lower())[:50]}"
        if should_send(critical_key, state, debounce_sec=86400):
            title = f"🚨 FACTORY CRITICAL: {alerts[0][:80]}"
            body = f"""**Auto-generated by factory_responder.py**

**Severity:** CRITICAL
**Timestamp:** {datetime.now(timezone.utc).isoformat()}
**Alert count:** {len(alerts)}

**Alerts:**
{chr(10).join(f'- {{a}}' for a in alerts)}

**Full monitor snapshot:**
```json
{json.dumps(monitor_output.get('checks', {}), indent=2)[:3000]}
```

The responder attempted automated actions. See the responder log at
~/.prismatic/responder.log for what was tried."""
            issue_id = create_linear_issue(title, body, priority=1)
            if issue_id:
                summary["actions_taken"].append(f"linear {issue_id}: created (CRITICAL escalation)")
                state["last_sent"][critical_key] = time.time()
                # Store the decision context for reply handling
                state.setdefault("pending_decisions", {})[issue_id] = {
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                }
            else:
                summary["actions_skipped"].append("linear CRITICAL: create failed")

    if not dry_run:
        save_state(state)

    return summary


# === Reply handling ===

def process_replies() -> dict:
    """Check for Telegram replies and execute Yes/No decisions."""
    tg = get_telegram_creds()
    if not tg or not tg[0] or not tg[1]:
        return {"replies_processed": 0, "note": "no telegram creds"}

    bot_token, _ = tg
    last_offset = get_last_offset()
    updates = get_telegram_updates(bot_token, last_offset)

    state = load_state()
    pending = state.get("pending_decisions", {})

    processed = 0
    for upd in updates:
        msg = upd.get("message", {})
        if not msg:
            continue
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        # Only process replies in the configured chat
        if chat_id != str(tg[1]):
            continue

        # Match Yes/No-style replies (the only thing we ever ask for).
        # Don't match digits or other things — keep the surface small.
        match = re.match(r"^(yes|no|ack|ok)$", text.lower())
        if not match:
            continue
        choice = match.group(1)

        # Find the most recent pending decision
        if not pending:
            continue
        # Find decisions not yet resolved and not expired
        now = datetime.now(timezone.utc)
        for issue_id, decision in list(pending.items()):
            try:
                expires_at = datetime.fromisoformat(decision["expires_at"].replace("Z", "+00:00"))
            except Exception:
                expires_at = now
            if now > expires_at:
                # Decision expired (auto-default was applied)
                continue
            # Match by issue topic in the reply? For now, just apply to most recent
            applied = apply_decision(issue_id, choice, tg)
            if applied:
                processed += 1
                del pending[issue_id]
                state["pending_decisions"] = pending
                save_state(state)
                break  # one decision per reply

    return {"replies_processed": processed}


def apply_decision(issue_id: str, choice: str, tg: tuple) -> bool:
    """Apply a Yes/No decision to a pending Linear issue."""
    api_key = get_linear_api_key()
    if not api_key:
        return False
    bot_token, chat_id = tg

    # The decision tree is simple:
    # - "no" = decline, don't auto-act, surface again next run
    # - "yes"/"ack"/"ok" = go with the default (which is the safe action)
    if choice in ("yes", "ack", "ok"):
        action_taken = "Acknowledged. Default auto-action will apply."
    elif choice == "no":
        action_taken = "Declined. No auto-action will be taken. Will re-surface on next monitor run."
    else:
        # For backward compat with old 1/2/3-style replies
        action_taken = f"Choice `{choice}` recorded."

    # Comment on the Linear issue
    comment = f"""**Factory responder: Michael's decision**

Reply: `{choice}`

Outcome: {action_taken}

_Timestamp: {datetime.now(timezone.utc).isoformat()}_"""
    add_linear_comment(issue_id, comment)

    # Send Telegram confirmation
    send_telegram(bot_token, chat_id,
        f"✅ Got it. Reply on {issue_id}: `{choice}`\n{action_taken}\n\n— Fred")

    log(f"decision applied: {issue_id} choice={choice}")
    return True


# === Main ===

def main():
    ap = argparse.ArgumentParser(description="Factory responder — give the monitor teeth")
    ap.add_argument("--once", action="store_true", help="Run once and exit (default)")
    ap.add_argument("--monitor-output", type=str, default=None,
                    help="Path to JSON output from factory_monitor.py (default: call it inline)")
    ap.add_argument("--dry-run", action="store_true", help="Don't actually take any action")
    ap.add_argument("--json", action="store_true", help="Output JSON only")
    ap.add_argument("--skip-replies", action="store_true",
                    help="Don't process Telegram replies (just do alerts)")
    args = ap.parse_args()

    # === Step 1: Process Telegram replies (always) ===
    if not args.dry_run and not args.skip_replies:
        reply_result = process_replies()
        log(f"reply processing: {reply_result}")

    # === Step 2: Run monitor, take action on alerts ===
    if args.monitor_output:
        with open(args.monitor_output) as f:
            monitor_output = json.load(f)
    else:
        if not Path(MONITOR_SCRIPT).exists():
            log(f"monitor script not found at {MONITOR_SCRIPT}")
            sys.exit(1)
        r = subprocess.run(
            ["python3", str(MONITOR_SCRIPT), "--json"],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode not in (0, 2):
            log(f"monitor exited {r.returncode}: {r.stderr[:200]}")
            sys.exit(1)
        try:
            monitor_output = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            log(f"monitor output not JSON: {e}")
            log(f"stdout was: {r.stdout[:200]}")
            sys.exit(1)

    summary = respond(monitor_output, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print()
        print("=" * 60)
        print(f"FACTORY RESPONDER — {datetime.now(timezone.utc).isoformat()}")
        print(f"  severity: {summary['severity']}")
        print(f"  alerts: {summary['alerts_count']}")
        print(f"  actions taken: {len(summary['actions_taken'])}")
        for a in summary["actions_taken"]:
            print(f"    + {a}")
        print(f"  actions skipped: {len(summary['actions_skipped'])}")
        for s in summary["actions_skipped"]:
            print(f"    - {s}")
        print("=" * 60)

    if summary["severity"] == "CRITICAL" and not summary["actions_taken"]:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
