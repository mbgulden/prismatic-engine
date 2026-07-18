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
import subprocess
import shutil
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LINEAR_API = os.environ.get("LINEAR_API_KEY")
DEFAULT_PRISMATIC_DB = Path.home() / ".prismatic" / "db"
QUEUE_DB = Path(
    os.environ.get(
        "PRISMATIC_LINEAR_WEBHOOK_QUEUE_DB",
        str(DEFAULT_PRISMATIC_DB / "linear_webhook_queue.db"),
    )
)
LAUNCH_DB = Path(
    os.environ.get(
        "PRISMATIC_LAUNCH_RECORDS_DB_PATH",
        str(DEFAULT_PRISMATIC_DB / "event_router.db"),
    )
)
BOT_TRIGGER_DIR = Path(
    os.environ.get(
        "PRISMATIC_BOT_DELEGATION_TRIGGER_DIR", "/tmp/bot-delegation/triggers"
    )
)
DEFAULT_STALE_SECONDS = int(
    os.environ.get("PRISMATIC_RESULT_WRITEBACK_STALE_SECONDS", "900")
)

AGY_OK = "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK"
AGY_BLOCKED = "AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED"
FRED_OK = "RAW_AGENT_OUTPUT_REPAIR_QUEUE_OK"
FRED_BLOCKED = "RAW_AGENT_OUTPUT_REPAIR_QUEUE_BLOCKED"
GEORGE_BLOCKED = "GEORGE_ASSIGNED_DISPATCH_BLOCKED"


def emit_visible_result_event(
    agent: str, identifier: str, status: str, *, marker: str = "", log: str = ""
) -> dict:
    """Best-effort Telegram-visible sleep/result breadcrumb for assigned-agent runs."""
    if os.environ.get("PRISMATIC_VISIBLE_AGENT_STREAM", "1") in {
        "0",
        "false",
        "False",
        "no",
    }:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    message = "\n".join(
        [
            f"🌙 Prismatic assigned-agent stream: {status}",
            "",
            f"agent={agent}",
            f"issue={identifier}",
            f"marker={marker}" if marker else "marker=unknown",
            f"log={log}" if log else "log=not-provided",
            "",
            "Agent run reached result/writeback reconciliation. Linear remains source of truth.",
        ]
    )
    try:
        from prismatic.agent_signal_stream import record_agent_signal

        severity = (
            "warning"
            if "BLOCKED" in status
            else "error"
            if "FAIL" in status
            else "success"
        )
        transcript = (
            Path(log).read_text(encoding="utf-8", errors="replace")[-4000:]
            if log and Path(log).exists()
            else ""
        )
        record_agent_signal(
            agent=agent,
            event_type=status,
            issue_id=identifier,
            status=status,
            message=message,
            source="result-writeback",
            severity=severity,
            log_path=log,
            transcript=transcript,
            metadata={"marker": marker},
        )
    except Exception:
        pass
    log_path = os.environ.get("PRISMATIC_VISIBLE_WAKE_LOG", "")
    if log_path:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(message + "\n---\n")
        except OSError:
            pass
    if os.environ.get("PRISMATIC_VISIBLE_WAKE_DRY_RUN", "0") == "1":
        return {"ok": True, "dry_run": True, "message": message}
    hermes = shutil.which(os.environ.get("PRISMATIC_HERMES_BIN", "hermes"))
    if not hermes:
        return {"ok": False, "reason": "hermes binary not found"}
    profile = os.environ.get("PRISMATIC_VISIBLE_WAKE_HERMES_PROFILE", "kai")
    target = os.environ.get("PRISMATIC_VISIBLE_WAKE_TARGET", "telegram")
    try:
        proc = subprocess.run(
            [
                hermes,
                "--profile",
                profile,
                "send",
                "--to",
                target,
                "--subject",
                f"[Prismatic] {agent} {status} {identifier}",
                message,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=float(os.environ.get("PRISMATIC_VISIBLE_WAKE_TIMEOUT", "15")),
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "target": target,
        "profile": profile,
    }


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
        return any(
            re.search(r"(?m)^MARKER=" + re.escape(marker) + r"\s*$", body)
            for body in issue_comments(identifier)
        )
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
    for part in cmd:
        if isinstance(part, str) and part.startswith("PRISMATIC_AGY_OUTPUT_LOG="):
            return Path(part.split("=", 1)[1])
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
        if re.match(
            r"^(skill_pack_state|shared_skill_packs|agent_skill_packs|packet_contract_version|packet_validation|COMMAND|RESULT|LOG|SCOPE|AD_HOC_OR_CANONICAL|NOT_CLAIMING|MARKER)=",
            line,
        ):
            capture = True
            lines.append(line[:1000])
    return "\n".join(lines[-12:]) if capture else None


def update_launch_status(run_id: str, status: str) -> None:
    if not LAUNCH_DB.exists():
        return
    con = sqlite3.connect(LAUNCH_DB)
    try:
        con.execute(
            "update launch_records set status=? where run_id=?", (status, run_id)
        )
        con.commit()
    finally:
        con.close()


def reconcile_agy() -> list[str]:
    out = []
    if not LAUNCH_DB.exists():
        return out
    con = sqlite3.connect(LAUNCH_DB)
    con.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in con.execute(
            "select * from launch_records where agent_name='agy' and identifier in ('GRO-3954') order by created_at desc limit 10"
        )
    ]
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
        text = (
            log_path.read_text(errors="replace")
            if log_path and log_path.exists()
            else ""
        )
        packet = compact_packet_from_text(text)
        if packet:
            marker = re.search(r"(?m)^MARKER=([A-Z0-9_]+)\s*$", packet).group(1)  # type: ignore[union-attr]
            result_match = re.search(r"(?m)^RESULT=(PASS|BLOCKED|FAIL)\s*$", packet)
            result_status = result_match.group(1) if result_match else "PASS"
            launch_status = (
                "blocked"
                if result_status == "BLOCKED"
                else ("failed" if result_status == "FAIL" else "completed")
            )
            event_type = (
                "WORK_BLOCKED"
                if result_status == "BLOCKED"
                else (
                    "WORK_FAILED" if result_status == "FAIL" else "WORK_RESULT_PACKET"
                )
            )
            if not has_marker(ident, marker):
                add_comment(
                    ident,
                    f"AGY completed-work packet from dispatcher log `{log_path}`:\n\n```text\n{packet}\n```",
                )
            update_launch_status(run_id, launch_status)
            emit_visible_result_event(
                "agy", ident, event_type, marker=marker, log=str(log_path or "")
            )
            out.append(f"AGY_PACKET_WRITTEN {ident} {marker} result={result_status}")
            continue
        if "--headless" in cmd or "--issue" in cmd or "--task" in cmd:
            reason = "AGY launch used obsolete unsupported CLI flags (`--headless/--issue/--task`); process exited without result log. Dispatcher has been patched to use `agy --print ... --log-file ...`."
        elif log_path and log_path.exists():
            tail = text[-800:].replace("`", "'")
            reason = f"AGY process exited without compact packet. Log: `{log_path}` Tail: {tail}"
        else:
            reason = "AGY process exited and no log/output path was available in launch record."
        if not has_marker(ident, AGY_BLOCKED):
            body = "\n".join(
                [
                    "RESULT=BLOCKED",
                    f"LOG={log_path or 'missing'}",
                    "SCOPE=AGY execution/result writeback for GRO-3954",
                    "AD_HOC_OR_CANONICAL=ad-hoc targeted",
                    "NOT_CLAIMING=AGY task completed,Prompt4 green,Prompt5 unlocked,production deployed,canonical suite green",
                    f"MARKER={AGY_BLOCKED}",
                    "",
                    reason,
                ]
            )
            add_comment(ident, body)
        update_launch_status(run_id, "blocked")
        emit_visible_result_event(
            "agy", ident, "WORK_BLOCKED", marker=AGY_BLOCKED, log=str(log_path or "")
        )
        out.append(f"AGY_BLOCKED_WRITTEN {ident} {run_id}")
    return out


