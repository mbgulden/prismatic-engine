from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

CRON_STATE_ACTIVE = "active"
CRON_STATE_PAUSED = "paused"
CRON_STATE_DEACTIVATED = "deactivated"
CRON_STATE_DELETED = "deleted"
QUEUE_STATES = {CRON_STATE_ACTIVE, CRON_STATE_PAUSED}
NON_QUEUE_STATES = {CRON_STATE_DEACTIVATED, CRON_STATE_DELETED}

Action = Literal["pause", "resume", "deactivate", "activate", "delete", "run", "recover"]


def validate_cron_dag_cycles(crons: Sequence[NativeCron]) -> list[str]:
    """Detect circular dependencies in depends_on DAG using Depth-First Search."""
    graph = {c.id: set(c.depends_on) for c in crons}
    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[str] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        for dep in graph.get(node, []):
            if dep not in visited:
                dfs(dep, path + [dep])
            elif dep in rec_stack:
                cycle_str = " -> ".join(path + [dep])
                cycles.append(cycle_str)
        rec_stack.remove(node)

    for cron_id in graph:
        if cron_id not in visited:
            dfs(cron_id, [cron_id])

    return cycles


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", repo_root() / "prismatic_state")).expanduser()


def default_cron_store_path() -> Path:
    return Path(os.environ.get("PRISMATIC_NATIVE_CRON_STORE", default_state_dir() / "native_crons.json")).expanduser()


@dataclass
class NativeCron:
    id: str
    name: str
    schedule: str
    command: list[str]
    cwd: str = "."
    group: str = "general"
    description: str = ""
    state: str = CRON_STATE_ACTIVE
    queue_state: str = "queued"
    output_policy: str = "silent_on_success"
    env: dict[str, str] = field(default_factory=dict)
    portable: bool = True
    source: str = "repo"
    tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    last_run_at: str | None = None
    last_status: str | None = None
    last_exit_code: int | None = None
    last_stdout: str | None = None
    last_stderr: str | None = None
    deactivated_at: str | None = None
    deleted_at: str | None = None
    paused_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        self.sync_queue_state()

    def sync_queue_state(self) -> None:
        self.queue_state = "queued" if self.state in QUEUE_STATES else "out_of_queue"

    @property
    def enabled(self) -> bool:
        return self.state == CRON_STATE_ACTIVE

    def to_dict(self) -> dict[str, Any]:
        self.sync_queue_state()
        data = asdict(self)
        data["enabled"] = self.enabled
        data["display_command"] = " ".join(shlex.quote(part) for part in self.command)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NativeCron":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


