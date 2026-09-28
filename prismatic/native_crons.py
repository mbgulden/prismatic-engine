from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

logger = logging.getLogger(__name__)

# Truncation policy for recorded stdout/stderr (shared by the `run` action
# and the run-recorder wrapper).
OUTPUT_TRUNCATE_CHARS = 4000

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
    last_duration_s: float | None = None
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


# ── WI-5: engine-health seed set for new users ─────────────────────────
# DECISION-2 (cron-overhaul plan): SEO + engine-health seeds stay merged —
# fresh stores get both groups; existing stores merge the new engine-health
# seeds while keeping their own runtime fields (state, last_*).
# Entry points verified on main: scripts/lane_visibility_probe.py (WI-6,
# no required args), scripts/receipt_coverage_watch.py (WI-7, no required
# args), `prismatic doctor` (prismatic/cli/__init__.py), and
# scripts/silent_cron_detector.py (all-optional argparse CLI).

ENGINE_HEALTH_CRONS: list[NativeCron] = [
    NativeCron(
        id="engine.lane-visibility-probe",
        name="Engine health — Dispatcher lane visibility probe",
        schedule="23 * * * *",
        command=["python3", "scripts/lane_visibility_probe.py"],
        cwd=".",
        group="engine-health",
        description=(
            "Read-only hourly check that every dispatcher lane can see "
            "canonically labeled work — catches blind-lane logic bugs (the "
            "July blind lane) within an hour instead of months."
        ),
        tags=["engine-health", "dispatcher", "lanes", "read-only"],
    ),
    NativeCron(
        id="engine.receipt-coverage-watch",
        name="Engine health — Merge receipt coverage watch",
        schedule="30 6 * * *",
        command=["python3", "scripts/receipt_coverage_watch.py"],
        cwd=".",
        group="engine-health",
        description=(
            "Read-only daily watch for merged PRs lacking signed "
            "merge-executor receipts (flags the known GitHub-UI-merge gap)."
        ),
        tags=["engine-health", "merge-receipts", "coverage", "read-only"],
    ),
    NativeCron(
        id="engine.doctor",
        name="Engine health — Weekly doctor",
        schedule="0 7 * * 1",
        command=["prismatic", "doctor"],
        cwd=".",
        group="engine-health",
        description="Weekly environment diagnostics via `prismatic doctor`.",
        tags=["engine-health", "diagnostics"],
    ),
    NativeCron(
        id="engine.silent-cron-detector",
        name="Engine health — Silent cron detector",
        schedule="45 6 * * *",
        command=[
            "python3",
            "scripts/silent_cron_detector.py",
            "--dry-run",
            "--no-telegram",
        ],
        cwd=".",
        group="engine-health",
        description=(
            "Daily read-only sweep for silently failing or stale crons. "
            "Seeded dry-run + no-telegram: reporting only, Linear/Telegram "
            "alerting stays opt-in."
        ),
        tags=["engine-health", "silent-failures", "read-only"],
    ),
]