def latest_agent_launch_rows(
    agent_name: str, identifiers: tuple[str, ...] = ()
) -> list[dict]:
    if not LAUNCH_DB.exists():
        return []
    con = sqlite3.connect(LAUNCH_DB)
    con.row_factory = sqlite3.Row
    try:
        if identifiers:
            placeholders = ",".join("?" for _ in identifiers)
            query = f"select * from launch_records where agent_name=? and identifier in ({placeholders}) order by created_at desc limit 10"
            params = (agent_name, *identifiers)
        else:
            query = "select * from launch_records where agent_name=? order by created_at desc limit 10"
            params = (agent_name,)
        rows = [dict(r) for r in con.execute(query, params)]
    finally:
        con.close()
    return rows


def reconcile_visible_hermes_launches(
    agent: str, identifiers: tuple[str, ...] = ()
) -> list[str]:
    out: list[str] = []
    default_marker = (
        FRED_OK if agent == "fred" else f"{agent.upper()}_ASSIGNED_DISPATCH_OK"
    )
    blocked_marker = FRED_BLOCKED if agent == "fred" else GEORGE_BLOCKED
    label = agent.capitalize()
    for row in latest_agent_launch_rows(agent, identifiers):
        ident = str(row.get("identifier") or row.get("issue_id") or "")
        run_id = str(row.get("run_id") or "")
        if (
            not ident
            or not run_id
            or row.get("status") in {"completed", "failed", "blocked"}
        ):
            continue
        if pid_live(row.get("pid")):
            out.append(f"{agent.upper()}_VISIBLE_STILL_RUNNING {ident} {run_id}")
            continue
        cmd = json.loads(row.get("command_json") or "[]")
        log_path = command_log_path(cmd)
        text = (
            log_path.read_text(errors="replace")
            if log_path and log_path.exists()
            else ""
        )
        packet = compact_packet_from_text(text)
        if packet:
            marker_match = re.search(r"(?m)^MARKER=([A-Z0-9_]+)\s*$", packet)
            marker = marker_match.group(1) if marker_match else default_marker
            result_match = re.search(r"(?m)^RESULT=(PASS|BLOCKED|FAIL)\s*$", packet)
            result_status = result_match.group(1) if result_match else "PASS"
            launch_status = (
                "blocked"
                if result_status == "BLOCKED"
                else ("failed" if result_status == "FAIL" else "completed")
            )
            event_type = (
                "WORK_BLOCKED"
                if result_status == "BLOCKED"
                else (
                    "WORK_FAILED" if result_status == "FAIL" else "WORK_RESULT_PACKET"
                )
            )
            if not has_marker(ident, marker):
                add_comment(
                    ident,
                    f"{label} visible-execution packet from Hermes log `{log_path}`:\n\n```text\n{packet}\n```",
                )
            update_launch_status(run_id, launch_status)
            emit_visible_result_event(
                agent, ident, event_type, marker=marker, log=str(log_path or "")
            )
            out.append(
                f"{agent.upper()}_VISIBLE_PACKET_WRITTEN {ident} {marker} result={result_status}"
            )
            continue
        if log_path and log_path.exists():
            tail = text[-800:].replace("`", "'")
            reason = f"{label} visible Hermes execution exited without compact packet. Log: `{log_path}` Tail: {tail}"
        else:
            reason = f"{label} visible Hermes execution exited and no log/output path was available."
        if not has_marker(ident, blocked_marker):
            body = "\n".join(
                [
                    "RESULT=BLOCKED",
                    f"LOG={log_path or 'missing'}",
                    f"SCOPE={label} visible execution/result writeback for {ident}",
                    "AD_HOC_OR_CANONICAL=ad-hoc targeted",
                    f"NOT_CLAIMING={label} task completed,Prompt4 green,Prompt5 unlocked,production deployed,canonical suite green",
                    f"MARKER={blocked_marker}",
                    "",
                    reason,
                ]
            )
            add_comment(ident, body)
        update_launch_status(run_id, "blocked")
        emit_visible_result_event(
            agent, ident, "WORK_BLOCKED", marker=blocked_marker, log=str(log_path or "")
        )
        out.append(f"{agent.upper()}_VISIBLE_BLOCKED_WRITTEN {ident} {run_id}")
    return out


