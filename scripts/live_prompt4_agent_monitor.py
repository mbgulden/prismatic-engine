#!/usr/bin/env python3
"""Live Prompt4 monitor using latest-valid packet semantics."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

from prismatic.prompt4_packet_gate import evaluate_prompt4_packet_gate

LINEAR_API = os.environ.get("LINEAR_API_KEY")
ISSUES = ["GRO-3952", "GRO-3954"]


def linear_status():
    if not LINEAR_API:
        return {}, "NO_LINEAR_API_KEY"
    query = """query($a:String!,$b:String!){ a: issue(id:$a){ identifier state { name type } labels { nodes { name } } comments(first:100){ nodes { body createdAt user { name } } } } b: issue(id:$b){ identifier state { name type } labels { nodes { name } } comments(first:100){ nodes { body createdAt user { name } } } } }"""
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps(
            {"query": query, "variables": {"a": ISSUES[0], "b": ISSUES[1]}}
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": LINEAR_API},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.load(response)
    if data.get("errors"):
        return {}, "LINEAR_ERRORS=" + json.dumps(data["errors"])
    comments_by_issue = {}
    issues = {}
    for key in ("a", "b"):
        issue = data["data"][key]
        identifier = issue["identifier"]
        comments = issue["comments"]["nodes"]
        comments_by_issue[identifier] = comments
        issues[identifier] = {
            "state": issue["state"],
            "labels": sorted(node["name"] for node in issue["labels"]["nodes"]),
            "comment_count": len(comments),
        }
    gate = evaluate_prompt4_packet_gate(comments_by_issue)
    gate["issues"] = issues
    return gate, None


def queue_status():
    db = Path("/home/ubuntu/.prismatic/db/linear_webhook_queue.db")
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in con.execute(
                "select identifier,dispatch_status,target_agent,resolver_status,preflight_status,claim_owner,run_id,last_error,result_status,blocker_summary,writeback_status,writeback_mode,updated_at from linear_webhook_queue where identifier in ('GRO-3952','GRO-3954') order by id desc"
            )
        ]
    finally:
        con.close()


def launch_status():
    db = Path("/home/ubuntu/.prismatic/db/event_router.db")
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in con.execute(
                "select run_id,identifier,agent_name,pid,status,created_at from launch_records where identifier in ('GRO-3952','GRO-3954') or issue_id in ('GRO-3952','GRO-3954') order by created_at desc limit 10"
            )
        ]
    finally:
        con.close()
    for row in rows:
        pid = row.get("pid")
        live = False
        if pid:
            try:
                os.kill(int(pid), 0)
                live = True
            except ProcessLookupError:
                live = False
            except PermissionError:
                live = True
        row["pid_live"] = live
    return rows


def main():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    gate, error = linear_status()
    queue_rows = queue_status()
    launches = launch_status()
    if error and not queue_rows:
        print(f"PROMPT4_LIVE_MONITOR_ERROR {now} {error}")
        return
    if gate.get("complete"):
        status = "COMPLETE"
    elif gate.get("active_blockers"):
        status = "BLOCKED_PACKET_PRESENT"
    else:
        status = "WAITING_REQUIRED_PACKET"
    packet_summary = {
        "required_agents": gate.get("required_agents", []),
        "found": gate.get("found", {}),
        "blocked": gate.get("blocked", {}),
        "superseded_blocked": gate.get("superseded_blocked", {}),
        "latest": gate.get("latest", {}),
    }
    print(f"PROMPT4_LIVE_MONITOR {now} status={status}")
    print("packets=" + json.dumps(packet_summary, sort_keys=True))
    print("queue=" + json.dumps(queue_rows, sort_keys=True))
    print("launches=" + json.dumps(launches, sort_keys=True))
    print(
        "george_dispatch_supported=false reason=George is intentionally manual/verifier and is not a required Prompt4 dispatched-agent packet until a separate launcher/resolver task is implemented"
    )
    if gate.get("complete"):
        print(
            "next=Prompt4 required-agent packet gate passed; Prompt5 is not automatically unlocked by this monitor"
        )
    else:
        missing = [str(agent) for agent in gate.get("missing", [])]
        print(
            "next=required packet work remains for "
            + ",".join(missing)
            + "; do not unlock Prompt5"
        )


if __name__ == "__main__":
    main()
