"""prismatic-cron — cron capability plugin for the Prismatic Engine.

First consumer of the generic plugin lifecycle (on_suspend / on_resume).

The plugin fires configured jobs on 5-field cron schedules. Handler kinds:

* ``script``  — run ``ref`` as a shell command via subprocess (validated with
  ``swarmcron.security.validate_task_command``, environment sanitized with
  ``swarmcron.security.sanitize_env``, output captured and secret-redacted).
* ``prompt``  — record a ``prompt-dispatched`` receipt carrying the prompt
  reference. The harness / agent layer picks the prompt up; the plugin itself
  never calls an LLM.
* ``webhook`` — POST a small JSON payload to the ``ref`` URL.

Every execution appends a run receipt as a JSON line to
``$PRISMATIC_HOME/plugin-state/prismatic-cron/runs.jsonl`` with an idempotency
key (sha256 over canonical fields, styled after
``prismatic/journal.py::signal_idempotency_key``).

The engine owns deterministic journal work; harnesses own cron. This plugin is
infrastructure for harnesses that want cron *as a capability*: it registers no
agent tools (``register_tools()`` returns ``[]``) and exposes job status only
through plugin-registered API routes — it never touches
``prismatic/gateway/server.py``.

Migration path (documented, NOT performed here): each job maps onto
``prismatic.schedules.ScheduleRecord`` with ``owner="prismatic"`` and
``schedule_type="cron"`` via :meth:`CronPlugin.to_schedule_record`. The job
definition fields intentionally cover what the harness profile's
``cron/jobs.json`` expresses (name, schedule, script/prompt, enabled,
deliver), so journal cron jobs COULD migrate onto ScheduleRecords later.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prismatic.interface.plugin import (
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)
from prismatic.schedules import (
    OWNER_PRISMATIC,
    TYPE_CRON,
    LastRunInfo,
    ScheduleRecord,
)
from swarmcron.core import SwarmCronRegistry
from swarmcron.scheduler import CronScheduleEvaluator
from swarmcron.security import (
    SecurityValidationError,
    sanitize_env,
    validate_task_command,
)

PLUGIN_NAME = "prismatic-cron"
PLUGIN_VERSION = "0.1.0"

TICK_INTERVAL_SEC = 30
RUNS_TAIL_MAX = 200
DEFAULT_SCRIPT_TIMEOUT_SEC = 120
OUTPUT_TAIL_CHARS = 4000
WEBHOOK_TIMEOUT_SEC = 15

HANDLER_SCRIPT = "script"
HANDLER_PROMPT = "prompt"
HANDLER_WEBHOOK = "webhook"
HANDLER_KINDS = (HANDLER_SCRIPT, HANDLER_PROMPT, HANDLER_WEBHOOK)

# Conservative secret redaction for captured handler output (mirrors the
# intent of prismatic/journal.py SECRET_PATTERNS without importing it).
_SECRET_PATTERNS = [
    (
        re.compile(
            r"""(?i)\b(api[_-]?key|token|password|secret|oauth code)\b\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s'"]+)"""
        ),
        r"\1: [REDACTED]",
    ),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]+"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|xox[a-z]-[A-Za-z0-9-]+"),
        "[REDACTED]",
    ),
]


