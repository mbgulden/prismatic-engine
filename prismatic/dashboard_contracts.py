"""Portable dashboard contract verification for Prismatic Engine.

The verifier is intentionally lightweight: it validates that the governance
operator dashboard is reachable, canonical, API-backed, safe-control wired, and
free of scoped mock/fake fallbacks without mutating real state.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TEMPLATE = REPO_ROOT / "prismatic" / "gateway" / "templates" / "dashboard.html"


@dataclass(frozen=True)
class DashboardSectionContract:
    id: str
    name: str
    dashboard_markers: tuple[str, ...] = ()
    fetches: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    endpoints: tuple[str, ...] = ()
    detail_endpoints: tuple[str, ...] = ()
    control_endpoints: tuple[str, ...] = ()
    required_states: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CONTRACTS: dict[str, DashboardSectionContract] = {
    "agents": DashboardSectionContract(
        id="agents",
        name="Dashboard Summary / Agent Detail",
        dashboard_markers=("agent-status-source-line", "agent-status-counts", "agent-cards-grid", "agent-detail-card"),
        fetches=("`${API_PREFIX}/agents/status`", "`${API_PREFIX}/agents/${encodeURIComponent(id)}`"),
        endpoints=("GET /api/gateway/agents/status",),
        detail_endpoints=("GET /api/gateway/agents/{agent_id}",),
        required_states=("Loading live agent/worker evidence", "No known agents/workers returned", "Agent status API unavailable", "No mock fallback rendered"),
        forbidden_patterns=("mockAgents", "mockWorkers", "fake online"),
        evidence={"sources": ["run_records", "agent_registry", "queue_state", "timeline", "health_context"]},
    ),
    "queue": DashboardSectionContract(
        id="queue",
        name="Queue / Webhook / Retry Detail",
        dashboard_markers=("queue-detail-source-line", "queue-depth-chips", "queue-retry-candidates", "queue-dead-letter-items"),
        fetches=("`${API_PREFIX}/webhooks/queue/detail`", "`${API_PREFIX}/webhooks/queue/${encodeURIComponent(id)}`"),
        actions=("`${API_PREFIX}/webhooks/queue/retry/${encodeURIComponent(taskId)}`", "`${API_PREFIX}/webhooks/queue/purge`"),
        endpoints=("GET /api/gateway/webhooks/queue/detail", "GET /api/gateway/webhooks/stats"),
        detail_endpoints=("GET /api/gateway/webhooks/queue/{task_id}",),
        control_endpoints=("POST /api/gateway/webhooks/queue/retry/{task_id}", "POST /api/gateway/webhooks/queue/purge"),
        required_states=("Loading live queue evidence", "Queue detail API unavailable", "No mock queue fallback rendered"),
        forbidden_patterns=("mockQueue", "fake queue", "fake retry", "fake dead-letter", "hardcoded healthy queue"),
        evidence={"sources": ["run_records", "webhook_counters", "dispatcher_state", "recovery_state", "queue_control", "timeline"]},
    ),
    "timeline": DashboardSectionContract(
        id="timeline",
        name="Operational Timeline",
        dashboard_markers=("dashboard-activity", "signals-log-box"),
        fetches=("/api/timeline?limit=", "/api/timeline/summary"),
        endpoints=("GET /api/timeline", "GET /api/timeline/summary"),
        required_states=("Loading operational timeline", "Timeline API unavailable"),
        forbidden_patterns=("mockSignals",),
        evidence={"sources": ["timeline_manual_events", "run_records", "control_ledgers"]},
    ),
    "workspaces": DashboardSectionContract(
        id="workspaces",
        name="Workspaces",
        dashboard_markers=("section-workspaces", "workspaces-tbody"),
        fetches=("/api/workspaces",),
        endpoints=("GET /api/workspaces",),
        required_states=("Populated dynamically",),
        forbidden_patterns=("mockWorkspaceStatus",),
        evidence={"sources": ["workspace_registry", "git_status", "swarm_locks"]},
    ),
    "skills": DashboardSectionContract(
        id="skills",
        name="Core Skills",
        dashboard_markers=("section-skills", "skills-grid"),
        fetches=("/api/skills",),
        endpoints=("GET /api/skills",),
        required_states=("Populated dynamically",),
        forbidden_patterns=("mockSkills",),
        evidence={"sources": ["prismatic.skills"]},
    ),
    "agent_context": DashboardSectionContract(
        id="agent_context",
        name="Agent Context",
        dashboard_markers=("agent-context", "context"),
        fetches=("/api/agent-context",),
        endpoints=("GET /api/agent-context",),
        required_states=(),
        forbidden_patterns=("mockAgentContext",),
        evidence={"sources": ["prismatic.agent_context"]},
    ),
    "foundation": DashboardSectionContract(
        id="foundation",
        name="Foundation / Peer Review",
        dashboard_markers=("section-foundation", "foundation-state-panel", "foundation-console"),
        fetches=("`${API_PREFIX}/foundation/peer_review`",),
        actions=("`${API_PREFIX}/foundation/control/${action}`",),
        endpoints=("GET /api/gateway/foundation/peer_review",),
        control_endpoints=("POST /api/gateway/foundation/control/{action}",),
        required_states=("Loading Foundation / Peer Review status", "Foundation / Peer Review API unavailable", "No fallback data rendered"),
        forbidden_patterns=("mockFoundation", "STDOUT"),
        evidence={"sources": ["run_records", "foundation_control_state"]},
    ),
    "merge": DashboardSectionContract(
        id="merge",
        name="Merge Pipeline",
        dashboard_markers=("section-merge", "merge-state-panel", "merge-control-console"),
        fetches=("/api/gateway/merge/status",),
        actions=("`/api/gateway/merge/control/${action}`",),
        endpoints=("GET /api/gateway/merge/status",),
        control_endpoints=("POST /api/gateway/merge/control/{action}",),
        required_states=("Loading Merge Pipeline status", "Merge Pipeline API unavailable", "No fallback data rendered"),
        forbidden_patterns=("mockMerge", "auto-merges to staging", "STDOUT"),
        evidence={"sources": ["merge_state", "governance_triage", "merge_control_state"]},
    ),
    "quota": DashboardSectionContract(
        id="quota",
        name="GCP Quotas / Cost",
        dashboard_markers=("section-quota", "quota-source-line", "quota-cards-grid"),
        fetches=("/api/quota",),
        actions=("/api/quota/poll",),
        endpoints=("GET /api/quota",),
        control_endpoints=("POST /api/quota/poll",),
        required_states=("Loading quota and subscription pressure", "Waiting for quota source evidence"),
        forbidden_patterns=("mockQuota", "fake spend", "fake credits"),
        evidence={"sources": ["dashboard_quota_state", "cost_tracker"]},
    ),
    "dispatcher_recovery": DashboardSectionContract(
        id="dispatcher_recovery",
        name="Dispatcher / Recovery / Webhook Ingestion",
        dashboard_markers=("disp-status-badge", "failure-taxonomy-grid", "recovery-pool-state"),
        fetches=("`${API_PREFIX}/dispatcher/status`", "`${API_PREFIX}/recovery/status`", "`${API_PREFIX}/webhooks/stats`"),
        actions=("`${API_PREFIX}/dispatcher/${action}`",),
        endpoints=("GET /api/gateway/dispatcher/status", "GET /api/gateway/recovery/status", "GET /api/gateway/webhooks/stats"),
        control_endpoints=("POST /api/gateway/dispatcher/{action}",),
        required_states=("Loading recovery taxonomy", "Recovery status API unavailable"),
        forbidden_patterns=("fake-broken-service", "[SYNTHETIC TEST]"),
        evidence={"sources": ["webhook_counters", "dispatcher_control_state", "recovery_control_state", "run_records"]},
    ),
}

SAFE_CONTROL_SOURCES = {
    "queue_retry": "QueueControl",
    "queue_purge": "QueueControl",
    "dispatcher": "DispatcherControl",
    "foundation": "FoundationControl",
    "merge": "MergeControl",
    "quota_poll": "QuotaControl",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dashboard_contract_manifest() -> dict[str, Any]:
    return {
        "source": "prismatic.dashboard_contracts",
        "generated_at": now_iso(),
        "dashboard_route": "/dashboard",
        "sections": [contract.to_dict() for contract in CONTRACTS.values()],
        "required_endpoints": sorted({endpoint for contract in CONTRACTS.values() for endpoint in (*contract.endpoints, *contract.detail_endpoints)}),
        "safe_controls": [
            {"id": key, "timeline_source": source, "audit_only": True, "stdout": "", "stderr": ""}
            for key, source in SAFE_CONTROL_SOURCES.items()
        ],
        "forbidden_patterns": sorted({pattern for contract in CONTRACTS.values() for pattern in contract.forbidden_patterns}),
        "verification_notes": {
            "scope": "dashboard section contract verification",
            "labels": ["ad-hoc targeted verification", "not full suite-green"],
            "public_url": "classified separately as reachable/access_blocked/wrong_surface/unavailable",
        },
    }


def selected_contracts(section: str | None = None) -> list[DashboardSectionContract]:
    if not section or section == "all":
        return list(CONTRACTS.values())
    keys = [s.strip().replace("-", "_") for s in section.split(",") if s.strip()]
    missing = [key for key in keys if key not in CONTRACTS]
    if missing:
        raise ValueError(f"unknown section(s): {', '.join(missing)}")
    return [CONTRACTS[key] for key in keys]


def extract_dashboard_script(html: str) -> str:
    start = html.index("<script>") + len("<script>")
    end = html.rindex("</script>")
    return html[start:end]


def check_dashboard_html(html: str, section: str = "all") -> dict[str, Any]:
    sections = selected_contracts(section)
    failures: list[dict[str, str]] = []
    for marker in ("Prismatic Engine", "API_PREFIX", "section-dashboard"):
        if marker not in html:
            failures.append({"scope": "canonical", "missing": marker})
    for contract in sections:
        for marker in contract.dashboard_markers:
            if marker and marker not in html:
                failures.append({"section": contract.id, "missing_marker": marker})
        for needle in (*contract.fetches, *contract.actions, *contract.required_states):
            if needle and needle not in html:
                failures.append({"section": contract.id, "missing_wiring_or_state": needle})
        for pattern in contract.forbidden_patterns:
            if pattern and pattern in html:
                failures.append({"section": contract.id, "forbidden_pattern": pattern})
    return {"ok": not failures, "sections_checked": [c.id for c in sections], "failures": failures}


def run_node_check(html: str) -> dict[str, Any]:
    script = extract_dashboard_script(html)
    fd, path = tempfile.mkstemp(prefix="hermes-dashboard-contract-script-", suffix=".js", dir="/tmp")
    os.close(fd)
    script_path = Path(path)
    script_path.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run(["node", "--check", str(script_path)], text=True, capture_output=True, timeout=30)
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr}
    finally:
        script_path.unlink(missing_ok=True)


def _urlopen_json(base_url: str, path: str, *, method: str = "GET", timeout: int = 20) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read().decode("utf-8", "replace")
        try:
            return res.status, json.loads(body)
        except json.JSONDecodeError:
            return res.status, body


def _urlopen_text(base_url: str, path: str, timeout: int = 20) -> tuple[int, str, str]:
    url = base_url.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return res.status, res.geturl(), res.read().decode("utf-8", "replace")


def classify_public_dashboard(public_url: str, timeout: int = 20) -> dict[str, Any]:
    try:
        status, final_url, body = _urlopen_text(public_url, "/dashboard", timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
        if exc.code in {401, 403} or "cloudflare" in body.lower() or "access" in body.lower():
            return {"status": "access_blocked", "http_status": exc.code}
        return {"status": "unavailable", "http_status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}
    if status == 200 and "Prismatic Engine" in body and "API_PREFIX" in body:
        return {"status": "reachable", "http_status": status, "final_url": final_url}
    if status == 200:
        return {"status": "wrong_surface", "http_status": status, "final_url": final_url}
    return {"status": "unavailable", "http_status": status, "final_url": final_url}


def _assert_keys(payload: Any, keys: tuple[str, ...], label: str) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{label}: expected object"]
    return [f"{label}: missing {key}" for key in keys if key not in payload]


def verify_base_url(base_url: str, *, section: str = "all", timeout: int = 20, exercise_controls: bool = True) -> dict[str, Any]:
    failures: list[str] = []
    status, _final, html = _urlopen_text(base_url, "/dashboard", timeout=timeout)
    if status != 200:
        failures.append(f"/dashboard HTTP {status}")
    html_check = check_dashboard_html(html, section=section)
    failures.extend([json.dumps(item, sort_keys=True) for item in html_check["failures"]])
    contract_status, manifest = _urlopen_json(base_url, "/api/gateway/dashboard/contracts", timeout=timeout)
    if contract_status != 200:
        failures.append(f"contract manifest HTTP {contract_status}")
    failures.extend(_assert_keys(manifest, ("source", "generated_at", "dashboard_route", "sections", "required_endpoints", "safe_controls"), "contracts"))

    # Always verify the two hardened sections deeply; other selected endpoints are shape-smoked if reachable.
    agent_status, agents = _urlopen_json(base_url, "/api/gateway/agents/status", timeout=timeout)
    if agent_status == 200:
        failures.extend(_assert_keys(agents, ("source", "generated_at", "status_counts", "agents", "evidence"), "agents/status"))
        if isinstance(agents, dict) and agents.get("agents"):
            agent_id = agents["agents"][0].get("id")
            detail_status, detail = _urlopen_json(base_url, f"/api/gateway/agents/{urllib.parse.quote(str(agent_id))}", timeout=timeout)
            if detail_status != 200:
                failures.append(f"agent detail HTTP {detail_status}")
            failures.extend(_assert_keys(detail, ("agent", "recent_runs", "recent_timeline", "queue_context", "health_context", "evidence"), "agent/detail"))
    else:
        failures.append(f"agents/status HTTP {agent_status}")

    queue_status, queue = _urlopen_json(base_url, "/api/gateway/webhooks/queue/detail", timeout=timeout)
    if queue_status == 200:
        failures.extend(_assert_keys(queue, ("source", "generated_at", "queue_depths", "items", "retry_candidates", "dead_letter", "skipped", "processing", "evidence"), "queue/detail"))
        retry_id = None
        if isinstance(queue, dict):
            for item in queue.get("retry_candidates", []):
                retry_id = item.get("id") or item.get("run_id")
                if retry_id:
                    break
        if retry_id:
            q_detail_status, q_detail = _urlopen_json(base_url, f"/api/gateway/webhooks/queue/{urllib.parse.quote(str(retry_id))}", timeout=timeout)
            if q_detail_status != 200:
                failures.append(f"queue item detail HTTP {q_detail_status}")
            failures.extend(_assert_keys(q_detail, ("item", "run_record", "recent_timeline", "retry_history", "recovery_context", "evidence"), "queue/item"))
            if exercise_controls:
                retry_status, retry = _urlopen_json(base_url, f"/api/gateway/webhooks/queue/retry/{urllib.parse.quote(str(retry_id))}", method="POST", timeout=timeout)
                if retry_status != 200:
                    failures.append(f"queue retry HTTP {retry_status}")
                if isinstance(retry, dict):
                    if retry.get("stdout") != "" or retry.get("stderr") != "":
                        failures.append("queue retry stdout/stderr not empty")
                    if (retry.get("timeline_item") or {}).get("source") != "QueueControl":
                        failures.append("queue retry missing QueueControl timeline")
                purge_status, purge = _urlopen_json(base_url, "/api/gateway/webhooks/queue/purge", method="POST", timeout=timeout)
                if purge_status != 200:
                    failures.append(f"queue purge HTTP {purge_status}")
                if isinstance(purge, dict):
                    if purge.get("stdout") != "" or purge.get("stderr") != "":
                        failures.append("queue purge stdout/stderr not empty")
                    if (purge.get("timeline_item") or {}).get("source") != "QueueControl":
                        failures.append("queue purge missing QueueControl timeline")
                timeline_status, timeline = _urlopen_json(base_url, "/api/timeline?source=QueueControl&limit=10", timeout=timeout)
                if timeline_status == 200 and not any(item.get("source") == "QueueControl" for item in timeline.get("items", [])):
                    failures.append("timeline missing QueueControl event")
        else:
            failures.append("queue/detail has no retry candidate fixture")
    else:
        failures.append(f"queue/detail HTTP {queue_status}")

    shape_smokes = {
        "/health": ("status",),
        "/api/timeline?limit=10": ("items",),
        "/api/timeline/summary": ("by_kind", "by_severity"),
        "/api/gateway/webhooks/stats": ("source", "queue_depths"),
        "/api/gateway/dispatcher/status": ("source", "status"),
        "/api/gateway/recovery/status": ("source", "failure_taxonomy"),
        "/api/gateway/foundation/peer_review": ("source", "evidence"),
        "/api/gateway/merge/status": ("source", "evidence"),
        "/api/quota": ("source", "evidence"),
        "/api/workspaces": ("source",),
        "/api/skills": ("source",),
        "/api/agent-context": ("source",),
    }
    endpoint_results: dict[str, str] = {}
    for path, keys in shape_smokes.items():
        try:
            code, payload = _urlopen_json(base_url, path, timeout=timeout)
            endpoint_results[path] = "passed" if code == 200 else f"HTTP {code}"
            if code == 200:
                failures.extend(_assert_keys(payload, keys, path))
        except Exception as exc:
            endpoint_results[path] = f"failed: {exc}"
            failures.append(f"{path}: {exc}")
    return {
        "ok": not failures,
        "base_url": base_url,
        "html_contract": html_check,
        "contract_manifest": "passed" if not _assert_keys(manifest, ("sections",), "contracts") else "failed",
        "endpoint_results": endpoint_results,
        "failures": failures,
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def seed_isolated_state(root: Path) -> dict[str, str]:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.environ["PRISMATIC_STATE_DIR"] = str(state)
    agent_registry = root / "agent_registry.json"
    agent_registry.write_text(json.dumps({"agents": [{"id": "fred", "name": "Fred", "role": "orchestrator"}]}), encoding="utf-8")
    os.environ["PRISMATIC_AGENT_REGISTRY"] = str(agent_registry)
    merge_state = root / "merge_state.json"
    merge_state.write_text(json.dumps({"last_scan": now_iso(), "pending": {"GRO-7001": {"mergeable": True, "checks": {"passed": True}, "files": ["README.md"]}}, "merged": {}}), encoding="utf-8")
    os.environ["PRISMATIC_MERGE_STATE_PATH"] = str(merge_state)

    from prismatic.run_records import AgentRunRecord, AgentRunRecordStore

    def iso(delta: int = 0) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()

    store = AgentRunRecordStore(str(state / "run_records.json"))
    records = [
        AgentRunRecord("run-active", "GRO-701", "agent:agy", status="running", started_at=iso(-500)),
        AgentRunRecord("run-idle", "GRO-702", "agent:fred", status="completed", started_at=iso(-90000), completed_at=iso(-89000)),
        AgentRunRecord("run-pending", "GRO-703", "agent:jules", status="pending", started_at=iso(-600)),
        AgentRunRecord("run-feedback", "GRO-704", "agent:ned", status="blocked", started_at=iso(-700), error_message="awaiting user feedback"),
        AgentRunRecord("run-completed", "GRO-705", "agent:kai", status="completed", started_at=iso(-800), completed_at=iso(-100)),
        AgentRunRecord("run-failed", "GRO-706", "agent:codex", status="failed", started_at=iso(-800), completed_at=iso(-200), error_message="unit test failure"),
        AgentRunRecord("run-dlq", "GRO-707", "agent:ned", status="failed", started_at=iso(-800), completed_at=iso(-300), error_message="dead_letter after max retries"),
        AgentRunRecord("run-skipped", "GRO-708", "agent:fred", status="skipped", started_at=iso(-700), completed_at=iso(-350), error_message="skipped by quarantine label"),
    ]
    store._records = {record.run_id: record for record in records}
    store._flush_to_disk()
    return {"state_dir": str(state), "agent_registry": str(agent_registry), "merge_state": str(merge_state)}


def start_temp_gateway(root: Path, port: int | None = None) -> tuple[subprocess.Popen[str], str, dict[str, str]]:
    env_paths = seed_isolated_state(root)
    selected_port = port or _free_port()
    env = os.environ.copy()
    env.update(env_paths)
    env["PYTHONPATH"] = f"{Path.home() / '.local/lib/python3.12/site-packages'}:{REPO_ROOT}:{env.get('PYTHONPATH', '')}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "prismatic.gateway.server:app", "--host", "127.0.0.1", "--port", str(selected_port)],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{selected_port}"
    deadline = time.time() + 30
    last_error = None
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"gateway exited early: {output}")
        try:
            _urlopen_json(base_url, "/health", timeout=2)
            return proc, base_url, env_paths
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    proc.terminate()
    raise TimeoutError(f"gateway readiness timeout: {last_error}")


def verify_local_smoke(section: str = "all", port: int | None = None, timeout: int = 20) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hermes-dashboard-contract-state-") as tmp:
        root = Path(tmp)
        proc: subprocess.Popen[str] | None = None
        try:
            proc, base_url, env_paths = start_temp_gateway(root, port=port)
            result = verify_base_url(base_url, section=section, timeout=timeout, exercise_controls=True)
            result["temp_state"] = "removed"
            result["gateway"] = "killed"
            result["env_paths"] = env_paths
            return result
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def verify_source_contract(section: str = "all") -> dict[str, Any]:
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    html_check = check_dashboard_html(html, section=section)
    node_check = run_node_check(html)
    return {"ok": html_check["ok"] and node_check["ok"], "html_contract": html_check, "node_check": {"ok": node_check["ok"], "stderr": node_check["stderr"]}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Prismatic dashboard section contracts.")
    parser.add_argument("--base-url")
    parser.add_argument("--public-url")
    parser.add_argument("--section", default="all")
    parser.add_argument("--isolated-state", action="store_true")
    parser.add_argument("--start-local-gateway", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--skip-public", action="store_true")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "AD_HOC_VERIFICATION": "PENDING",
        "scope": "Public / live dashboard contract verification hardening",
        "generated_at": now_iso(),
        "section": args.section,
        "source_contract": None,
        "base_url_smoke": None,
        "local_live_smoke": None,
        "public_smoke": "skipped",
        "failures": [],
    }
    try:
        source = verify_source_contract(args.section)
        report["source_contract"] = source
        if not source["ok"]:
            report["failures"].append("source contract failed")
            if args.fail_fast:
                raise RuntimeError("source contract failed")
        if args.start_local_gateway or args.isolated_state:
            local = verify_local_smoke(section=args.section, port=args.port, timeout=args.timeout)
            report["local_live_smoke"] = local
            if not local["ok"]:
                report["failures"].extend(local.get("failures", []))
                if args.fail_fast:
                    raise RuntimeError("local live smoke failed")
        if args.base_url:
            base = verify_base_url(args.base_url, section=args.section, timeout=args.timeout)
            report["base_url_smoke"] = base
            if not base["ok"]:
                report["failures"].extend(base.get("failures", []))
        if args.public_url and not args.skip_public:
            report["public_smoke"] = classify_public_dashboard(args.public_url, timeout=args.timeout)
        report["AD_HOC_VERIFICATION"] = "PASS" if not report["failures"] else "FAIL"
    except Exception as exc:
        report["AD_HOC_VERIFICATION"] = "FAIL"
        report["failures"].append(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"AD_HOC_VERIFICATION={report['AD_HOC_VERIFICATION']}")
        print(f"scope={report['scope']}")
        if report["failures"]:
            print("failures:")
            for failure in report["failures"]:
                print(f"- {failure}")
    return 0 if report["AD_HOC_VERIFICATION"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
