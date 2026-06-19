#!/usr/bin/env python3
"""Prismatic Journal continuity kernel.

Harness-agnostic implementation of the Journal Continuity Audit canary.
The engine owns deterministic work: inventory, snapshot/event indexing,
Linear import readiness checks, and artifact validation. Harnesses own cron,
profile secrets, dashboards, and notification routing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

LINEAR_URL = "https://api.linear.app/graphql"
DEFAULT_TEAM_ID = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"
DEFAULT_JCA_PROJECT_ID = "ece6a786-c1a8-477d-87cb-1fde304e5d4b"
DEFAULT_STATE_TODO = "3d29ebe3-00cf-428b-b52a-bfecb5ae4410"
DEFAULT_STATE_IN_PROGRESS = "734901ee-58f0-457c-b9a0-f911c0da13a4"
DEFAULT_LABELS = {
    "agent:agy": "1b69d9c0-20a8-45b3-a594-771b8cba75a7",
    "agent:fred": "a43efb77-534a-4e39-8ff3-76f0e42019d1",
    "pipeline:research-strategy": "f7f6e8f7-abe9-4b9b-a73a-b1d391c551f6",
    "type:research": "b721e7a8-68e0-46fa-aeb1-7dc007cfe80a",
    "type:docs": "d24a4a88-00d8-40e7-9e58-6fdfc8a1a6b6",
}
SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret|oauth code)\b\s*[:=]\s*[^\s'\"]+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|xox[a-z]-[A-Za-z0-9-]+"), "[REDACTED]"),
]
ERROR_PATTERNS = [
    (r"rate.?limit", "Rate limit hit"),
    (r"token.?limit|context.?length.?exceeded", "Token/context limit exceeded"),
    (r"timed?.?out|timeout", "Timeout detected"),
    (r"error.*authenticate|unauthorized|401|403", "Authentication error"),
    (r"traceback.*most recent call last", "Python traceback in output"),
    (r"\[ERROR\]|\[FATAL\]|\[CRITICAL\]", "Explicit error log marker"),
    (r"segmentation fault|SIGSEGV|SIGABRT", "Process crash signal"),
    (r"out of memory|OOM|MemoryError", "Out of memory"),
    (r"connection refused|connection reset|ECONNREFUSED", "Connection failure"),
    (r"failed to (create|write|open|read|parse|load|import|connect)", "Operation failure"),
    (r"panic:|fatal error|unrecoverable", "Fatal error"),
]
FILE_PATH_PATTERN = re.compile(r"(?:^|\s)(/(?:home|tmp|etc|var|opt|usr)/[^\s:,;)\]]+)")


def _default_workspace() -> Path:
    return Path(os.environ.get("PRISMATIC_HOME", str(Path.home() / "work"))).expanduser()


def _default_harness_profile() -> Path:
    explicit = os.environ.get("PRISMATIC_HARNESS_PROFILE") or os.environ.get("HERMES_PROFILE")
    if explicit:
        return Path(explicit).expanduser()
    workspace = _default_workspace()
    parent = workspace.parent if workspace.name == "work" else Path.home()
    return parent / ".hermes" / "profiles" / "orchestrator"


@dataclass(frozen=True)
class JournalConfig:
    workspace: Path
    harness_profile: Path
    research_repo: Path
    journal_root: Path
    report_root: Path
    doc_root: Path
    sessions_dir: Path
    cron_jobs: Path
    project_registry: Path
    team_id: str
    project_id: str
    state_todo: str
    state_in_progress: str
    labels: dict[str, str]
    linear_url: str = LINEAR_URL

    @classmethod
    def from_env(cls) -> "JournalConfig":
        workspace = _default_workspace()
        harness_profile = _default_harness_profile()
        research_repo = Path(os.environ.get("PRISMATIC_JOURNAL_REPO", str(workspace / "Hermes-Research"))).expanduser()
        journal_root = Path(os.environ.get("PRISMATIC_JOURNAL_ROOT", str(research_repo / "journals"))).expanduser()
        report_root = Path(os.environ.get("PRISMATIC_JOURNAL_REPORT_ROOT", str(research_repo / "reports" / "journal-continuity-audit"))).expanduser()
        doc_root = Path(os.environ.get("PRISMATIC_JOURNAL_DOC_ROOT", str(research_repo / "docs" / "journal-continuity-audit"))).expanduser()
        sessions_dir = Path(os.environ.get("PRISMATIC_JOURNAL_SESSIONS_DIR", str(harness_profile / "sessions"))).expanduser()
        cron_jobs = Path(os.environ.get("PRISMATIC_CRON_JOBS", str(harness_profile / "cron" / "jobs.json"))).expanduser()
        project_registry = Path(os.environ.get("PRISMATIC_PROJECT_REGISTRY", str(workspace / "project-registry.json"))).expanduser()
        labels = dict(DEFAULT_LABELS)
        if os.environ.get("PRISMATIC_JOURNAL_LABELS_JSON"):
            labels.update(json.loads(os.environ["PRISMATIC_JOURNAL_LABELS_JSON"]))
        return cls(
            workspace=workspace,
            harness_profile=harness_profile,
            research_repo=research_repo,
            journal_root=journal_root,
            report_root=report_root,
            doc_root=doc_root,
            sessions_dir=sessions_dir,
            cron_jobs=cron_jobs,
            project_registry=project_registry,
            team_id=os.environ.get("PRISMATIC_LINEAR_TEAM_ID", DEFAULT_TEAM_ID),
            project_id=os.environ.get("PRISMATIC_JOURNAL_LINEAR_PROJECT_ID", DEFAULT_JCA_PROJECT_ID),
            state_todo=os.environ.get("PRISMATIC_LINEAR_STATE_TODO", DEFAULT_STATE_TODO),
            state_in_progress=os.environ.get("PRISMATIC_LINEAR_STATE_IN_PROGRESS", DEFAULT_STATE_IN_PROGRESS),
            labels=labels,
        )


def period_default() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")


def file_range(files: Iterable[str | Path]) -> dict[str, Any]:
    normalized = sorted(str(Path(f)) for f in files)
    if not normalized:
        return {"count": 0, "first": None, "last": None, "files": []}
    return {"count": len(normalized), "first": normalized[0], "last": normalized[-1], "files": normalized}


def _scan_top_level_files(root: Path, suffixes: tuple[str, ...]) -> list[dict[str, Any]]:
    entries = []
    if not root.exists():
        return entries
    for entry in os.scandir(root):
        if entry.is_file() and entry.name.endswith(suffixes):
            st = entry.stat()
            entries.append({
                "name": entry.name,
                "path": entry.path,
                "mtime": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(),
                "size": st.st_size,
            })
    return sorted(entries, key=lambda x: x["mtime"])


def safe_session_inventory(config: JournalConfig) -> dict[str, Any]:
    if not config.sessions_dir.exists():
        return {"exists": False, "count": 0}
    entries = _scan_top_level_files(config.sessions_dir, (".json", ".jsonl"))
    if not entries:
        return {"exists": True, "count": 0}
    return {
        "exists": True,
        "count": len(entries),
        "oldest": entries[0],
        "newest": entries[-1],
        "sample_oldest_5": entries[:5],
        "sample_newest_10": entries[-10:],
    }


def cron_inventory(config: JournalConfig) -> dict[str, Any]:
    if not config.cron_jobs.exists():
        return {"exists": False, "jobs": []}
    data = json.loads(config.cron_jobs.read_text())
    raw_jobs = data if isinstance(data, list) else data.get("jobs", []) if isinstance(data, dict) else []
    jobs = []
    keywords = ("journal", "memory", "morning", "golden", "agy", "continuity")
    for job in raw_jobs:
        if not isinstance(job, dict):
            continue
        blob = " ".join(str(job.get(k) or "") for k in ("name", "script", "prompt")).lower()
        if any(k in blob for k in keywords):
            jobs.append({
                "id": job.get("id") or job.get("job_id"),
                "name": job.get("name"),
                "enabled": job.get("enabled", not job.get("paused", False)),
                "schedule": job.get("schedule_display") or job.get("schedule"),
                "last_run_at": job.get("last_run_at"),
                "last_status": job.get("last_status"),
                "deliver": job.get("deliver"),
                "script": job.get("script"),
            })
    return {"exists": True, "count": len(jobs), "jobs": jobs}


def build_inventory(period: str, config: JournalConfig | None = None) -> tuple[Path, Path]:
    config = config or JournalConfig.from_env()
    out_dir = config.report_root / period
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = glob.glob(str(config.journal_root / "20*" / "*" / "*.md"))
    inbox = glob.glob(str(config.journal_root / "inbox" / "*.md"))
    weekly = glob.glob(str(config.journal_root / "weekly" / "*.md"))
    events = glob.glob(str(config.journal_root / ".index" / "events-*.json"))
    latest = [p for p in [config.journal_root / "latest.md", config.journal_root / "latest-inbox.md", config.journal_root / "latest-weekly.md"] if p.exists()]
    inventory = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "period": period,
        "docs": {"plan": str(config.doc_root / "README.md"), "sequence": str(config.doc_root / "workflow-sequence.json")},
        "journal_root": str(config.journal_root),
        "dated_journals": file_range(dated),
        "inbox_journals": file_range(inbox),
        "weekly_rollups": file_range(weekly),
        "event_indexes": file_range(events),
        "latest_pointers": file_range(latest),
        "sessions": safe_session_inventory(config),
        "cron": cron_inventory(config),
        "project_registry": {"path": str(config.project_registry), "exists": config.project_registry.exists()},
        "linear": {"team": "GRO", "team_id": config.team_id, "project_id": config.project_id, "project_name": "Journal Continuity Audit"},
        "constraints": [
            "Do not recursively scan /home.",
            "Do not read all sessions at once; sample and search only after the audit identifies a needed date/source.",
            "Pure audit must not modify files, cron, or Linear.",
            "Only active sequence step gets an execution label.",
        ],
    }
    json_path = out_dir / "source-inventory.json"
    md_path = out_dir / "source-inventory.md"
    json_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    md = [
        f"# Journal Continuity Audit Source Inventory — {period}",
        "",
        f"Generated: {inventory['generated_at']}",
        "",
        "## Coverage",
        f"- Dated journals: {inventory['dated_journals']['count']} — {inventory['dated_journals']['first']} → {inventory['dated_journals']['last']}",
        f"- Inbox journals: {inventory['inbox_journals']['count']} — {inventory['inbox_journals']['first']} → {inventory['inbox_journals']['last']}",
        f"- Weekly rollups: {inventory['weekly_rollups']['count']} — {inventory['weekly_rollups']['first']} → {inventory['weekly_rollups']['last']}",
        f"- Event indexes: {inventory['event_indexes']['count']} — {inventory['event_indexes']['first']} → {inventory['event_indexes']['last']}",
        f"- Session files: {inventory['sessions'].get('count', 0)} — oldest {inventory['sessions'].get('oldest', {}).get('mtime')} → newest {inventory['sessions'].get('newest', {}).get('mtime')}",
        "",
        "## Relevant cron jobs",
    ]
    for job in inventory["cron"]["jobs"]:
        md.append(f"- `{job['id']}` — {job['name']} — enabled={job['enabled']} — schedule={job['schedule']} — last={job['last_status']} — deliver={job['deliver']} — script={job['script']}")
    md += [
        "",
        "## Audit instruction",
        "Read this inventory and the plan/sequence docs. Then inspect the listed journals/event indexes only. If a finding requires raw session proof, identify the exact date/session candidate instead of opening all sessions.",
        "",
        "Write the crack-audit report to:",
        f"- `{out_dir / 'agy-crack-audit.md'}`",
    ]
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return json_path, md_path


def _load_key(config: JournalConfig) -> str:
    key = os.environ.get("LINEAR_API_KEY", "").strip().strip('"').strip("'")
    if key:
        return key
    env_path = config.harness_profile / ".env"
    if env_path.exists():
        for line in env_path.read_text(errors="ignore").splitlines():
            if line.startswith("LINEAR_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def gql(query: str, variables: dict[str, Any] | None = None, config: JournalConfig | None = None) -> dict[str, Any]:
    config = config or JournalConfig.from_env()
    key = _load_key(config)
    if not key:
        return {"errors": [{"message": "LINEAR_API_KEY missing"}]}
    # GRO-2054: gate every Linear API call through LinearBudget so journal.py
    # can't silently burn the 2500/hr budget (e.g., during a hot loop of issue
    # creation). Pattern from GRO-2034 in agent_dispatcher.py::_linear_gql.
    try:
        from prismatic.linear.budget import linear_budget
        if not linear_budget.check_and_consume("prismatic.journal"):
            return {"errors": [{"message": "Linear API budget exceeded — call refused"}]}
    except ImportError:
        # LinearBudget not importable (e.g., partial install) — proceed but warn
        import sys
        print("[journal] WARNING: LinearBudget not importable — proceeding without gate", file=sys.stderr)
    req = urllib.request.Request(
        config.linear_url,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            return {"errors": [{"message": f"HTTP {exc.code}: {exc.reason}"}]}


def find_issue_by_title(title: str, config: JournalConfig) -> dict[str, Any] | None:
    data = gql(
        "query($project:ID!){ issues(first:100, filter:{project:{id:{eq:$project}}}) { nodes { id identifier title url labels { nodes { name } } } } }",
        {"project": config.project_id},
        config,
    )
    if data.get("errors"):
        raise SystemExit("Linear errors: " + json.dumps(data["errors"], indent=2)[:2000])
    return next((i for i in data["data"]["issues"]["nodes"] if i["title"] == title), None)


def create_issue(title: str, desc: str, state_id: str, label_names: list[str], config: JournalConfig) -> tuple[dict[str, Any], bool]:
    existing = find_issue_by_title(title, config)
    if existing:
        return existing, False
    payload = {
        "teamId": config.team_id,
        "projectId": config.project_id,
        "stateId": state_id,
        "title": title,
        "description": desc,
        "priority": 2,
        "labelIds": [config.labels[n] for n in label_names if n in config.labels],
    }
    data = gql(
        "mutation($input: IssueCreateInput!){ issueCreate(input:$input){ success issue { id identifier title url labels { nodes { name } } } } }",
        {"input": payload},
        config,
    )
    if data.get("errors"):
        raise SystemExit("Linear errors: " + json.dumps(data["errors"], indent=2)[:2000])
    return data["data"]["issueCreate"]["issue"], True


def create_monthly(period: str, config: JournalConfig | None = None) -> dict[str, Any]:
    config = config or JournalConfig.from_env()
    json_path, md_path = build_inventory(period, config)
    control_title = f"[MONTHLY JCA {period}] Journal Continuity Audit control"
    audit_title = f"[MONTHLY JCA {period}] AGY read-only crack audit"
    control_desc = f"Monthly Journal Continuity Audit control for {period}.\n\nInventory created:\n- {json_path}\n- {md_path}\n\nSequence rule: only the active audit task has agent:agy. Downstream synthesis/import tasks are created after AGY output exists."
    audit_desc = f"Audit task. Pure research. Do NOT modify files, cron, Linear, or journals.\n\nRead:\n- {config.doc_root / 'README.md'}\n- {config.doc_root / 'workflow-sequence.json'}\n- {md_path}\n- {json_path}\n\nThen read listed journal/event files and write only:\n- {config.report_root / period / 'agy-crack-audit.md'}\n\nRequired sections: Fallen Through the Cracks; False Stale / Already Done; Strategic Through-lines; Revenue / Leads / Trust; Enforcement Gaps; Recommended Linear Backlog; Needs Michael. Cite exact files/dates."
    control, c_new = create_issue(control_title, control_desc, config.state_in_progress, ["agent:fred", "pipeline:research-strategy", "type:docs"], config)
    audit, a_new = create_issue(audit_title, audit_desc, config.state_todo, ["agent:agy", "pipeline:research-strategy", "type:research"], config)
    return {"period": period, "inventory": str(md_path), "control": control, "control_created": c_new, "agy_audit": audit, "agy_created": a_new}


def redact(text: str) -> str:
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def read_text(path: Path, limit: int = 8000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def collect_candidates(config: JournalConfig, since: float | None = None) -> list[Path]:
    since = since or dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    roots = [config.sessions_dir, config.harness_profile / "cron" / "output", config.harness_profile / "logs", config.research_repo / "docs"]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl", ".md", ".log", ".txt", ".yaml", ".yml"}:
                continue
            try:
                if path.stat().st_mtime >= since:
                    files.append(path)
            except FileNotFoundError:
                continue
    return sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)


def git(repo: Path, cmd: list[str]) -> str:
    try:
        res = subprocess.run(["git", "-C", str(repo), *cmd], capture_output=True, text=True, check=False)
        return (res.stdout or res.stderr or "").strip()
    except Exception:
        return ""


def extract_session_signals(path: Path) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    text = read_text(path, 30000)
    try:
        data = json.loads(text)
    except Exception:
        return signals
    messages = data if isinstance(data, list) else data.get("messages", data.get("conversation", [])) if isinstance(data, dict) else []
    if not isinstance(messages, list):
        return signals
    model = ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not isinstance(content, str) or len(content) < 20:
            continue
        if role == "system" and not model:
            m = re.search(r"model[:\s]+(\S+)", content[:500])
            if m:
                model = m.group(1)
        if role == "assistant":
            cleaned = redact(content)
            if re.search(r"(?i)\b(decided|decision|let's|I'll|we should|the fix is|resolved|fixed)\b", cleaned[:300]):
                signals.append({"type": "decision", "source": path.name, "snippet": cleaned[:200].replace("\n", " ").strip(), "model": model})
            if re.search(r"(?i)\b(error|exception|traceback|failed|timeout|401|403|409|429|500)\b", cleaned[:300]):
                signals.append({"type": "error", "source": path.name, "snippet": cleaned[:200].replace("\n", " ").strip(), "model": model})
            for m in re.finditer(r"(?:created|wrote|modified|saved)\s+(?:to\s+)?`?([/~][^\s`]+)`?", cleaned[:1000]):
                signals.append({"type": "file_created", "source": path.name, "path": m.group(1), "model": model})
    return signals


def extract_cron_signals(path: Path) -> list[dict[str, Any]]:
    text = redact(read_text(path, 5000))
    job_name = re.search(r"#\s*Cron Job:\s*(.+?)(?:\n|$)", text)
    job_id = re.search(r"\*\*Job ID:\*\*\s*(\S+)", text)
    status = "error" if re.search(r"(?i)\b(error|exception|traceback|failed|timeout)\b", text) else "silent" if "[SILENT]" in text else "ok"
    summary = ""
    for line in text.splitlines()[8:]:
        stripped = line.strip()
        if len(stripped) > 15 and not stripped.startswith(("#", "**", "---", "http")):
            summary = stripped[:200]
            break
    return [{"type": "cron_run", "source": path.name, "job_name": job_name.group(1).strip() if job_name else path.parent.name, "job_id": job_id.group(1).strip() if job_id else "", "status": status, "summary": summary}]


def extract_log_signals(path: Path) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    text = redact(read_text(path, 15000))
    for line in text.splitlines():
        if re.search(r"(?i)\b(gateway.*restart|starting|application started|press ctrl\+c)\b", line):
            signals.append({"type": "restart", "source": path.name, "snippet": line.strip()[:200]})
            break
    error_lines = [line.strip()[:200] for line in text.splitlines()[-50:] if re.search(r"(?i)\b(error|exception|traceback|failed|timeout|401|403|409|429|500|conflict)\b", line)]
    if error_lines:
        signals.append({"type": "log_error", "source": path.name, "count": len(error_lines), "latest": error_lines[-3:]})
    return signals


def extract_git_signals(config: JournalConfig) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    status = git(config.research_repo, ["status", "--short"])
    if status:
        changed = [line.strip() for line in status.splitlines() if line.strip()]
        signals.append({"type": "git_dirty", "files": changed[:20], "count": len(changed)})
    log = git(config.research_repo, ["log", "--oneline", "--since=midnight", "--no-merges"])
    if log:
        commits = [line.strip() for line in log.splitlines() if line.strip()]
        signals.append({"type": "git_commits", "commits": commits[:10], "count": len(commits)})
    return signals


def extract_all_signals(paths: list[Path], config: JournalConfig) -> list[dict[str, Any]]:
    all_signals: list[dict[str, Any]] = []
    for path in paths:
        rel = str(path)
        if "/sessions/" in rel and path.suffix == ".json":
            all_signals.extend(extract_session_signals(path))
        elif "/cron/output/" in rel:
            all_signals.extend(extract_cron_signals(path))
        elif "/logs/" in rel or path.suffix == ".log":
            all_signals.extend(extract_log_signals(path))
    all_signals.extend(extract_git_signals(config))
    seen = set()
    unique = []
    for signal in all_signals:
        key = (signal["type"], str(signal.get("snippet", ""))[:80], str(signal.get("source", "")))
        if key not in seen:
            seen.add(key)
            unique.append(signal)
    return unique


def extract_golden_thread_summary(config: JournalConfig) -> str:
    if not config.project_registry.exists():
        return "> ⚠️ project-registry.json not found\n"
    try:
        reg = json.loads(config.project_registry.read_text(encoding="utf-8"))
    except Exception:
        return "> ⚠️ project-registry.json unreadable\n"
    lines = ["### 🔗 Golden Thread (project-registry.json)"]
    sync = reg.get("_last_sync", {})
    if sync:
        lines.append(f"- Linear: {sync.get('linear_in_progress', 0)} In Progress, {sync.get('linear_in_review', 0)} In Review, {sync.get('linear_todo', 0)} Todo")
        lines.append(f"- GitHub: {sync.get('github_prs_open', 0)} open PRs, {sync.get('github_issues_open', 0)} issues")
        lines.append("")
    ventures = reg.get("ventures", {})
    standalone = reg.get("standalone_projects", {})
    active = []
    for key, value in {**ventures, **standalone}.items():
        next_action = value.get("next_action", "") if isinstance(value, dict) else ""
        if next_action and "DONE" not in next_action[:30] and "deferred" not in next_action.lower()[:20]:
            active.append((key, value.get("name", key), value.get("project_type", "?"), next_action[:120]))
    if active:
        lines.append("**Active projects:**")
        for _key, name, project_type, next_action in sorted(active[:12]):
            lines.append(f"- **{name}** ({project_type}): {next_action}")
        lines.append("")
    return "\n".join(lines)


def build_compact_markdown(signals: list[dict[str, Any]], now: str, config: JournalConfig) -> str:
    branch = git(config.research_repo, ["branch", "--show-current"]) or "unknown"
    head = git(config.research_repo, ["rev-parse", "--short", "HEAD"]) or "unknown"
    status = git(config.research_repo, ["status", "--short"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        grouped[signal["type"]].append(signal)
    blocks = [f"## {now.split('T')[0]} · Snapshot {now.split('T')[1][:5]} UTC", "", f"`{branch}@{head}` · {len(signals)} events"]
    if status:
        blocks.append(f" · {len([line for line in status.splitlines() if line.strip()])} dirty files")
    blocks += ["", "", extract_golden_thread_summary(config)]
    type_icons = {"decision": "🧠", "error": "❌", "file_created": "📄", "cron_run": "⏱️", "restart": "🔄", "log_error": "⚠️", "git_dirty": "📝", "git_commits": "📦"}
    for event_type, items in sorted(grouped.items()):
        blocks += [f"### {type_icons.get(event_type, '•')} {event_type.replace('_', ' ').title()} ({len(items)})", ""]
        for item in items[:8]:
            if event_type in {"decision", "error", "restart"}:
                blocks.append(f"- {item.get('snippet', '?')[:150]}")
            elif event_type == "file_created":
                blocks.append(f"- `{item.get('path', '?')[:100]}`")
            elif event_type == "cron_run":
                status_icon = "✅" if item.get("status") == "ok" else "❌"
                blocks.append(f"- {status_icon} **{item.get('job_name', '?')}** — {item.get('summary', '')[:120]}")
            elif event_type == "log_error":
                blocks.append(f"- {item.get('count', 0)} errors in `{item.get('source', '?')}`")
            elif event_type == "git_dirty":
                blocks.append(f"- {item.get('count', 0)} files changed")
            elif event_type == "git_commits":
                blocks.extend(f"- {commit}" for commit in item.get("commits", [])[:5])
            else:
                blocks.append(f"- {json.dumps(item)[:150]}")
        blocks.append("")
    blocks += ["---", "*Auto-generated by prismatic-journal-snapshot · Secrets redacted*", ""]
    return "\n".join(blocks)


def _days_ago(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%d")


def update_event_index(signals: list[dict[str, Any]], now: str, config: JournalConfig) -> None:
    index_dir = config.journal_root / ".index"
    index_dir.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    today_events_path = index_dir / f"events-{today}.json"
    today_events = json.loads(today_events_path.read_text()) if today_events_path.exists() else []
    for signal in signals:
        signal["_timestamp"] = now
        today_events.append(signal)
    today_events_path.write_text(json.dumps(today_events, indent=2, default=str), encoding="utf-8")
    index_file = index_dir / "events.json"
    master = json.loads(index_file.read_text()) if index_file.exists() else {}
    tag_counts = master.get("_tag_counts", {})
    for signal in signals:
        tag_counts[signal["type"]] = tag_counts.get(signal["type"], 0) + 1
    master["_updated"] = now
    master["_tag_counts"] = tag_counts
    master["_total_events"] = master.get("_total_events", 0) + len(signals)
    keep_after = _days_ago(90)
    for key in list(master):
        if key.startswith("20") and key <= keep_after:
            del master[key]
    master[today] = master.get(today, 0) + len(signals)
    index_file.write_text(json.dumps(master, indent=2, default=str), encoding="utf-8")


def fingerprint(paths: list[Path], config: JournalConfig) -> str:
    payload = {
        "paths": [f"{path}:{int(path.stat().st_mtime)}:{path.stat().st_size}" for path in paths[:50]],
        "git": git(config.research_repo, ["status", "--short"]),
        "head": git(config.research_repo, ["rev-parse", "--short", "HEAD"]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def run_snapshot(config: JournalConfig | None = None, force: bool = False) -> dict[str, Any]:
    config = config or JournalConfig.from_env()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    since = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    inbox_dir = config.journal_root / "inbox"
    state_dir = config.journal_root / ".state"
    state_file = state_dir / "daily-journal-snapshot.json"
    today_file = inbox_dir / f"{today}.md"
    config.journal_root.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    paths = collect_candidates(config, since)
    current_fp = fingerprint(paths, config)
    previous_fp = ""
    try:
        previous_fp = str(json.loads(state_file.read_text(encoding="utf-8")).get("fingerprint", ""))
    except Exception:
        pass
    if not force and previous_fp == current_fp and today_file.exists():
        return {"changed": False, "signals": 0, "today_file": str(today_file)}
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals = extract_all_signals(paths, config)
    if not force and not signals and not any(path.stat().st_mtime >= since for path in paths):
        return {"changed": False, "signals": 0, "today_file": str(today_file)}
    md = build_compact_markdown(signals, now, config)
    existing = today_file.read_text(encoding="utf-8", errors="ignore") if today_file.exists() else ""
    merged = existing.rstrip() + "\n\n" + md if existing else md
    today_file.write_text(merged, encoding="utf-8")
    (config.journal_root / "latest-inbox.md").write_text(merged, encoding="utf-8")
    update_event_index(signals, now, config)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"fingerprint": current_fp, "updated_at": now}), encoding="utf-8")
    return {"changed": True, "signals": len(signals), "today_file": str(today_file), "lines": len([line for line in md.splitlines() if line.strip()])}


def import_plan_ready(period: str = "initial", config: JournalConfig | None = None, execute: bool = False) -> dict[str, Any]:
    config = config or JournalConfig.from_env()
    plan = config.report_root / period / "linear-import-plan.json"
    synthesis = config.report_root / period / "fred-synthesis.md"
    if not plan.exists() or not synthesis.exists():
        return {"ready": False, "reason": "synthesis/import plan missing", "plan": str(plan), "synthesis": str(synthesis), "exit_code": 2}
    body = gql("{ viewer { name } }", config=config)
    errors = body.get("errors") or []
    if errors:
        msg = json.dumps(errors)
        if "Rate limit exceeded" in msg or "RATELIMITED" in msg:
            return {"ready": False, "reason": "Linear API rate-limited", "plan": str(plan), "synthesis": str(synthesis), "exit_code": 0}
        return {"ready": False, "reason": "Linear API error", "errors": errors, "plan": str(plan), "synthesis": str(synthesis), "exit_code": 2}
    items = json.loads(plan.read_text()).get("items", [])
    result = {"ready": True, "items": len(items), "plan": str(plan), "synthesis": str(synthesis), "execute": execute, "exit_code": 0}
    if execute:
        result["note"] = "Execution is intentionally conservative in Phase 1; dedupe/create mutations remain harness-orchestrated."
    return result


def extract_file_paths(text: str) -> list[str]:
    paths = []
    for match in FILE_PATH_PATTERN.finditer(text):
        path = match.group(1).rstrip(".,`'")
        if not path.startswith("http") and len(path) > 4:
            paths.append(path)
    return sorted(set(paths))


def validate_artifacts(paths: list[str], require_non_empty: bool = True) -> dict[str, Any]:
    found = []
    missing = []
    empty = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            missing.append(raw)
        elif require_non_empty and path.is_file() and path.stat().st_size == 0:
            empty.append(raw)
        else:
            found.append(raw)
    passed = not missing and not empty
    return {"passed": passed, "found": found, "missing": missing, "empty": empty}


def validate_agent_output(issue_identifier: str | None = None, log_path: str | None = None, artifact: list[str] | None = None, text: str | None = None) -> dict[str, Any]:
    artifacts = artifact or []
    if text:
        artifacts.extend(extract_file_paths(text))
    transcript = ""
    resolved_log = Path(log_path) if log_path else Path(f"/tmp/antigravity_{issue_identifier}.log") if issue_identifier else None
    log_exists = bool(resolved_log and resolved_log.exists())
    transcript_size = 0
    error_markers: list[str] = []
    if resolved_log and resolved_log.exists():
        transcript_size = resolved_log.stat().st_size
        transcript = resolved_log.read_text(errors="replace")
        for pattern, label in ERROR_PATTERNS:
            if re.search(pattern, transcript, re.IGNORECASE):
                error_markers.append(label)
    artifact_result = validate_artifacts(sorted(set(artifacts))) if artifacts else {"passed": True, "found": [], "missing": [], "empty": []}
    passed = log_exists and transcript_size >= 100 and not error_markers and artifact_result["passed"]
    return {
        "issue_identifier": issue_identifier,
        "passed": passed,
        "log_path": str(resolved_log) if resolved_log else None,
        "checks": {
            "log_file_exists": log_exists,
            "log_file_non_empty": transcript_size >= 100,
            "no_error_markers": not error_markers,
            "artifacts_exist": artifact_result["passed"],
        },
        "transcript_size_bytes": transcript_size,
        "error_markers": error_markers,
        "artifacts": artifact_result,
    }


def cli_journal(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic-journal")
    sub = parser.add_subparsers(dest="cmd")
    inv = sub.add_parser("inventory", help="Build bounded source inventory")
    inv.add_argument("--period", default=period_default())
    monthly = sub.add_parser("monthly", help="Create monthly control/audit Linear issues")
    monthly.add_argument("--period", default=period_default())
    monthly.add_argument("--inventory-only", action="store_true", help="Compatibility mode for old monthly_journal_continuity_audit.py")
    args = parser.parse_args(argv)
    if args.cmd in {None, "inventory"}:
        json_path, md_path = build_inventory(args.period)
        print(f"inventory_json={json_path}\ninventory_md={md_path}")
        return 0
    if args.cmd == "monthly":
        if args.inventory_only:
            json_path, md_path = build_inventory(args.period)
            print(f"inventory_json={json_path}\ninventory_md={md_path}")
        else:
            print(json.dumps(create_monthly(args.period), indent=2))
        return 0
    parser.error("unknown command")
    return 2


def cli_journal_snapshot(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic-journal-snapshot")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    result = run_snapshot(force=args.force)
    print(json.dumps(result, indent=2))
    return 0


def cli_linear_import(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic-linear-import")
    parser.add_argument("--period", default="initial")
    parser.add_argument("--execute", action="store_true", help="Reserved for Phase 2; Phase 1 remains readiness-only")
    args = parser.parse_args(argv)
    result = import_plan_ready(args.period, execute=args.execute)
    print(json.dumps(result, indent=2))
    return int(result.get("exit_code", 0))


def cli_second_witness(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic-second-witness")
    parser.add_argument("--issue", help="Issue identifier, e.g. GRO-1954")
    parser.add_argument("--log-path")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--text-file", help="Optional text/comment file to scan for artifact paths")
    args = parser.parse_args(argv)
    text = Path(args.text_file).read_text(errors="replace") if args.text_file else None
    result = validate_agent_output(issue_identifier=args.issue, log_path=args.log_path, artifact=args.artifact, text=text)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(cli_journal())
