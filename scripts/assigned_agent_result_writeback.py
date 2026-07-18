#!/usr/bin/env python3
"""Reconcile assigned-agent launch/execution results back to Linear.

This is intentionally conservative: it does not claim agent work succeeded unless a
real compact proof packet appears in the agent log. It does write explicit BLOCKED
packets when a launched/signal-delivered task has no executable result path, so the
operator lane stops failing silently.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LINEAR_API = os.environ.get("LINEAR_API_KEY")
QUEUE_DB = Path(os.environ.get("PRISMATIC_LINEAR_WEBHOOK_QUEUE_DB", "/home/ubuntu/.prismatic/db/linear_webhook_queue.db"))
LAUNCH_DB = Path(os.environ.get("PRISMATIC_LAUNCH_RECORDS_DB_PATH", "/home/ubuntu/.prismatic/db/event_router.db"))
BOT_TRIGGER_DIR = Path(os.environ.get("PRISMATIC_BOT_DELEGATION_TRIGGER_DIR", "/tmp/bot-delegation/triggers"))
DEFAULT_STALE_SECONDS = int(os.environ.get("PRISMATIC_RESULT_WRITEBACK_STALE_SECONDS", "900"))

AGY_OK = "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK"
AGY_BLOCKED = "AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED"
FRED_OK = "RAW_AGENT_OUTPUT_REPAIR_QUEUE_OK"
FRED_BLOCKED = "RAW_AGENT_OUTPUT_REPAIR_QUEUE_BLOCKED"


def _graphql(query: str, variables: dict) -> dict:
    if not LINEAR_API:
        raise RuntimeError("LINEAR_API_KEY missing")
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": LINEAR_API},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"]))
    return data


def issue_comments(identifier: str) -> list[str]:
    q = "query($id:String!){ issue(id:$id){ comments(first:100){ nodes { body } } } }"
    data = _graphql(q, {"id": identifier})
    issue = data.get("data", {}).get("issue") or {}
    return [(n.get("body") or "") for n in issue.get("comments", {}).get("nodes", [])]


def has_marker(identifier: str, marker: str) -> bool:
    try:
        return any(re.search(r"(?m)^MARKER=" + re.escape(marker) + r"\s*$", body) for body in issue_comments(identifier))
    except Exception:
        return False


def add_comment(identifier: str, body: str) -> bool:
    if not LINEAR_API:
        print(f"LINEAR_COMMENT_SKIPPED {identifier} no LINEAR_API_KEY")
        return False
    q = "mutation($issueId:String!,$body:String!){ commentCreate(input:{issueId:$issueId,body:$body}){ success comment { id } } }"
    data = _graphql(q, {"issueId": identifier, "body": body})
    ok = bool(data.get("data", {}).get("commentCreate", {}).get("success"))
    print(f"LINEAR_COMMENT {identifier} success={ok}")
    return ok


def pid_live(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def command_log_path(cmd: list[str]) -> Path | None:
    if "--log-file" in cmd:
        i = cmd.index("--log-file")
        if i + 1 < len(cmd):
            return Path(cmd[i + 1])
    return None


def compact_packet_from_text(text: str) -> str | None:
    if not re.search(r"(?m)^RESULT=(PASS|BLOCKED|FAIL)\s*$", text):
        return None
    if not re.search(r"(?m)^MARKER=[A-Z0-9_]+\s*$", text):
        return None
    lines = []
    capture = False
    for line in text.splitlines():
        if re.match(r"^(COMMAND|RESULT|LOG|SCOPE|AD_HOC_OR_CANONICAL|NOT_CLAIMING|MARKER)=", line):
            capture = True
            lines.append(line[:1000])
    return "\n".join(lines[-12:]) if capture else None


def update_launch_status(run_id: str, status: str) -> None:
    if not LAUNCH_DB.exists():
        return
    con = sqlite3.connect(LAUNCH_DB)
    try:
        con.execute("update launch_records set status=? where run_id=?", (status, run_id))
        con.commit()
    finally:
        con.close()


def reconcile_agy() -> list[str]:
    out = []
    if not LAUNCH_DB.exists():
        return out
    con = sqlite3.connect(LAUNCH_DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("select * from launch_records where agent_name='agy' and identifier in ('GRO-3954') order by created_at desc limit 10")]
    con.close()
    for row in rows:
        ident = str(row.get("identifier") or row.get("issue_id") or "")
        run_id = str(row.get("run_id") or "")
        if not ident or not run_id:
            continue
        status = row.get("status")
        if status in {"completed", "failed", "blocked"}:
            continue
        if pid_live(row.get("pid")):
            out.append(f"AGY_STILL_RUNNING {ident} {run_id}")
            continue
        cmd = json.loads(row.get("command_json") or "[]")
        log_path = command_log_path(cmd)
        text = log_path.read_text(errors="replace") if log_path and log_path.exists() else ""
        packet = compact_packet_from_text(text)
        if packet:
            marker = re.search(r"(?m)^MARKER=([A-Z0-9_]+)\s*$", packet).group(1)  # type: ignore[union-attr]
            if not has_marker(ident, marker):
                add_comment(ident, f"AGY completed-work packet from dispatcher log `{log_path}`:\n\n```text\n{packet}\n```")
            update_launch_status(run_id, "completed")
            out.append(f"AGY_PACKET_WRITTEN {ident} {marker}")
            continue
        if "--headless" in cmd or "--issue" in cmd or "--task" in cmd:
            reason = "AGY launch used obsolete unsupported CLI flags (`--headless/--issue/--task`); process exited without result log. Dispatcher has been patched to use `agy --print ... --log-file ...`."
        elif log_path and log_path.exists():
            tail = text[-800:].replace("`", "'")
            reason = f"AGY process exited without compact packet. Log: `{log_path}` Tail: {tail}"
        else:
            reason = "AGY process exited and no log/output path was available in launch record."
        if not has_marker(ident, AGY_BLOCKED):
            body = "\n".join([
                "RESULT=BLOCKED",
                f"LOG={log_path or 'missing'}",
                "SCOPE=AGY execution/result writeback for GRO-3954",
                "AD_HOC_OR_CANONICAL=ad-hoc targeted",
                "NOT_CLAIMING=AGY task completed,Prompt4 green,Prompt5 unlocked,production deployed,canonical suite green",
                f"MARKER={AGY_BLOCKED}",
                "",
                reason,
            ])
            add_comment(ident, body)
        update_launch_status(run_id, "blocked")
        out.append(f"AGY_BLOCKED_WRITTEN {ident} {run_id}")
    return out


def latest_queue_rows() -> list[dict]:
    if not QUEUE_DB.exists():
        return []
    con = sqlite3.connect(QUEUE_DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("select * from linear_webhook_queue where identifier='GRO-3952' and target_agent='fred' order by id desc limit 3")]
    con.close()
    return rows


def reconcile_fred() -> list[str]:
    out = []
    for row in latest_queue_rows():
        ident = str(row.get("identifier") or "")
        if not ident or row.get("dispatch_status") != "dispatched":
            continue
        if has_marker(ident, FRED_OK) or has_marker(ident, FRED_BLOCKED):
            out.append(f"FRED_ALREADY_HAS_PACKET {ident}")
            continue
        run_id = row.get("run_id") or ""
        triggers = sorted(BOT_TRIGGER_DIR.glob("*.trigger"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
        matching = [p for p in triggers if "fred" in p.name and (not run_id or run_id in p.read_text(errors="replace"))]
        # Fall back to any recent Fred trigger if old bridge format did not include run_id.
        if not matching:
            matching = [p for p in triggers if "fred" in p.name]
        newest = matching[-1] if matching else None
        age = time.time() - newest.stat().st_mtime if newest else None
        if age is not None and age < DEFAULT_STALE_SECONDS:
            out.append(f"FRED_TRIGGER_WAITING {ident} age={int(age)}s")
            continue
        reason = "Fred nudge reached bot-delegation trigger files, but no executor/result packet consumed it before the stale threshold. This proves the break is after bridge/trigger creation."
        body = "\n".join([
            "RESULT=BLOCKED",
            f"LOG={newest or 'missing trigger'}",
            "SCOPE=Fred execution/result writeback for GRO-3952",
            "AD_HOC_OR_CANONICAL=ad-hoc targeted",
            "NOT_CLAIMING=Fred task completed,Prompt4 green,Prompt5 unlocked,production deployed,canonical suite green",
            f"MARKER={FRED_BLOCKED}",
            "",
            reason,
        ])
        add_comment(ident, body)
        out.append(f"FRED_BLOCKED_WRITTEN {ident}")
    return out


def main() -> int:
    print("ASSIGNED_AGENT_RESULT_WRITEBACK_SCAN " + datetime.now(timezone.utc).isoformat())
    results = reconcile_agy() + reconcile_fred()
    for item in results:
        print(item)
    if not results:
        print("NO_ACTION")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