SEO_NATIVE_CRONS: list[NativeCron] = [
    NativeCron(
        id="seo.ubersuggest-token-refresh",
        name="SEO — Ubersuggest token refresh",
        schedule="0 3 * * *",
        command=["python3", "scripts/pwp", "credentials", "refresh", "ubersuggest"],
        cwd=".",
        group="seo",
        description="Rotate the Ubersuggest MCP OAuth access+refresh token through PWP before SEO jobs run.",
        tags=["seo", "ubersuggest", "oauth", "pwp"],
    ),
    NativeCron(
        id="seo.aot-weekly-rankings",
        name="SEO — Managed site weekly rankings change report",
        schedule="0 4 * * 1",
        command=["python3", "scripts/seo/aot_kpi_tracker.py"],
        cwd=".",
        group="seo",
        description="Pull weekly target domain + competitor keyword snapshots and preserve the last good baseline.",
        tags=["seo", "site-rankings", "rankings", "ubersuggest"],
        depends_on=["seo.ubersuggest-token-refresh"],
    ),
    NativeCron(
        id="seo.aot-competitor-velocity",
        name="SEO — Competitor content velocity",
        schedule="0 6 * * 0",
        command=["python3", "scripts/seo/competitor_velocity.py"],
        cwd=".",
        group="seo",
        description="Monitor competitor top-page movement and alert when new content enters monitored territory.",
        tags=["seo", "competitor-monitoring", "ubersuggest"],
        depends_on=["seo.ubersuggest-token-refresh"],
    ),
    NativeCron(
        id="seo.aot-full-sweep",
        name="SEO — Full competitive sweep",
        schedule="manual",
        command=["python3", "scripts/seo/seo_full_sweep.py"],
        cwd=".",
        group="seo",
        description="On-demand seven-phase competitive SEO sweep for monitored domain.",
        state=CRON_STATE_DEACTIVATED,
        tags=["seo", "competitive-audit", "manual"],
        depends_on=["seo.ubersuggest-token-refresh"],
    ),
    NativeCron(
        id="seo.managed-sites-setup-audit",
        name="SEO — Managed sites GSC/GA setup audit",
        schedule="0 5 * * 0",
        command=["python3", "scripts/seo/managed_site_setup_audit.py"],
        cwd=".",
        group="seo",
        description="Audit every managed SEO site for GSC property access, sitemap reachability, GTM/dataLayer installation, GA4 stream/property configuration, and setup blockers.",
        tags=["seo", "managed-sites", "gsc", "ga4", "gtm", "datalayer", "setup"],
    ),
    NativeCron(
        id="seo.managed-sites-ga4-insights",
        name="SEO — Managed sites GA4 conversion/revenue insights",
        schedule="0 6 * * *",
        command=["python3", "scripts/seo/ga4_insights.py"],
        cwd=".",
        group="seo",
        description="Pull GA4 sessions, conversions, conversion rates, booking/ecommerce revenue, and page economics for configured managed sites.",
        tags=["seo", "managed-sites", "ga4", "analytics", "conversion", "revenue"],
        depends_on=["seo.managed-sites-setup-audit"],
    ),
    NativeCron(
        id="seo.gsc-query-page-export",
        name="SEO — GSC own-site query/page export",
        schedule="30 5 * * *",
        command=["python3", "scripts/seo/gsc_query_page_export.py"],
        cwd=".",
        group="seo",
        description="Export Google Search Console query/page rows for monitored domain as the own-site source of truth.",
        tags=["seo", "gsc", "own-site-truth"],
    ),
    NativeCron(
        id="seo.aot-counter-content-briefs",
        name="SEO — Counter-content brief generator",
        schedule="30 7 * * 0",
        command=["python3", "scripts/seo/gsc_ubersuggest_countercontent.py"],
        cwd=".",
        group="seo",
        description="Pair weekly competitor velocity data with GSC own-site evidence and produce counter-content briefs.",
        tags=["seo", "gsc", "ubersuggest", "content-briefs"],
        depends_on=["seo.gsc-query-page-export", "seo.aot-competitor-velocity"],
    ),
    NativeCron(
        id="seo.aot-internal-link-orphan-audit",
        name="SEO — Internal link/orphan audit",
        schedule="15 8 * * 1",
        command=["python3", "scripts/seo/internal_link_orphan_audit.py"],
        cwd=".",
        group="seo",
        description="Crawl the static site export to build a link graph, find orphan pages, and surface internal-link quality issues.",
        tags=["seo", "internal-links", "orphan-pages"],
    ),
    NativeCron(
        id="seo.aot-structured-data-drift-audit",
        name="SEO — Structured data drift audit",
        schedule="45 8 * * 1",
        command=["python3", "scripts/seo/structured_data_drift_audit.py"],
        cwd=".",
        group="seo",
        description="Parse static HTML JSON-LD blocks, count schema types, and flag parse/schema drift.",
        tags=["seo", "schema", "structured-data"],
    ),
    NativeCron(
        id="seo.aot-sitemap-gsc-verification",
        name="SEO — Sitemap/GSC verification",
        schedule="manual",
        command=["python3", "scripts/seo/sitemap_gsc_verification.py"],
        cwd=".",
        group="seo",
        description="Verify live sitemap.xml against Google Search Console sitemap API. Manual/post-deploy because submission is gated by Google auth.",
        state=CRON_STATE_DEACTIVATED,
        tags=["seo", "gsc", "sitemap", "manual", "post-deploy"],
        depends_on=["seo.gsc-query-page-export"],
    ),
    NativeCron(
        id="seo.aot-lighthouse-seo-a11y-monitor",
        name="SEO — Lighthouse SEO/A11y monitor",
        schedule="30 9 * * 1",
        command=["python3", "scripts/seo/lighthouse_seo_a11y_monitor.py"],
        cwd=".",
        group="seo",
        description="Run rendered Lighthouse SEO/A11y/Best-Practices checks on priority routes, with static fallback artifacts when Lighthouse is unavailable.",
        tags=["seo", "lighthouse", "accessibility", "post-deploy"],
    ),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


class NativeCronStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cron_store_path()

    def ensure_seeded(self) -> None:
        if not self.path.exists():
            self.save(SEO_NATIVE_CRONS)
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            existing = [NativeCron.from_dict(item) for item in raw.get("crons", [])]
        except Exception:
            return
        existing_by_id = {cron.id: cron for cron in existing}
        merged: list[NativeCron] = []
        changed = False
        runtime_fields = {
            "state", "queue_state", "last_run_at", "last_status", "last_exit_code", "last_stdout", "last_stderr",
            "deactivated_at", "deleted_at", "paused_at", "updated_at",
        }
        for default in SEO_NATIVE_CRONS:
            existing_cron = existing_by_id.pop(default.id, None)
            if existing_cron is None:
                merged.append(default)
                changed = True
                continue
            refreshed = NativeCron.from_dict({
                **default.to_dict(),
                **{field: getattr(existing_cron, field) for field in runtime_fields},
            })
            if refreshed.to_dict() != existing_cron.to_dict():
                changed = True
            merged.append(refreshed)
        if existing_by_id:
            merged.extend(existing_by_id.values())
        if changed:
            self.save(merged)

    def load(self) -> list[NativeCron]:
        self.ensure_seeded()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        raw = data.get("crons", data if isinstance(data, list) else [])
        return [NativeCron.from_dict(item) for item in raw if isinstance(item, dict)]

    def save(self, crons: Sequence[NativeCron]) -> None:
        for cron in crons:
            cron.sync_queue_state()
        _atomic_write_json(self.path, {"version": 1, "crons": [cron.to_dict() for cron in crons]})

    def get(self, cron_id: str) -> NativeCron:
        for cron in self.load():
            if cron.id == cron_id:
                return cron
        raise KeyError(cron_id)

    def mutate(self, cron_id: str, action: Action) -> dict[str, Any]:
        crons = self.load()
        for index, cron in enumerate(crons):
            if cron.id != cron_id:
                continue
            if action == "pause":
                if cron.state == CRON_STATE_ACTIVE:
                    cron.state = CRON_STATE_PAUSED
                    cron.paused_at = _now()
            elif action == "resume":
                if cron.state == CRON_STATE_PAUSED:
                    cron.state = CRON_STATE_ACTIVE
                    cron.paused_at = None
            elif action == "deactivate":
                if cron.state != CRON_STATE_DELETED:
                    cron.state = CRON_STATE_DEACTIVATED
                    cron.deactivated_at = _now()
            elif action == "activate":
                if cron.state == CRON_STATE_DEACTIVATED:
                    cron.state = CRON_STATE_ACTIVE
                    cron.deactivated_at = None
            elif action == "delete":
                cron.state = CRON_STATE_DELETED
                cron.deleted_at = _now()
            elif action == "run":
                result = run_native_cron(cron)
                cron.last_run_at = result["ran_at"]
                cron.last_status = result["status"]
                cron.last_exit_code = result["exit_code"]
                cron.last_stdout = result["stdout"][-4000:]
                cron.last_stderr = result["stderr"][-4000:]
                crons[index] = cron
                self.save(crons)
                return {"success": result["status"] == "success", "cron": cron.to_dict(), "run": result}
            elif action == "recover":
                # Replay up to 3 missed executions
                replays = []
                for _ in range(3):
                    res = run_native_cron(cron)
                    replays.append(res)
                    if res["status"] != "success":
                        break
                last_res = replays[-1]
                cron.last_run_at = last_res["ran_at"]
                cron.last_status = last_res["status"]
                cron.last_exit_code = last_res["exit_code"]
                cron.last_stdout = last_res["stdout"][-4000:]
                cron.last_stderr = last_res["stderr"][-4000:]
                crons[index] = cron
                self.save(crons)
                return {"success": last_res["status"] == "success", "cron": cron.to_dict(), "replays_count": len(replays), "replays": replays}
            else:
                raise ValueError(f"Unsupported native cron action: {action}")
            cron.updated_at = _now()
            cron.sync_queue_state()
            crons[index] = cron
            self.save(crons)
            return {"success": True, "cron": cron.to_dict(), "action": action}
        raise KeyError(cron_id)


def list_native_crons(include_deleted: bool = False, store: NativeCronStore | None = None) -> list[dict[str, Any]]:
    crons = (store or NativeCronStore()).load()
    if not include_deleted:
        crons = [cron for cron in crons if cron.state != CRON_STATE_DELETED]
    return [cron.to_dict() for cron in crons]


def run_native_cron(cron: NativeCron, timeout: int = 600) -> dict[str, Any]:
    if cron.state == CRON_STATE_DELETED:
        raise ValueError(f"Cannot run deleted cron {cron.id}")
    env = os.environ.copy()
    env.update(cron.env)
    cwd = repo_root() / cron.cwd if not Path(cron.cwd).is_absolute() else Path(cron.cwd)
    ran_at = _now()
    completed = subprocess.run(
        cron.command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    return {
        "ran_at": ran_at,
        "status": "success" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def mutate_native_cron(cron_id: str, action: Action, store: NativeCronStore | None = None) -> dict[str, Any]:
    return (store or NativeCronStore()).mutate(cron_id, action)


def export_system_crontab_lines(store: NativeCronStore | None = None) -> list[str]:
    lines: list[str] = []
    for cron in (store or NativeCronStore()).load():
        if cron.state != CRON_STATE_ACTIVE or cron.schedule == "manual":
            continue
        command = " ".join(shlex.quote(part) for part in cron.command)
        cwd = repo_root() / cron.cwd if not Path(cron.cwd).is_absolute() else Path(cron.cwd)
        lines.append(f"{cron.schedule} cd {shlex.quote(str(cwd))} && {command}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Prismatic native cron registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    export = sub.add_parser("export-crontab")
    export.add_argument("--include-header", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("cron_id")
    mutate = sub.add_parser("mutate")
    mutate.add_argument("cron_id")
    mutate.add_argument("action", choices=["pause", "resume", "deactivate", "activate", "delete"])
    args = parser.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(list_native_crons(include_deleted=True), indent=2, sort_keys=True))
        return 0
    if args.cmd == "export-crontab":
        lines = export_system_crontab_lines()
        if args.include_header:
            print("# Generated by prismatic.native_crons")
        print("\n".join(lines))
        return 0
    if args.cmd == "run":
        result = mutate_native_cron(args.cron_id, "run")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("success") else 1
    if args.cmd == "mutate":
        print(json.dumps(mutate_native_cron(args.cron_id, args.action), indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