def redact_secrets(text: str) -> str:
    """Redact secret-looking material from captured handler output."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def run_idempotency_key(plugin: str, job: str, fired_at_minute: str,
                        handler_kind: str, handler_ref: str) -> str:
    """Stable key for one scheduled job firing.

    Styled after ``prismatic/journal.py::signal_idempotency_key``: sha256 over
    canonical JSON of the identifying fields. A restart that re-ticks the same
    minute produces the same key and is deduped instead of double-firing.
    """
    canonical = {
        "plugin": plugin,
        "job": job,
        "fired_at_minute": fired_at_minute,
        "handler_kind": handler_kind,
        "handler_ref": handler_ref,
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def plugin_state_dir() -> Path:
    """State root for this plugin.

    Matches the lifecycle contract: ``$PRISMATIC_HOME/plugin-state/<name>/``.
    """
    home = Path(os.environ.get("PRISMATIC_HOME") or Path.home())
    return home / "plugin-state" / PLUGIN_NAME


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def validate_job(raw: Any) -> Dict[str, Any]:
    """Validate one job definition; return the canonical job dict.

    Raises PluginValidationError on any violation. Mirrors the manifest
    ``config_schema`` (the loader validates at attach; this is the defensive
    re-check inside on_init / on_resume).
    """
    if not isinstance(raw, dict):
        raise PluginValidationError(f"job must be an object, got {type(raw).__name__}")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PluginValidationError("job.name must be a non-empty string")

    schedule = raw.get("schedule")
    if not isinstance(schedule, str) or not schedule.strip():
        raise PluginValidationError(f"job {name!r}: schedule must be a non-empty string")
    try:
        CronScheduleEvaluator(schedule)
    except ValueError as exc:
        raise PluginValidationError(f"job {name!r}: invalid cron schedule: {exc}") from exc

    handler = raw.get("handler")
    if not isinstance(handler, dict):
        raise PluginValidationError(f"job {name!r}: handler must be an object")
    kind = handler.get("kind")
    if kind not in HANDLER_KINDS:
        raise PluginValidationError(
            f"job {name!r}: handler.kind must be one of {HANDLER_KINDS}, got {kind!r}"
        )
    ref = handler.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise PluginValidationError(f"job {name!r}: handler.ref must be a non-empty string")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise PluginValidationError(f"job {name!r}: enabled must be a boolean")

    deliver = raw.get("deliver")
    if deliver is not None and not isinstance(deliver, dict):
        raise PluginValidationError(f"job {name!r}: deliver must be an object")

    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise PluginValidationError(f"job {name!r}: metadata must be an object")

    policy = raw.get("policy", {})
    if not isinstance(policy, dict):
        raise PluginValidationError(f"job {name!r}: policy must be an object")
    max_runs = policy.get("max_runs_per_day")
    if max_runs is not None and (not isinstance(max_runs, int) or max_runs < 1):
        raise PluginValidationError(
            f"job {name!r}: policy.max_runs_per_day must be a positive integer"
        )

    return {
        "name": name,
        "schedule": schedule,
        "handler": {"kind": kind, "ref": ref},
        "enabled": enabled,
        "deliver": deliver,
        "metadata": metadata,
        "policy": policy,
    }


class CronPlugin(PrismaticPlugin):
    """Cron capability plugin — infrastructure scheduling for harnesses."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._evaluators: Dict[str, CronScheduleEvaluator] = {}
        self._registry: Optional[SwarmCronRegistry] = None
        self._runs: List[Dict[str, Any]] = []          # in-memory receipt history
        self._seen_keys: set = set()                  # idempotency dedupe
        self._last_fire_minute: Dict[str, str] = {}   # job -> "YYYY-MM-DDTHH:MM"
        self._stop_event = threading.Event()
        self._tick_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._script_timeout_sec = DEFAULT_SCRIPT_TIMEOUT_SEC
        self._state_dir = plugin_state_dir()

    # ── lifecycle: init / suspend / resume ──────────────────────────────

    def on_init(self, context: PluginContext) -> None:
        """Validate config jobs, restore state, start the tick thread."""
        config = (context.config or {})
        jobs_cfg = config.get("jobs", [])
        if not isinstance(jobs_cfg, list):
            raise PluginValidationError("config.jobs must be an array")
        self._script_timeout_sec = int(config.get("script_timeout_sec", DEFAULT_SCRIPT_TIMEOUT_SEC))

        jobs: Dict[str, Dict[str, Any]] = {}
        for raw in jobs_cfg:
            job = validate_job(raw)
            if job["name"] in jobs:
                raise PluginValidationError(f"duplicate job name: {job['name']!r}")
            jobs[job["name"]] = job

        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._registry = SwarmCronRegistry(path=self._state_dir / "swarmcron-tasks.json")

        with self._lock:
            self._jobs = jobs
            self._evaluators = {n: CronScheduleEvaluator(j["schedule"]) for n, j in jobs.items()}
            self._restore_history_locked()

        self._start_tick_thread()

    def _restore_history_locked(self) -> None:
        """Belt-and-braces restore: re-read runs.jsonl (+ state.json tail).

        The loader persists plugin state at the contract path; this restores
        run history even if the plugin was re-attached without a suspend
        snapshot flowing through on_resume().
        """
        runs_path = self._state_dir / "runs.jsonl"
        receipts: List[Dict[str, Any]] = []
        if runs_path.exists():
            for line in runs_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    receipts.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # state.json may hold a tail the jsonl missed (belt-and-braces merge).
        state_path = self._state_dir / "state.json"
        if state_path.exists():
            try:
                snapshot = json.loads(state_path.read_text(encoding="utf-8"))
                for r in snapshot.get("runs_tail", []):
                    if isinstance(r, dict) and r.get("idempotency_key"):
                        receipts.append(r)
            except (json.JSONDecodeError, OSError):
                pass
        merged: Dict[str, Dict[str, Any]] = {}
        for r in receipts:
            key = r.get("idempotency_key")
            if isinstance(key, str):
                merged[key] = r
        self._runs = sorted(merged.values(), key=lambda r: r.get("fired_at", ""))[-RUNS_TAIL_MAX:]
        self._seen_keys = set(merged.keys())
        self._rebuild_fire_minutes_locked()

    def _rebuild_fire_minutes_locked(self) -> None:
        """Rebuild per-job last-fire minute markers from run history."""
        self._last_fire_minute = {}
        for r in self._runs:
            fired = str(r.get("fired_at", ""))
            minute = fired[:16]  # "YYYY-MM-DDTHH:MM"
            if len(minute) == 16:
                self._last_fire_minute[str(r.get("job", ""))] = minute

    def _start_tick_thread(self) -> None:
        self._stop_tick_thread()
        self._stop_event.clear()
        self._tick_thread = threading.Thread(
            target=self._tick_loop, name="prismatic-cron-tick", daemon=True
        )
        self._tick_thread.start()

    def _stop_tick_thread(self) -> None:
        self._stop_event.set()
        thread, self._tick_thread = self._tick_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def on_suspend(self) -> Dict[str, Any]:
        """Stop the tick thread and return JSON-serializable plugin state."""
        self._stop_tick_thread()
        with self._lock:
            snapshot = {
                "plugin": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "saved_at": _iso(_utcnow()),
                "jobs": list(self._jobs.values()),
                "runs_tail": list(self._runs[-RUNS_TAIL_MAX:]),
            }
        # Belt-and-braces: persist alongside the loader's own state.json write.
        try:
            (self._state_dir / "state.json").write_text(
                json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
            )
        except OSError:
            pass
        return json.loads(json.dumps(snapshot, default=str))

    def on_resume(self, state: Dict[str, Any]) -> None:
        """Restore jobs + run history from a suspend snapshot; restart ticks."""
        jobs_cfg = state.get("jobs", [])
        jobs: Dict[str, Dict[str, Any]] = {}
        for raw in jobs_cfg:
            job = validate_job(raw)
            jobs[job["name"]] = job
        with self._lock:
            self._jobs = jobs
            self._evaluators = {n: CronScheduleEvaluator(j["schedule"]) for n, j in jobs.items()}
            for r in state.get("runs_tail", []):
                if isinstance(r, dict) and r.get("idempotency_key") not in self._seen_keys:
                    self._runs.append(r)
                    key = r.get("idempotency_key")
                    if isinstance(key, str):
                        self._seen_keys.add(key)
            self._runs = sorted(self._runs, key=lambda r: r.get("fired_at", ""))[-RUNS_TAIL_MAX:]
            self._rebuild_fire_minutes_locked()
        self._start_tick_thread()

    # ── ticking / firing ────────────────────────────────────────────────

    def _tick_loop(self) -> None:
        while not self._stop_event.wait(TICK_INTERVAL_SEC):
            try:
                self._tick_once(_utcnow())
            except Exception:
                # A tick must never kill the thread; the next tick retries.
                continue

    def _tick_once(self, now: datetime) -> List[Dict[str, Any]]:
        """Fire every job due at ``now``. Returns the receipts written."""
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        receipts: List[Dict[str, Any]] = []
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if not job["enabled"]:
                continue
            if self._last_fire_minute.get(job["name"]) == minute_key:
                continue
            evaluator = self._evaluators.get(job["name"])
            if evaluator is None:
                continue
            if evaluator.matches(now):
                receipts.append(self._fire_job(job, now, minute_key))
        return receipts

    def _runs_today(self, job_name: str, today: str) -> int:
        return sum(
            1
            for r in self._runs
            if r.get("job") == job_name
            and str(r.get("fired_at", "")).startswith(today)
            and r.get("status") in ("ok", "failed", "prompt-dispatched")
        )

    def _fire_job(self, job: Dict[str, Any], now: datetime, minute_key: str) -> Dict[str, Any]:
        """Execute one due job and append its run receipt."""
        name = job["name"]
        kind = job["handler"]["kind"]
        ref = job["handler"]["ref"]
        key = run_idempotency_key(PLUGIN_NAME, name, minute_key, kind, ref)

        with self._lock:
            if key in self._seen_keys:
                return {"status": "skipped", "reason": "duplicate-idempotency-key", "job": name}
            self._seen_keys.add(key)
            self._last_fire_minute[name] = minute_key

        today = now.strftime("%Y-%m-%d")
        max_runs = job["policy"].get("max_runs_per_day")
        with self._lock:
            ran_today = self._runs_today(name, today)
        if max_runs is not None and ran_today >= max_runs:
            receipt = self._receipt(key, name, kind, ref, now, 0.0, "skipped",
                                    reason=f"max_runs_per_day={max_runs} reached")
            self._append_receipt(receipt)
            return receipt

        started = _utcnow()
        try:
            if kind == HANDLER_SCRIPT:
                status, output, error = self._run_script(ref)
            elif kind == HANDLER_PROMPT:
                status, output, error = "prompt-dispatched", "", ""
            else:  # webhook
                status, output, error = self._post_webhook(ref, name, now, key)
        except Exception as exc:  # never let a handler kill the tick
            status, output, error = "failed", "", f"{type(exc).__name__}: {exc}"
        duration = (_utcnow() - started).total_seconds()

        receipt = self._receipt(key, name, kind, ref, now, duration, status,
                                output_tail=output, error=error)
        if kind == HANDLER_PROMPT:
            receipt["prompt_ref"] = ref
            receipt["note"] = (
                "prompt-dispatched: the harness/agent layer picks this prompt up; "
                "the plugin does not call LLMs itself"
            )
        self._append_receipt(receipt)
        return receipt

    def _receipt(self, key: str, job: str, kind: str, ref: str, now: datetime,
                 duration: float, status: str, output_tail: str = "",
                 error: str = "", reason: str = "") -> Dict[str, Any]:
        receipt: Dict[str, Any] = {
            "idempotency_key": key,
            "plugin": PLUGIN_NAME,
            "job": job,
            "handler_kind": kind,
            "handler_ref": ref,
            "fired_at": _iso(now),
            "duration_sec": round(duration, 3),
            "status": status,
        }
        if output_tail:
            receipt["output_tail"] = output_tail
        if error:
            receipt["error"] = error
        if reason:
            receipt["reason"] = reason
        return receipt

    def _append_receipt(self, receipt: Dict[str, Any]) -> None:
        line = json.dumps(receipt, default=str)
        try:
            with (self._state_dir / "runs.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        with self._lock:
            self._runs.append(receipt)
            self._runs = self._runs[-RUNS_TAIL_MAX:]

    def _run_script(self, ref: str) -> tuple:
        """Run a script handler command; returns (status, output_tail, error)."""
        try:
            argv = validate_task_command(ref)
        except SecurityValidationError as exc:
            return "failed", "", f"command rejected: {exc}"
        env = sanitize_env(dict(os.environ))
        try:
            proc = subprocess.run(
                argv, env=env, capture_output=True, text=True,
                timeout=self._script_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return "failed", "", f"timed out after {self._script_timeout_sec}s"
        combined = (proc.stdout or "") + (proc.stderr or "")
        output = redact_secrets(combined[-OUTPUT_TAIL_CHARS:])
        if proc.returncode == 0:
            return "ok", output, ""
        return "failed", output, f"exit code {proc.returncode}"

    def _post_webhook(self, url: str, job: str, now: datetime, key: str) -> tuple:
        """POST a JSON payload to a webhook handler URL."""
        payload = json.dumps({
            "plugin": PLUGIN_NAME, "job": job,
            "fired_at": _iso(now), "idempotency_key": key,
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SEC) as resp:
                body = resp.read(OUTPUT_TAIL_CHARS).decode("utf-8", "replace")
                if 200 <= resp.status < 300:
                    return "ok", redact_secrets(body), ""
                return "failed", redact_secrets(body), f"HTTP {resp.status}"
        except Exception as exc:
            return "failed", "", f"{type(exc).__name__}: {exc}"

    # ── plugin-registered API surface (no core edits) ───────────────────

    def jobs_status(self) -> List[Dict[str, Any]]:
        """Payload for GET /api/cron/jobs."""
        out: List[Dict[str, Any]] = []
        with self._lock:
            jobs = list(self._jobs.values())
            runs = list(self._runs)
        for job in jobs:
            evaluator = self._evaluators.get(job["name"])
            next_fire = None
            if evaluator is not None:
                nxt = evaluator.get_next_run()
                next_fire = _iso(nxt) if nxt else None
            last = next((r for r in reversed(runs) if r.get("job") == job["name"]), None)
            out.append({
                "name": job["name"],
                "schedule": job["schedule"],
                "enabled": job["enabled"],
                "next_fire_at": next_fire,
                "last_run": (
                    {"fired_at": last["fired_at"], "status": last["status"]}
                    if last else None
                ),
            })
        return out

    def register_api_routes(self) -> List[Dict[str, Any]]:
        """Route descriptors the PE Gateway exposes for this plugin."""
        return [
            {
                "method": "GET",
                "path": "/api/cron/jobs",
                "description": "List cron jobs with schedule, enabled flag, next fire time, and last run status.",
                "handler": "cron.plugin:CronPlugin.jobs_status",
            }
        ]

    def register_tools(self) -> List[Dict[str, Any]]:
        """Scheduling is infrastructure, not an agent tool."""
        return []

    def capability_contract(self) -> Dict[str, Any]:
        """Machine-readable description of the cron capability."""
        return {
            "capability": "cron-scheduling",
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": (
                "Fires configured jobs on 5-field cron schedules with script, "
                "prompt, and webhook handlers; persists per-run receipts with "
                "idempotency keys."
            ),
            "handler_kinds": list(HANDLER_KINDS),
            "routes": self.register_api_routes(),
            "state_dir": str(self._state_dir),
            "tools": [],
            "notes": [
                "Disabled by default (auto_enable: false); operator enables explicitly.",
                "Prompt handlers never call LLMs: receipts are marked prompt-dispatched for the harness/agent layer.",
                "to_schedule_record() maps each job onto prismatic.schedules.ScheduleRecord (owner=prismatic, type=cron) — the documented future migration path for journal cron jobs.",
            ],
        }

    # ── ScheduleRecord mapping (future migration path) ──────────────────

    def to_schedule_record(self, job: Dict[str, Any]) -> ScheduleRecord:
        """Map a cron job onto ``prismatic.schedules.ScheduleRecord``.

        This is the documented migration path: journal cron jobs (today owned
        by harnesses via ``cron/jobs.json``) could be re-expressed as
        ScheduleRecords with ``owner="prismatic"`` and ``schedule_type="cron"``
        and managed through this plugin. The plugin performs no migration
        itself — this helper only defines the mapping.
        """
        evaluator = self._evaluators.get(job["name"]) or CronScheduleEvaluator(job["schedule"])
        nxt = evaluator.get_next_run()
        last_run: Optional[LastRunInfo] = None
        with self._lock:
            last = next((r for r in reversed(self._runs) if r.get("job") == job["name"]), None)
        if last:
            last_run = LastRunInfo(
                fired_at=str(last.get("fired_at", "")),
                status=str(last.get("status", "")),
                run_id=str(last.get("idempotency_key", ""))[:16],
                duration_sec=last.get("duration_sec"),
                error_message=last.get("error"),
            )
        metadata: Dict[str, str] = {
            "handler_kind": str(job["handler"]["kind"]),
            "handler_ref": str(job["handler"]["ref"]),
            "plugin": PLUGIN_NAME,
        }
        if job.get("deliver"):
            metadata["deliver"] = json.dumps(job["deliver"])
        for k, v in (job.get("metadata") or {}).items():
            if isinstance(v, str):
                metadata[f"meta_{k}"] = v
        return ScheduleRecord(
            id=f"cron:{job['name']}",
            name=job["name"],
            owner=OWNER_PRISMATIC,
            schedule_type=TYPE_CRON,
            schedule_expr=job["schedule"],
            enabled=bool(job["enabled"]),
            next_run_at=_iso(nxt) if nxt else None,
            last_run=last_run,
            metadata=metadata,
        )