def reconcile_fred_visible_launches() -> list[str]:
    return reconcile_visible_hermes_launches("fred", ("GRO-3952",))


def reconcile_george_visible_launches() -> list[str]:
    return reconcile_visible_hermes_launches("george")


def latest_queue_rows() -> list[dict]:
    if not QUEUE_DB.exists():
        return []
    con = sqlite3.connect(QUEUE_DB)
    con.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in con.execute(
            "select * from linear_webhook_queue where identifier='GRO-3952' and target_agent='fred' order by id desc limit 3"
        )
    ]
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
        triggers = sorted(
            BOT_TRIGGER_DIR.glob("*.trigger"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
        )
        matching = [
            p
            for p in triggers
            if "fred" in p.name
            and (not run_id or run_id in p.read_text(errors="replace"))
        ]
        # Fall back to any recent Fred trigger if old bridge format did not include run_id.
        if not matching:
            matching = [p for p in triggers if "fred" in p.name]
        newest = matching[-1] if matching else None
        age = time.time() - newest.stat().st_mtime if newest else None
        if age is not None and age < DEFAULT_STALE_SECONDS:
            out.append(f"FRED_TRIGGER_WAITING {ident} age={int(age)}s")
            continue
        reason = "Fred nudge reached bot-delegation trigger files, but no executor/result packet consumed it before the stale threshold. This proves the break is after bridge/trigger creation."
        body = "\n".join(
            [
                "RESULT=BLOCKED",
                f"LOG={newest or 'missing trigger'}",
                "SCOPE=Fred execution/result writeback for GRO-3952",
                "AD_HOC_OR_CANONICAL=ad-hoc targeted",
                "NOT_CLAIMING=Fred task completed,Prompt4 green,Prompt5 unlocked,production deployed,canonical suite green",
                f"MARKER={FRED_BLOCKED}",
                "",
                reason,
            ]
        )
        add_comment(ident, body)
        emit_visible_result_event(
            "fred",
            ident,
            "WORK_BLOCKED",
            marker=FRED_BLOCKED,
            log=str(newest or "missing trigger"),
        )
        out.append(f"FRED_BLOCKED_WRITTEN {ident}")
    return out


def main() -> int:
    print(
        "ASSIGNED_AGENT_RESULT_WRITEBACK_SCAN " + datetime.now(timezone.utc).isoformat()
    )
    results = (
        reconcile_agy()
        + reconcile_fred_visible_launches()
        + reconcile_george_visible_launches()
        + reconcile_fred()
    )
    for item in results:
        print(item)
    if not results:
        print("NO_ACTION")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
