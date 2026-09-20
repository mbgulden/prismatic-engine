#!/usr/bin/env python3
"""Reconcile assigned-agent launch/execution results back to Linear.

This is intentionally conservative: it does not claim agent work succeeded unless one
complete canonical JSON packet appears in the agent log and binds to the exact run and
git provenance. It does write explicit BLOCKED packets when a launched/signal-delivered
task has no executable result path, so the operator lane stops failing silently.
"""

from __future__ import annotations

import hashlib
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

from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER,
    AgyCompletedWorkStore,
)

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
RAW_CAPTURE_EVENT = "assigned_agent_result_writeback_log"


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


def capture_raw_result_output(
    *,
    agent: str,
    identifier: str,
    run_id: str,
    text: str,
    raw_bytes: bytes,
    artifact_path: Path | None,
    source_event: str = RAW_CAPTURE_EVENT,
) -> dict:
    """Best-effort pre-normalization raw output capture for result writeback.

    Capture is deliberately fail-safe: result classification/comment writeback keeps
    using the caller's original text even if this queue write fails.
    """
    if not text.strip() or not raw_bytes:
        return {"ok": False, "skipped": True, "reason": "empty_raw_output"}
    source_event_id = ":".join(
        part
        for part in (
            source_event,
            agent,
            identifier,
            run_id or str(artifact_path or ""),
        )
        if part
    )
    try:
        from prismatic.agent_raw_output_queue import persist_raw_output

        row = persist_raw_output(
            raw_text=text,
            raw_bytes=raw_bytes,
            agent=agent,
            task_id=identifier,
            source_event_id=source_event_id,
            raw_text_or_artifact_path=str(artifact_path or ""),
            expected_agent=agent,
        )
        return {
            "ok": True,
            "raw_output_id": row.raw_output_id,
            "agent": row.agent,
            "task_id": row.task_id,
            "raw_bytes_sha256": row.raw_bytes_sha256,
            "raw_bytes_length": row.raw_bytes_length,
            "normalization_status": row.normalization_status,
            "source_event_id": row.source_event_id,
        }
    except Exception as exc:
        return {"ok": False, "reason": f"raw output capture failed: {exc}"}


def compact_packet_from_text(text: str) -> str | None:
    if not re.search(r"(?m)^RESULT=(PASS|BLOCKED|FAIL)\s*$", text):
        return None
    if not re.search(r"(?m)^MARKER=[A-Z0-9_]+\s*$", text):
        return None
    lines = []
    capture = False
    for line in text.splitlines():
        if re.match(
            r"^(skill_pack_state|shared_skill_packs|agent_skill_packs|packet_contract_version|packet_validation|COMMAND|RESULT|LOG|SCOPE|AD_HOC_OR_CANONICAL|NOT_CLAIMING|MARKER|AGENT|ISSUE|ISSUE_IDENTIFIER|SOURCE_BRANCH|BRANCH|SOURCE_PATH|BASE_BRANCH|CHANGED_FILES|RESULT_SUMMARY|VERIFICATION_LANE|MERGE_LANE)=",
            line,
        ):
            capture = True
            lines.append(line[:1000])
    return "\n".join(lines[-12:]) if capture else None


_CANONICAL_JSON_BLOCK = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _expected_source_event_id(agent: str, identifier: str, run_id: str) -> str:
    return ":".join(
        part for part in (RAW_CAPTURE_EVENT, agent, identifier, run_id) if part
    )


def durable_capture_error(
    capture: dict,
    *,
    agent: str,
    identifier: str,
    run_id: str,
    raw_bytes: bytes,
) -> str | None:
    """Validate the durable raw row returned by the canonical capture primitive."""

    if capture.get("ok") is not True:
        return str(capture.get("reason") or "raw_capture_unavailable")
    expected = {
        "agent": agent,
        "task_id": identifier,
        "source_event_id": _expected_source_event_id(agent, identifier, run_id),
    }
    raw_output_id = capture.get("raw_output_id")
    if not isinstance(raw_output_id, str) or not raw_output_id.strip():
        return "raw_output_id_unavailable"
    if capture.get("raw_bytes_sha256") != hashlib.sha256(raw_bytes).hexdigest():
        return "raw_capture_bytes_sha256_mismatch"
    if capture.get("raw_bytes_length") != len(raw_bytes):
        return "raw_capture_bytes_length_mismatch"
    for field, value in expected.items():
        if capture.get(field) != value:
            return f"raw_capture_{field}_mismatch"
    return None