#: Seed groups merged by ``NativeCronStore.ensure_seeded()``, in order.
#: Resolved through a function (not a module constant) so the merge always
#: reads the *current* module attributes — tests may monkeypatch the seed
#: lists, and the merge must honor that.
def _seed_cron_groups() -> tuple[tuple[str, list[NativeCron]], ...]:
    return (
        ("seo", SEO_NATIVE_CRONS),
        ("engine-health", ENGINE_HEALTH_CRONS),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── WI-3: create path + register_native_cron ──────────────────────────

_CRON_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DuplicateCronIdError(ValueError):
    """Raised when creating a native cron whose id already exists."""


def validate_cron_schedule(schedule: str) -> None:
    """Validate a cron schedule string.

    Accepts the special value ``"manual"`` (on-demand only) or a 5-field cron
    expression. Raises ValueError otherwise.
    """
    if not isinstance(schedule, str) or not schedule.strip():
        raise ValueError(
            f"Invalid cron schedule {schedule!r}: expected a 5-field cron "
            "expression (minute hour day month weekday) or 'manual'"
        )
    if schedule.strip() == "manual":
        return
    fields = schedule.split()
    if len(fields) != 5:
        raise ValueError(
            f"Invalid cron schedule {schedule!r}: expected 5 fields "
            "(minute hour day month weekday) or 'manual'"
        )


def create_native_cron(
    data: dict[str, Any] | NativeCron, store: NativeCronStore | None = None
) -> dict[str, Any]:
    """Create a native cron in the store. Returns the stored cron as a dict.

    Validates the id (required, unique, safe characters), name, schedule
    (5-field or 'manual') and command. Raises DuplicateCronIdError on id
    collision, ValueError on any other validation failure.
    """
    st = store or NativeCronStore()
    if isinstance(data, NativeCron):
        cron = data
    elif isinstance(data, dict):
        try:
            cron = NativeCron.from_dict(data)
        except TypeError as exc:
            raise ValueError(f"Invalid native cron definition: {exc}") from exc
    else:
        raise ValueError(
            f"Invalid native cron definition: expected dict or NativeCron, "
            f"got {type(data).__name__}"
        )

    cron_id = (cron.id or "").strip()
    if not _CRON_ID_RE.match(cron_id):
        raise ValueError(
            f"Invalid native cron id {cron.id!r}: use letters, digits, '.', "
            "'_' or '-', max 128 chars"
        )
    cron.id = cron_id
    if not (cron.name or "").strip():
        raise ValueError("Native cron 'name' is required")
    if (
        not isinstance(cron.command, list)
        or not cron.command
        or not all(isinstance(part, str) for part in cron.command)
    ):
        raise ValueError("Native cron 'command' must be a non-empty list of strings")
    validate_cron_schedule(cron.schedule)
    cron.schedule = cron.schedule.strip()

    existing = st.load()
    if any(c.id == cron.id for c in existing):
        raise DuplicateCronIdError(f"Native cron id already exists: {cron.id}")
    cron.updated_at = _now()
    cron.sync_queue_state()
    existing.append(cron)
    st.save(existing)
    # A new schedulable cron must reach the system crontab (best-effort).
    refresh_system_crontab(st)
    return cron.to_dict()


def register_native_cron(
    cron: NativeCron | dict[str, Any], store: NativeCronStore | None = None
) -> dict[str, Any]:
    """Register a native cron via the create path. Returns the stored cron dict.

    This is the function ``prismatic/gateway/routes/pwp.py`` imports; it used
    to raise ImportError because the name did not exist.
    """
    return create_native_cron(cron, store=store)


# ── WI-2: managed crontab block (extracted from scripts/install_native_crons.py)

CRONTAB_BLOCK_BEGIN = "# BEGIN PRISMATIC_NATIVE_CRONS"
CRONTAB_BLOCK_END = "# END PRISMATIC_NATIVE_CRONS"
CRONTAB_BLOCK_NOTICE = (
    "# Generated by prismatic.native_crons. Edit PE native cron state, not this block."
)


def render_crontab_block(store: NativeCronStore | None = None) -> str:
    lines = [CRONTAB_BLOCK_BEGIN, CRONTAB_BLOCK_NOTICE]
    lines.extend(export_system_crontab_lines(store))
    lines.append(CRONTAB_BLOCK_END)
    return "\n".join(lines) + "\n"


def replace_crontab_managed_block(existing: str, block: str) -> str:
    """Replace (or append) the PRISMATIC_NATIVE_CRONS managed block in a crontab."""
    if CRONTAB_BLOCK_BEGIN in existing and CRONTAB_BLOCK_END in existing:
        before, rest = existing.split(CRONTAB_BLOCK_BEGIN, 1)
        _old, after = rest.split(CRONTAB_BLOCK_END, 1)
        result = before.rstrip() + "\n\n" + block + after.lstrip("\n")
    else:
        result = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    return result.rstrip() + "\n"


def read_user_crontab() -> str | None:
    """Return the current user crontab, or None when it is unavailable/unreadable."""
    if shutil.which("crontab") is None:
        return None
    completed = subprocess.run(
        ["crontab", "-l"], text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def write_user_crontab(content: str) -> None:
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def refresh_system_crontab(store: NativeCronStore | None = None) -> bool:
    """Best-effort reinstall of the PRISMATIC_NATIVE_CRONS managed block.

    Never raises: returns True when the block was rewritten, False when there
    was nothing to do or the reinstall failed (both are logged).
    """
    try:
        existing = read_user_crontab()
    except Exception as exc:
        logger.warning("native-crons: skipping crontab re-export (read failed: %s)", exc)
        return False
    if existing is None:
        logger.info(
            "native-crons: no readable user crontab; skipping managed-block re-export"
        )
        return False
    try:
        write_user_crontab(
            replace_crontab_managed_block(existing, render_crontab_block(store))
        )
    except Exception as exc:
        logger.warning("native-crons: crontab re-export failed: %s", exc)
        return False
    return True


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
            self.save([cron for _group_name, group in _seed_cron_groups() for cron in group])
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
            "last_duration_s",
            "deactivated_at", "deleted_at", "paused_at", "updated_at",
        }
        for _group_name, seed_group in _seed_cron_groups():
            for default in seed_group:
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
                cron.last_stdout = result["stdout"][-OUTPUT_TRUNCATE_CHARS:]
                cron.last_stderr = result["stderr"][-OUTPUT_TRUNCATE_CHARS:]
                cron.last_duration_s = result.get("duration_s")
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
                cron.last_stdout = last_res["stdout"][-OUTPUT_TRUNCATE_CHARS:]
                cron.last_stderr = last_res["stderr"][-OUTPUT_TRUNCATE_CHARS:]
                cron.last_duration_s = last_res.get("duration_s")
                crons[index] = cron
                self.save(crons)
                return {"success": last_res["status"] == "success", "cron": cron.to_dict(), "replays_count": len(replays), "replays": replays}
            else:
                raise ValueError(f"Unsupported native cron action: {action}")
            cron.updated_at = _now()
            cron.sync_queue_state()
            crons[index] = cron
            self.save(crons)
            if action in {"pause", "resume", "deactivate", "activate", "delete"}:
                # WI-2: the set of exported lines changed — reinstall the
                # managed crontab block best-effort. This must never fail the
                # mutation itself (dashboard actions must not 500).
                try:
                    refresh_system_crontab(self)
                except Exception as exc:
                    logger.warning(
                        "native-crons: post-mutate crontab refresh failed: %s", exc
                    )
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
    started = time.monotonic()
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
        "duration_s": time.monotonic() - started,
    }


