#!/usr/bin/env python3
"""Run and prove the AGY single-task canary for GRO-3837 only.

This verifier intentionally uses the installed AGY CLI shape:

    agy --print ... --model ... --log-file ...

It does not use the legacy unsupported ``agy --headless --issue`` shape.
The proof accepts byte/length equivalents when AGY does not expose token
counters: prompt_length, task_payload_bytes, result_text_bytes, artifact path,
Linear comment/state mutation, and an explicit one-subprocess launch record.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LINEAR_URL = "https://api.linear.app/graphql"
ALLOWED_IDENTIFIER = "GRO-3837"
DEFAULT_MODEL = "Gemini 3.5 Flash (High)"


def graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    token = os.environ.get("LINEAR_API_KEY")
    if not token:
        raise RuntimeError("LINEAR_API_KEY is required for GRO-3837 proof/writeback")
    req = urllib.request.Request(
        LINEAR_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]


def fetch_issue(identifier: str) -> dict[str, Any]:
    query = """
    query Issue($id: String!) {
      issue(id: $id) {
        id
        identifier
        title
        description
        url
        state { id name type }
        labels { nodes { id name } }
        team { states { nodes { id name type } } }
      }
    }
    """
    data = graphql(query, {"id": identifier})
    issue = data.get("issue")
    if not issue:
        raise RuntimeError(f"Linear issue not found: {identifier}")
    return issue


def comment_issue(issue_id: str, body: str) -> str:
    mutation = """
    mutation Comment($issueId: String!, $body: String!) {
      commentCreate(input: {issueId: $issueId, body: $body}) {
        success
        comment { id url }
      }
    }
    """
    data = graphql(mutation, {"issueId": issue_id, "body": body})["commentCreate"]
    if not data.get("success"):
        raise RuntimeError("Linear commentCreate returned success=false")
    return data["comment"]["id"]


def update_issue_state(issue_id: str, state_id: str) -> bool:
    mutation = """
    mutation UpdateState($issueId: String!, $stateId: String!) {
      issueUpdate(id: $issueId, input: {stateId: $stateId}) { success }
    }
    """
    data = graphql(mutation, {"issueId": issue_id, "stateId": state_id})["issueUpdate"]
    if not data.get("success"):
        raise RuntimeError("Linear issueUpdate returned success=false")
    return True


def label_names(issue: dict[str, Any]) -> list[str]:
    return [node["name"] for node in issue.get("labels", {}).get("nodes", [])]


def find_state(issue: dict[str, Any], name: str) -> dict[str, str]:
    states = issue.get("team", {}).get("states", {}).get("nodes", [])
    for state in states:
        if state.get("name") == name:
            return state
    raise RuntimeError(f"Linear state not found: {name}")


def build_prompt(issue: dict[str, Any], task_payload: dict[str, Any]) -> str:
    description = issue.get("description") or ""
    labels = ", ".join(task_payload["labels"])
    return f"""You are AGY running exactly one approved Prismatic task.

Hard constraints:
- Work ONLY on {ALLOWED_IDENTIFIER}.
- Do NOT launch, inspect, claim, or modify any other Linear issue.
- Do NOT post to Linear yourself; Fred's verifier will do writeback.
- Produce a concise RESULT.md-style answer.

Task:
Identifier: {issue['identifier']}
Title: {issue['title']}
State: {issue['state']['name']}
Labels: {labels}
URL: {issue.get('url','')}

Description:
{description}