def canonical_packet_from_text(
    text: str,
    *,
    expected_agent: str,
    expected_identifier: str,
    expected_run_id: str,
) -> dict:
    """Extract and validate one complete fenced canonical JSON packet."""

    blocks = _CANONICAL_JSON_BLOCK.findall(text)
    if len(blocks) != 1:
        raise ValueError("canonical_packet_missing_or_ambiguous")
    try:
        packet = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise ValueError("canonical_packet_json_invalid") from exc
    if not isinstance(packet, dict):
        raise ValueError("canonical_packet_not_object")

    scalar_fields = (
        "agent",
        "issue_identifier",
        "run_id",
        "source_branch",
        "source_path",
        "base_branch",
        "source_commit_sha",
        "base_commit_sha",
        "result_summary",
        "verification_lane",
        "result",
        "classification",
        "marker",
    )
    if any(
        not isinstance(packet.get(field), str) or not packet[field].strip()
        for field in scalar_fields
    ):
        raise ValueError("canonical_packet_provenance_incomplete")
    if packet["agent"].lower() != expected_agent.lower():
        raise ValueError("canonical_packet_agent_mismatch")
    if packet["issue_identifier"].upper() != expected_identifier.upper():
        raise ValueError("canonical_packet_issue_mismatch")
    if packet["run_id"] != expected_run_id:
        raise ValueError("canonical_packet_run_mismatch")
    if (
        not _COMMIT_SHA.fullmatch(packet["source_commit_sha"].lower())
        or not _COMMIT_SHA.fullmatch(packet["base_commit_sha"].lower())
        or set(packet["source_commit_sha"].lower()) == {"0"}
        or set(packet["base_commit_sha"].lower()) == {"0"}
    ):
        raise ValueError("canonical_packet_commit_provenance_invalid")
    source_path = Path(packet["source_path"]).expanduser()
    if (
        not source_path.is_absolute()
        or source_path.is_symlink()
        or not source_path.is_dir()
        or str(source_path.resolve()) != packet["source_path"]
    ):
        raise ValueError("canonical_packet_source_path_invalid")

    def git_value(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(source_path), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("canonical_packet_git_provenance_unavailable") from exc

    if git_value("rev-parse", "--show-toplevel") != str(source_path):
        raise ValueError("canonical_packet_source_path_not_repository_root")
    if (
        git_value("rev-parse", "HEAD^{commit}").lower()
        != packet["source_commit_sha"].lower()
    ):
        raise ValueError("canonical_packet_source_commit_mismatch")

    def valid_branch_name(value: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "check-ref-format", "--branch", value],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except OSError:
            return False
        return result.returncode == 0 and result.stdout.strip() == value

    if not valid_branch_name(packet["source_branch"]):
        raise ValueError("canonical_packet_source_branch_invalid")
    base_branch = packet["base_branch"]
    if not valid_branch_name(base_branch):
        raise ValueError("canonical_packet_base_branch_invalid")
    if base_branch.startswith(("refs/heads/", "refs/remotes/")):
        base_refs = [base_branch]
    else:
        base_refs = [f"refs/heads/{base_branch}", f"refs/remotes/{base_branch}"]
    resolved_base_shas: list[str] = []
    for base_ref in base_refs:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source_path),
                "rev-parse",
                "--verify",
                f"{base_ref}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            resolved_base_shas.append(result.stdout.strip().lower())
    if not resolved_base_shas:
        raise ValueError("canonical_packet_base_branch_ref_missing")
    if set(resolved_base_shas) != {packet["base_commit_sha"].lower()}:
        raise ValueError("canonical_packet_base_commit_mismatch")

    changed_files = packet.get("changed_files")
    if (
        not isinstance(changed_files, list)
        or not changed_files
        or any(not isinstance(item, str) or not item.strip() for item in changed_files)
        or len(set(changed_files)) != len(changed_files)
    ):
        raise ValueError("canonical_packet_changed_files_invalid")
    lane_scope = packet.get("lane_scope")
    if not isinstance(lane_scope, dict):
        raise ValueError("canonical_packet_lane_scope_missing")
    for field in ("allowed_paths", "touched_paths"):
        values = lane_scope.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item.strip() for item in values)
        ):
            raise ValueError("canonical_packet_lane_scope_incomplete")
    if set(lane_scope["touched_paths"]) != set(changed_files):
        raise ValueError("canonical_packet_lane_scope_mismatch")

    artifacts = packet.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or not artifacts
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not item["path"].strip()
            for item in artifacts
        )
    ):
        raise ValueError("canonical_packet_artifacts_invalid")
    non_claims = packet.get("non_claims")
    if (
        not isinstance(non_claims, list)
        or not non_claims
        or any(not isinstance(item, str) or not item.strip() for item in non_claims)
    ):
        raise ValueError("canonical_packet_non_claims_missing")

    proof = packet.get("proof")
    if not isinstance(proof, dict):
        raise ValueError("canonical_packet_proof_incomplete")
    for field in (
        "command",
        "result",
        "log",
        "scope",
        "ad_hoc_or_canonical",
        "marker",
    ):
        if not isinstance(proof.get(field), str) or not proof[field].strip():
            raise ValueError("canonical_packet_proof_incomplete")
    proof_non_claims = proof.get("non_claims")
    if (
        not isinstance(proof_non_claims, list)
        or not proof_non_claims
        or any(
            not isinstance(item, str) or not item.strip() for item in proof_non_claims
        )
    ):
        raise ValueError("canonical_packet_proof_non_claims_missing")

    result = packet["result"].upper()
    if result not in {"PASS", "BLOCKED", "FAIL"} or proof["result"].upper() != result:
        raise ValueError("canonical_packet_result_invalid")
    expected_classification = {
        "PASS": "merge_ready",
        "BLOCKED": "blocked",
        "FAIL": "failed",
    }[result]
    if packet["classification"] != expected_classification:
        raise ValueError("canonical_packet_classification_mismatch")
    if proof["marker"] != packet["marker"]:
        raise ValueError("canonical_packet_marker_mismatch")
    return packet