def mutate_native_cron(cron_id: str, action: Action, store: NativeCronStore | None = None) -> dict[str, Any]:
    return (store or NativeCronStore()).mutate(cron_id, action)


def _wrapper_prefix() -> str:
    """Prefix for exported crontab lines: cd to the repo root and invoke the recorder."""
    repo = repo_root()
    python = sys.executable or "python3"
    return (
        f"cd {shlex.quote(str(repo))} && "
        f"{shlex.quote(python)} -m prismatic.native_crons record-run"
    )


def _wrapped_crontab_command(cron: NativeCron) -> str:
    """Build the crontab command field: run-recorder wrapper + job tail.

    The tail preserves the exact pre-wrapper execution semantics
    (``cd {cwd} && {command}``). It is passed as a single shell-quoted word so
    the wrapper receives it verbatim — re-quoting argv tokens would corrupt
    shell operators (``&&``) and re-splitting would corrupt quoted arguments.
    The wrapper executes the tail, records the outcome (exit code / stdout /
    stderr / duration) against the cron id, and exits with the job's exit code.
    """
    cwd = repo_root() / cron.cwd if not Path(cron.cwd).is_absolute() else Path(cron.cwd)
    job = f"cd {shlex.quote(str(cwd))} && {' '.join(shlex.quote(part) for part in cron.command)}"
    return f"{_wrapper_prefix()} {shlex.quote(cron.id)} -- {shlex.quote(job)}"


def export_system_crontab_lines(store: NativeCronStore | None = None) -> list[str]:
    lines: list[str] = []
    for cron in (store or NativeCronStore()).load():
        if cron.state != CRON_STATE_ACTIVE or cron.schedule == "manual":
            continue
        lines.append(f"{cron.schedule} {_wrapped_crontab_command(cron)}")
    return lines