Required output:
1. Start with: DONE: {ALLOWED_IDENTIFIER}
2. Summarize the rubric inventory/scoring-rule result.
3. Include a short verification note that you only handled {ALLOWED_IDENTIFIER}.
"""


def process_snapshot() -> list[str]:
    result = subprocess.run(
        ["pgrep", "-af", r"agy|GRO-3837|issue-batches|agy_sandbox"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def issue_batch_snapshot() -> list[str]:
    root = Path("/tmp/issue-batches")
    if not root.exists():
        return []
    return sorted(str(path) for path in root.glob("*"))


def run(args: argparse.Namespace) -> dict[str, Any]:
    identifier = args.issue.strip().upper()
    if identifier != ALLOWED_IDENTIFIER:
        raise RuntimeError(f"Refusing to run {identifier}; only {ALLOWED_IDENTIFIER} is allowed")

    issue = fetch_issue(identifier)
    if issue["identifier"] != ALLOWED_IDENTIFIER:
        raise RuntimeError(f"Linear resolved unexpected issue: {issue['identifier']}")
    labels = label_names(issue)
    required_labels = {"agent:agy", "dispatch:ready"}
    missing = sorted(required_labels - set(labels))
    if missing:
        raise RuntimeError(f"{identifier} missing required labels: {missing}")

    in_review = find_state(issue, args.state)
    task_payload = {
        "identifier": issue["identifier"],
        "id": issue["id"],
        "title": issue["title"],
        "description": issue.get("description") or "",
        "state": issue["state"],
        "labels": labels,
        "url": issue.get("url", ""),
    }
    task_payload_json = json.dumps(task_payload, indent=2, sort_keys=True)
    task_payload_bytes = len(task_payload_json.encode())
    prompt = build_prompt(issue, task_payload)
    prompt_length = len(prompt)
    if prompt_length <= 0 or task_payload_bytes <= 0:
        raise RuntimeError("prompt/task payload proof fields must be > 0")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = Path(args.artifact_dir).expanduser().resolve() / identifier / ts
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload_path = artifact_dir / "task_payload.json"
    prompt_path = artifact_dir / "prompt.txt"
    result_path = artifact_dir / "RESULT.md"
    log_path = artifact_dir / "agy.log"
    proof_path = artifact_dir / "proof.json"
    payload_path.write_text(task_payload_json)
    prompt_path.write_text(prompt)

    before_processes = process_snapshot()
    before_batches = issue_batch_snapshot()
    command = [
        "agy",
        "--print",
        prompt,
        "--print-timeout",
        args.print_timeout,
        "--model",
        args.model,
        "--log-file",
        str(log_path),
    ]
    started_at = time.time()
    completed = subprocess.run(command, capture_output=True, text=True, timeout=args.subprocess_timeout)
    duration_seconds = round(time.time() - started_at, 3)
    result_text = (completed.stdout or "").strip()
    if completed.returncode != 0:
        (artifact_dir / "stderr.txt").write_text(completed.stderr or "")
        raise RuntimeError(f"agy exited {completed.returncode}; stderr saved to {artifact_dir / 'stderr.txt'}")
    if not result_text:
        raise RuntimeError("agy returned empty result text")
    result_path.write_text(result_text + "\n")
    result_text_bytes = len(result_text.encode())
    if result_text_bytes <= 0:
        raise RuntimeError("result_text_bytes must be > 0")
    if f"DONE: {identifier}" not in result_text:
        raise RuntimeError(f"AGY result missing DONE marker for {identifier}")

    after_processes = process_snapshot()
    after_batches = issue_batch_snapshot()
    new_batches = sorted(set(after_batches) - set(before_batches))
    if new_batches:
        raise RuntimeError(f"Unexpected issue batch files created: {new_batches}")

    comment_body = f"""✅ **AGY_SINGLE_TASK_PROOF_OK**

Exactly one AGY canary task was run: `{identifier}`.

Proof fields:
- model: `{args.model}`
- prompt_length: `{prompt_length}`
- task_payload_bytes: `{task_payload_bytes}`
- result_text_bytes: `{result_text_bytes}`
- result artifact: `{result_path}`
- proof artifact: `{proof_path}`
- no unrelated task batch files created: `{not new_batches}`
- command shape: `agy --print ... --model ... --log-file ...`

Boundary: ad-hoc single-task AGY proof, not full suite green.
"""
    comment_id = comment_issue(issue["id"], comment_body)
    state_update_ok = update_issue_state(issue["id"], in_review["id"])
    post_issue = fetch_issue(identifier)

    proof = {
        "AD_HOC_VERIFICATION": "PASS",
        "marker": "AGY_SINGLE_TASK_PROOF_OK",
        "scope": "single AGY task proof for GRO-3837 only using installed agy --print CLI; not full suite green",
        "identifier": identifier,
        "issue_id": issue["id"],
        "model": args.model,
        "command_shape": ["agy", "--print", "<prompt>", "--print-timeout", args.print_timeout, "--model", args.model, "--log-file", str(log_path)],
        "prompt_length": prompt_length,
        "task_payload_bytes": task_payload_bytes,
        "result_text_bytes": result_text_bytes,
        "artifact_dir": str(artifact_dir),
        "result_artifact": str(result_path),
        "result_artifact_exists": result_path.exists(),
        "proof_artifact": str(proof_path),
        "log_artifact": str(log_path),
        "linear_comment_id": comment_id,
        "linear_state_update_ok": state_update_ok,
        "linear_state_after": post_issue["state"],
        "before_process_count": len(before_processes),
        "after_process_count": len(after_processes),
        "new_issue_batch_files": new_batches,
        "no_other_tasks_launched": len(new_batches) == 0,
        "agy_returncode": completed.returncode,
        "agy_duration_seconds": duration_seconds,
    }
    proof_path.write_text(json.dumps(proof, indent=2, sort_keys=True))
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify AGY single task proof for GRO-3837 only")
    parser.add_argument("--issue", default=ALLOWED_IDENTIFIER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--state", default="In Review", help="Linear state to set after artifact/comment proof")
    parser.add_argument("--artifact-dir", default="/tmp/agy-single-task-proofs")
    parser.add_argument("--print-timeout", default="20m0s")
    parser.add_argument("--subprocess-timeout", type=int, default=1500)
    args = parser.parse_args()
    proof = run(args)
    print(json.dumps(proof, indent=2, sort_keys=True))
    print("AGY_SINGLE_TASK_PROOF_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