def persisted_packet_text(row) -> str:
    """Render only the immutable packet read back from canonical persistence."""

    return json.dumps(row.packet, indent=2, sort_keys=True)


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
        terminal = status in {"completed", "failed", "blocked"}
        if not terminal and pid_live(row.get("pid")):
            out.append(f"AGY_STILL_RUNNING {ident} {run_id}")
            continue
        cmd = json.loads(row.get("command_json") or "[]")
        log_path = command_log_path(cmd)
        raw_bytes = log_path.read_bytes() if log_path and log_path.exists() else b""
        text = raw_bytes.decode("utf-8", errors="replace")
        capture = capture_raw_result_output(
            agent="agy",
            identifier=ident,
            run_id=run_id,
            text=text,
            raw_bytes=raw_bytes,
            artifact_path=log_path,
        )
        capture_error = durable_capture_error(
            capture,
            agent="agy",
            identifier=ident,
            run_id=run_id,
            raw_bytes=raw_bytes,
        )
        if capture_error:
            out.append(f"AGY_RAW_CAPTURE_FAILED {ident} {run_id} {capture_error}")
            continue

        try:
            parsed_packet = canonical_packet_from_text(
                text,
                expected_agent="agy",
                expected_identifier=ident,
                expected_run_id=run_id,
            )
        except Exception as exc:
            out.append(f"AGY_PACKET_PARSE_FAILED {ident} {run_id} {exc}")
            continue

        if parsed_packet:
            try:
                cw_store = AgyCompletedWorkStore()
                cw_row = cw_store.ingest(parsed_packet)
            except Exception as exc:
                out.append(f"AGY_PERSISTENCE_FAILED {ident} {run_id} {exc}")
                continue

            if (
                not cw_row.id
                or cw_row.ingestion_marker != AGY_COMPLETED_WORK_INGESTION_MARKER
                or cw_row.as_dict().get("integration_marker")
                != AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER
            ):
                out.append(f"AGY_MARKER_VALIDATION_FAILED {ident} {run_id}")
                continue

            result_status = cw_row.proof_result or "PASS"
            marker = cw_row.proof_marker or "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK"

            if result_status == "PASS":
                if (
                    cw_row.classification != "merge_ready"
                    or not cw_row.eligible_for_merge
                ):
                    out.append(
                        f"AGY_PERSISTED_NOT_MERGE_READY {ident} {run_id} classification={cw_row.classification}"
                    )
                    continue
                launch_status = "completed"
                event_type = "WORK_RESULT_PACKET"
            elif result_status == "BLOCKED":
                launch_status = "blocked"
                event_type = "WORK_BLOCKED"
            else:
                launch_status = "failed"
                event_type = "WORK_FAILED"

            marker_present = has_marker(ident, marker)
            if terminal and marker_present:
                continue

            if not marker_present:
                add_comment(
                    ident,
                    f"AGY completed-work packet from dispatcher log `{log_path}`:\n\n```json\n{persisted_packet_text(cw_row)}\n```\n\n```text\nCOMPLETED_WORK_ID={cw_row.id}\n```",
                )
            update_launch_status(run_id, launch_status)
            emit_visible_result_event(
                "agy", ident, event_type, marker=marker, log=str(log_path or "")
            )
            out.append(f"AGY_PACKET_WRITTEN {ident} {marker} result={result_status}")
            continue

        if terminal:
            # Historical terminal rows were already classified. Reconcile a real
            # packet above if writeback was missed, but do not manufacture a new
            # blocker for an already-terminal row with no packet.
            continue
        if "--headless" in cmd or "--issue" in cmd or "--task" in cmd:
            reason = "AGY launch used obsolete unsupported CLI flags (`--headless/--issue/--task`); process exited without result log. Dispatcher has been patched to use `agy --print ... --log-file ...`."
        elif log_path and log_path.exists():
            tail = text[-800:].replace("`", "'")
            reason = f"AGY process exited without canonical JSON packet. Log: `{log_path}` Tail: {tail}"
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
        raw_bytes = log_path.read_bytes() if log_path and log_path.exists() else b""
        text = raw_bytes.decode("utf-8", errors="replace")
        capture = capture_raw_result_output(
            agent=agent,
            identifier=ident,
            run_id=run_id,
            text=text,
            raw_bytes=raw_bytes,
            artifact_path=log_path,
        )
        capture_error = durable_capture_error(
            capture,
            agent=agent,
            identifier=ident,
            run_id=run_id,
            raw_bytes=raw_bytes,
        )
        if capture_error:
            out.append(
                f"{agent.upper()}_RAW_CAPTURE_FAILED {ident} {run_id} {capture_error}"
            )
            continue

        try:
            parsed_packet = canonical_packet_from_text(
                text,
                expected_agent=agent,
                expected_identifier=ident,
                expected_run_id=run_id,
            )
        except Exception as exc:
            out.append(f"{agent.upper()}_PACKET_PARSE_FAILED {ident} {run_id} {exc}")
            continue

        if parsed_packet:
            try:
                cw_store = AgyCompletedWorkStore()
                cw_row = cw_store.ingest(parsed_packet)
            except Exception as exc:
                out.append(f"{agent.upper()}_PERSISTENCE_FAILED {ident} {run_id} {exc}")
                continue

            if (
                not cw_row.id
                or cw_row.ingestion_marker != AGY_COMPLETED_WORK_INGESTION_MARKER
                or cw_row.as_dict().get("integration_marker")
                != AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER
            ):
                out.append(f"{agent.upper()}_MARKER_VALIDATION_FAILED {ident} {run_id}")
                continue

            result_status = cw_row.proof_result or "PASS"
            marker = cw_row.proof_marker or default_marker

            if result_status == "PASS":
                if (
                    cw_row.classification != "merge_ready"
                    or not cw_row.eligible_for_merge
                ):
                    out.append(
                        f"{agent.upper()}_PERSISTED_NOT_MERGE_READY {ident} {run_id} classification={cw_row.classification}"
                    )
                    continue
                launch_status = "completed"
                event_type = "WORK_RESULT_PACKET"
            elif result_status == "BLOCKED":
                launch_status = "blocked"
                event_type = "WORK_BLOCKED"
            else:
                launch_status = "failed"
                event_type = "WORK_FAILED"

            if not has_marker(ident, marker):
                add_comment(
                    ident,
                    f"{label} visible-execution packet from Hermes log `{log_path}`:\n\n```json\n{persisted_packet_text(cw_row)}\n```\n\n```text\nCOMPLETED_WORK_ID={cw_row.id}\n```",
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
            reason = f"{label} visible Hermes execution exited without canonical JSON packet. Log: `{log_path}` Tail: {tail}"
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