# ── WI-9: signed run receipts (cheap win extracted from the 5k system) ──
# The recorder additionally appends a CronRunReceipt-shaped JSONL entry per
# run, shaped by prismatic/cron_receipts/cron-run-receipt-v1.schema.json
# (reused in place via the packaged dataclass — never copied).
#
# v1 has NO signature infrastructure: signing_key_id is "unsigned-local"
# and the signature is the explicit "unsigned" placeholder. A truly empty
# signature would not validate (the schema requires signature minLength 1).
# The shape is what matters in v1; signing can come later if Michael wants it.
# No authority adoption: cron_runner.py / cron_authority.py are untouched.

CRON_RECEIPT_SIGNING_KEY_ID = "unsigned-local"
CRON_RECEIPT_UNSIGNED_SIGNATURE = "unsigned"


def default_cron_receipt_log_path() -> Path:
    """JSONL file run receipts are appended to (env-overridable for tests)."""
    return Path(
        os.environ.get(
            "PRISMATIC_CRON_RECEIPT_LOG",
            str(Path.home() / ".prismatic" / "audit" / "cron-run-receipts.jsonl"),
        )
    ).expanduser()


def _utc_z(dt: datetime) -> str:
    """RFC 3339 UTC timestamp with Z suffix, as the receipt schema requires."""
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _runner_release_digest() -> str:
    """Best-effort 64-hex digest identifying the recorder build.

    The schema requires runner_release_digest as a SHA-256. There is no
    release-artifact pipeline for native crons in v1, so the digest is taken
    over this module's own bytes (stable per release); a fixed constant is
    the fallback if the file cannot be read.
    """
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return hashlib.sha256(
            b"prismatic.native_crons:record-run:unsigned-local"
        ).hexdigest()


def build_cron_run_receipt(
    *,
    cron_id: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    """Build a CronRunReceipt v1 dict for a completed recorder run.

    The receipt reuses the packaged ``CronRunReceipt`` dataclass in place
    (schema validation happens in the constructor and via ``validate()``).
    Raises on invalid input — callers treat receipt writing as best-effort.
    """
    from prismatic.cron_receipts.schema import CronRunReceipt

    evidence = f"{stdout or ''}\n{stderr or ''}"
    receipt = CronRunReceipt(
        receipt_id=f"cronrun-{uuid.uuid4().hex}",
        cron_id=cron_id,
        execution_id=f"exec-{uuid.uuid4().hex}",
        outcome="succeeded" if exit_code == 0 else "failed",
        attempt=1,
        runner_id=socket.gethostname() or "unknown",
        runner_release_digest=_runner_release_digest(),
        started_at=_utc_z(started_at),
        finished_at=_utc_z(finished_at),
        error_classification=None if exit_code == 0 else f"exit_code:{exit_code}",
        evidence_digest=hashlib.sha256(
            evidence.encode("utf-8", errors="replace")
        ).hexdigest(),
        signing_key_id=CRON_RECEIPT_SIGNING_KEY_ID,
        signature=CRON_RECEIPT_UNSIGNED_SIGNATURE,
    )
    receipt.validate()
    return receipt.to_dict()


def append_cron_run_receipt(receipt: dict[str, Any], path: Path | None = None) -> bool:
    """Append one receipt JSON object to the JSONL log.

    Best-effort: returns True on success, logs to stderr and returns False
    on any failure. Never raises.
    """
    target = Path(path) if path is not None else default_cron_receipt_log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, separators=(",", ":")) + "\n")
        return True
    except OSError as exc:
        print(f"record-run: failed to append run receipt: {exc}", file=sys.stderr)
        return False


def record_cron_run(
    cron_id: str, shell_command: str, store: NativeCronStore | None = None
) -> int:
    """Execute *shell_command*, record the outcome on the cron registry.

    Returns the job's exit code. Recording is best-effort: an unknown cron id
    or a store failure is logged to stderr and never masks the job's status.

    Note: the write goes through a direct load/save — run records must not
    trigger a crontab re-export (``mutate()`` does that for state changes).
    A signed CronRunReceipt (WI-9) is additionally appended to the receipt
    JSONL log, best-effort and independent of the store writeback.
    """
    started_dt = datetime.now(timezone.utc)
    ran_at = _now()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            shell_command, shell=True, text=True, capture_output=True, check=False
        )
        exit_code = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except Exception as exc:  # e.g. the shell itself could not start
        exit_code, stdout, stderr = 127, "", f"record-run failed to launch job: {exc}"
    duration_s = time.monotonic() - started
    finished_dt = datetime.now(timezone.utc)
    status = "success" if exit_code == 0 else "failed"
    try:
        st = store or NativeCronStore()
        crons = st.load()
        for index, cron in enumerate(crons):
            if cron.id != cron_id:
                continue
            cron.last_run_at = ran_at
            cron.last_status = status
            cron.last_exit_code = exit_code
            cron.last_stdout = (stdout or "")[-OUTPUT_TRUNCATE_CHARS:]
            cron.last_stderr = (stderr or "")[-OUTPUT_TRUNCATE_CHARS:]
            cron.last_duration_s = duration_s
            crons[index] = cron
            st.save(crons)
            break
        else:
            print(
                f"record-run: unknown cron id {cron_id!r}; job ran without recording",
                file=sys.stderr,
            )
    except Exception as exc:
        print(f"record-run: failed to record run for {cron_id!r}: {exc}", file=sys.stderr)
    try:
        # WI-9: signed run receipt, independent of the store writeback above.
        # Best-effort — it must never mask the job's own exit code.
        append_cron_run_receipt(
            build_cron_run_receipt(
                cron_id=cron_id,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                started_at=started_dt,
                finished_at=finished_dt,
            )
        )
    except Exception as exc:
        print(
            f"record-run: failed to write run receipt for {cron_id!r}: {exc}",
            file=sys.stderr,
        )
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Prismatic native cron registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    export = sub.add_parser("export-crontab")
    export.add_argument("--include-header", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("cron_id")
    record = sub.add_parser(
        "record-run",
        help="Run a shell command and record the outcome against a cron id "
        "(used by exported crontab lines)",
    )
    record.add_argument("cron_id")
    record.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Shell command to run (everything after --)",
    )
    add = sub.add_parser("add", help="Create a native cron")
    add.add_argument("--id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--schedule", required=True,
                     help="5-field cron expression or 'manual'")
    add.add_argument("--command", nargs="+", required=True,
                     help="Command argv, e.g. --command python3 scripts/job.py")
    add.add_argument("--cwd", default=".")
    add.add_argument("--group", default="general")
    add.add_argument("--tags", default="",
                     help="Comma-separated tags")
    add.add_argument("--description", default="")
    add.add_argument("--env", action="append", default=[],
                     help="Environment variable as KEY=VAL (repeatable)")
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
    if args.cmd == "record-run":
        # The export passes the job tail as a single shell-quoted word after
        # `--`; direct CLI use may pass several words, joined best-effort.
        shell_command = args.command[0] if len(args.command) == 1 else " ".join(args.command)
        return record_cron_run(args.cron_id, shell_command)
    if args.cmd == "add":
        env: dict[str, str] = {}
        for item in args.env:
            if "=" not in item:
                print(
                    json.dumps({"error": f"Invalid --env {item!r}: expected KEY=VAL"}),
                    file=sys.stderr,
                )
                return 2
            key, value = item.split("=", 1)
            env[key] = value
        try:
            created = create_native_cron(
                {
                    "id": args.id,
                    "name": args.name,
                    "schedule": args.schedule,
                    "command": args.command,
                    "cwd": args.cwd,
                    "group": args.group,
                    "tags": [tag for tag in args.tags.split(",") if tag],
                    "description": args.description,
                    "env": env,
                }
            )
        except (ValueError, TypeError) as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 1
        print(json.dumps(created, indent=2, sort_keys=True))
        return 0
    if args.cmd == "mutate":
        print(json.dumps(mutate_native_cron(args.cron_id, args.action), indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
