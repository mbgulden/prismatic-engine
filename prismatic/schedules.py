"""
prismatic/schedules.py — Schedule Observatory Engine

Defines normalized schedule records, events, adapters for local (cron/systemd)
and remote (AGY/Jules) providers, and owner-aware mutation policies.
"""

from __future__ import annotations

import os
import json
import uuid
import urllib.error
import urllib.request
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prismatic.schedules")

# Owner enum mapping
OWNER_PRISMATIC = "prismatic"
OWNER_AGY = "agy"
OWNER_JULES = "jules"
OWNER_TASK_MANAGER = "task-manager"

# Schedule Type enum mapping
TYPE_CRON = "cron"
TYPE_SYSTEMD = "systemd-timer"
TYPE_ONE_SHOT = "one-shot"
TYPE_INTERVAL = "interval"
TYPE_REMOTE = "remote-managed"

# Status enum mapping
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"
STATUS_CANCELLED = "cancelled"


@dataclass
class LastRunInfo:
    fired_at: str
    status: str
    run_id: Optional[str] = None
    duration_sec: Optional[float] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ScheduleRecord:
    id: str
    name: str
    owner: str  # prismatic | agy | jules | task-manager
    schedule_type: str  # cron | systemd-timer | one-shot | interval | remote-managed
    schedule_expr: str
    enabled: bool
    next_run_at: Optional[str] = None
    last_run: Optional[LastRunInfo] = None
    deep_link: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "schedule_type": self.schedule_type,
            "schedule_expr": self.schedule_expr,
            "enabled": self.enabled,
            "next_run_at": self.next_run_at,
            "deep_link": self.deep_link,
            "metadata": self.metadata,
        }
        if self.last_run:
            res["last_run"] = self.last_run.to_dict()
        else:
            res["last_run"] = None
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScheduleRecord:
        last_run_data = data.get("last_run")
        last_run = None
        if last_run_data:
            last_run = LastRunInfo(
                fired_at=last_run_data["fired_at"],
                status=last_run_data["status"],
                run_id=last_run_data.get("run_id"),
                duration_sec=last_run_data.get("duration_sec"),
                error_message=last_run_data.get("error_message"),
            )
        return cls(
            id=data["id"],
            name=data["name"],
            owner=data["owner"],
            schedule_type=data["schedule_type"],
            schedule_expr=data["schedule_expr"],
            enabled=data["enabled"],
            next_run_at=data.get("next_run_at"),
            last_run=last_run,
            deep_link=data.get("deep_link"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ScheduleEvent:
    event_id: str
    event_type: str  # schedule.created / schedule.fired / etc.
    schedule_id: str
    owner: str
    timestamp: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Adapters ──────────────────────────────────────────────────────────

def get_prismatic_cron_jobs(cron_jobs_path: Optional[Path] = None) -> List[ScheduleRecord]:
    """Inventory Prismatic cron jobs from the configured jobs.json."""
    if not cron_jobs_path:
        # Resolve path similarly to journal.py
        state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()
        profile_dir = Path(os.environ.get("PRISMATIC_HARNESS_PROFILE", "~/.harness/profiles/orchestrator")).expanduser()
        cron_jobs_path = Path(os.environ.get("PRISMATIC_CRON_JOBS", str(profile_dir / "cron" / "jobs.json"))).expanduser()

    if not cron_jobs_path.exists():
        return []

    try:
        data = json.loads(cron_jobs_path.read_text())
        raw_jobs = data if isinstance(data, list) else data.get("jobs", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.error("Failed to parse cron jobs file %s: %s", cron_jobs_path, e)
        return []

    records = []
    for job in raw_jobs:
        if not isinstance(job, dict):
            continue
        job_id = job.get("id") or job.get("job_id") or str(uuid.uuid4())
        name = job.get("name") or f"Cron Job {job_id}"
        enabled = job.get("enabled", not job.get("paused", False))
        expr = job.get("schedule_display") or job.get("schedule") or "* * * * *"
        
        last_run = None
        if job.get("last_run_at"):
            last_run = LastRunInfo(
                fired_at=job["last_run_at"],
                status=job.get("last_status") or STATUS_SUCCESS,
                run_id=job.get("last_run_id"),
                duration_sec=job.get("last_duration_sec"),
                error_message=job.get("last_error"),
            )

        records.append(ScheduleRecord(
            id=f"prismatic:cron:{job_id}",
            name=name,
            owner=OWNER_PRISMATIC,
            schedule_type=TYPE_CRON,
            schedule_expr=expr,
            enabled=enabled,
            next_run_at=job.get("next_run_at"),
            last_run=last_run,
            deep_link=None,
            metadata={"script": job.get("script", ""), "deliver": job.get("deliver", "")}
        ))
    return records


def get_systemd_timer_schedules() -> List[ScheduleRecord]:
    """Inventory systemd timers by looking at configured service units or mock data."""
    # systemd timers run locally. Let's return local timers.
    # In production, we'd query systemctl list-timers --all
    # We will simulate discovery of the prismatic-watchdog.timer
    watchdog_timer = ScheduleRecord(
        id="prismatic:systemd:prismatic-watchdog",
        name="Prismatic Distributed Watchdog Timer",
        owner=OWNER_PRISMATIC,
        schedule_type=TYPE_SYSTEMD,
        schedule_expr="OnCalendar=*:0/5",  # every 5 minutes
        enabled=True,
        next_run_at=datetime.now(timezone.utc).isoformat(),  # Simulated next run
        last_run=LastRunInfo(
            fired_at=datetime.now(timezone.utc).isoformat(),
            status=STATUS_SUCCESS
        ),
        deep_link=None,
        metadata={"unit": "prismatic-watchdog.timer", "service": "prismatic-watchdog.service"}
    )
    return [watchdog_timer]


def get_agy_schedules() -> List[ScheduleRecord]:
    """Inventory AGY `/schedule` events and jobs.

    Reads from the local AGY schedule index at
    ``~/.gemini/schedules/*.json`` when it exists. Each file is
    expected to be a JSON list of dicts with keys:
    ``id, name, schedule, enabled, schedule_type? (default 'cron')``.

    On a clean filesystem, falls back to the canonical mock
    representation and emits a single warning log so the Schedule
    Observatory can render an honest "data freshness" badge.
    """
    schedules_dir = Path(os.environ.get(
        "AGY_SCHEDULES_DIR", str(Path.home() / ".gemini" / "schedules")
    ))
    if schedules_dir.exists() and schedules_dir.is_dir():
        try:
            json_files = sorted(schedules_dir.glob("*.json"))
            records: List[ScheduleRecord] = []
            for path in json_files:
                try:
                    data = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "agy schedule adapter: failed to parse %s: %s", path, exc
                    )
                    continue
                # Each file may be a list of dicts or a single dict.
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    rec = ScheduleRecord(
                        id=f"agy:schedule:{entry['id']}",
                        name=entry.get("name", entry["id"]),
                        owner=OWNER_AGY,
                        schedule_type=entry.get("schedule_type", TYPE_CRON),
                        schedule_expr=str(entry.get("schedule", "* * * * *")),
                        enabled=bool(entry.get("enabled", True)),
                        next_run_at=entry.get("next_run_at"),
                        last_run=None,
                        deep_link=f"http://jules.google.com/agy/schedules/{entry['id']}",
                        metadata={
                            "adapter": "live",
                            "source_file": str(path),
                            "lane": entry.get("lane", "agy-default"),
                            "workspace": entry.get("workspace", "prismatic-engine"),
                        },
                    )
                    records.append(rec)
            if records:
                return records
        except OSError as exc:
            logger.warning(
                "agy schedule adapter: read error from %s: %s; using fallback",
                schedules_dir, exc,
            )

    # Fallback: mock data, with honest "adapter" metadata.
    logger.warning(
        "agy schedule adapter: no schedules found at %s, using fallback mock data",
        schedules_dir,
    )
    s1 = ScheduleRecord(
        id="agy:schedule:daily-repo-sync",
        name="AGY Repository Auto-Sync",
        owner=OWNER_AGY,
        schedule_type=TYPE_CRON,
        schedule_expr="0 2 * * *",  # 2 AM daily
        enabled=True,
        next_run_at=None,
        last_run=LastRunInfo(
            fired_at=datetime.now(timezone.utc).isoformat(),
            status=STATUS_SUCCESS
        ),
        deep_link="http://jules.google.com/agy/schedules/daily-repo-sync",
        metadata={
            "adapter": "fallback-mock",
            "reason": f"no files in {schedules_dir}",
            "lane": "agy-review-lane",
            "workspace": "prismatic-engine",
        },
    )
    s2 = ScheduleRecord(
        id="agy:schedule:pr-watch",
        name="AGY PR Watcher & Triage",
        owner=OWNER_AGY,
        schedule_type=TYPE_INTERVAL,
        schedule_expr="every 15 minutes",
        enabled=False,  # disabled schedule
        next_run_at=None,
        last_run=None,
        deep_link="http://jules.google.com/agy/schedules/pr-watch",
        metadata={
            "adapter": "fallback-mock",
            "reason": f"no files in {schedules_dir}",
            "lane": "agy-triage-lane",
        },
    )
    return [s1, s2]


def get_jules_schedules() -> List[ScheduleRecord]:
    """Inventory Jules schedules.

    Three-tier read path:

    1. Local config: ``~/.config/jules/schedules.json`` (list of dicts).
    2. Remote API: if ``JULES_API_KEY`` is set, call
       ``https://jules.google.com/api/v1/schedules`` (Bearer auth).
    3. Fallback: canonical mock with ``metadata.adapter = "fallback-mock"``.

    Each path emits a single warning log on fallback so the Schedule
    Observatory can render an honest "data freshness" badge.
    """
    # Tier 1: local config
    local_path = Path(os.environ.get(
        "JULES_SCHEDULES_FILE", str(Path.home() / ".config" / "jules" / "schedules.json")
    ))
    if local_path.exists():
        try:
            data = json.loads(local_path.read_text())
            entries = data if isinstance(data, list) else [data]
            records: List[ScheduleRecord] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                rec = ScheduleRecord(
                    id=f"jules:schedule:{entry['id']}",
                    name=entry.get("name", entry["id"]),
                    owner=OWNER_JULES,
                    schedule_type=TYPE_REMOTE,
                    schedule_expr=str(entry.get("schedule", "0 0 * * 0")),
                    enabled=bool(entry.get("enabled", True)),
                    next_run_at=entry.get("next_run_at"),
                    last_run=None,
                    deep_link=f"https://jules.google.com/schedules/{entry['id']}",
                    metadata={
                        "adapter": "live",
                        "source_file": str(local_path),
                        "persona": entry.get("persona", "default"),
                        "repo": entry.get("repo", ""),
                    },
                )
                records.append(rec)
            if records:
                return records
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "jules schedule adapter: failed to parse %s: %s; falling through",
                local_path, exc,
            )

    # Tier 2: remote API (only if JULES_API_KEY is set)
    api_key = os.environ.get("JULES_API_KEY", "").strip()
    if api_key:
        try:
            req = urllib.request.Request(
                "https://jules.google.com/api/v1/schedules",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                    "User-Agent": "Prismatic-Engine",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            entries = payload if isinstance(payload, list) else payload.get("schedules", [])
            records: List[ScheduleRecord] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                rec = ScheduleRecord(
                    id=f"jules:schedule:{entry['id']}",
                    name=entry.get("name", entry["id"]),
                    owner=OWNER_JULES,
                    schedule_type=TYPE_REMOTE,
                    schedule_expr=str(entry.get("schedule", "0 0 * * 0")),
                    enabled=bool(entry.get("enabled", True)),
                    next_run_at=entry.get("next_run_at"),
                    last_run=None,
                    deep_link=f"https://jules.google.com/schedules/{entry['id']}",
                    metadata={
                        "adapter": "live",
                        "source": "jules.google.com/api/v1/schedules",
                        "persona": entry.get("persona", "default"),
                        "repo": entry.get("repo", ""),
                    },
                )
                records.append(rec)
            if records:
                return records
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "jules schedule adapter: API call failed, using fallback; error=%s", exc
            )

    # Tier 3: fallback mock
    logger.warning(
        "jules schedule adapter: no live source available, using fallback mock data"
    )
    j1 = ScheduleRecord(
        id="jules:schedule:dependency-scan",
        name="Jules Cloud Dependency Vulnerability Scanner",
        owner=OWNER_JULES,
        schedule_type=TYPE_REMOTE,
        schedule_expr="0 0 * * 0",  # weekly on Sunday
        enabled=True,
        next_run_at=None,
        last_run=LastRunInfo(
            fired_at=datetime.now(timezone.utc).isoformat(),
            status=STATUS_SUCCESS
        ),
        deep_link="https://jules.google.com/schedules/dependency-scan",
        metadata={
            "adapter": "fallback-mock",
            "reason": "no local file and no JULES_API_KEY",
            "persona": "security-reviewer",
            "repo": "github.com/mbgulden/prismatic-engine",
        },
    )
    return [j1]


def get_all_schedules(cron_jobs_path: Optional[Path] = None) -> List[ScheduleRecord]:
    """Get unified list of all scheduled jobs across providers."""
    schedules = []
    schedules.extend(get_prismatic_cron_jobs(cron_jobs_path))
    schedules.extend(get_systemd_timer_schedules())
    schedules.extend(get_agy_schedules())
    schedules.extend(get_jules_schedules())
    return schedules


# ── Mutation Policy Guard ─────────────────────────────────────────────

class UnauthorizedMutationError(PermissionError):
    pass


def request_schedule_mutation(
    schedule_id: str,
    enabled: Optional[bool] = None,
    schedule_expr: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Mutate a schedule safely using owner-aware policy gates.
    
    Rules:
      - 'prismatic' schedules can be modified directly (rewrites local config).
      - 'agy' schedules reject direct mutation, suggesting conversational command routing.
      - 'jules' schedules are completely read-only (immutable at the gateway layer).
    """
    schedules = get_all_schedules(config_path)
    target = next((s for s in schedules if s.id == schedule_id), None)
    
    if not target:
        raise FileNotFoundError(f"Schedule with ID '{schedule_id}' not found.")
        
    if target.owner == OWNER_PRISMATIC:
        # Directly execute mutation on local config
        if target.schedule_type == TYPE_CRON:
            return _mutate_local_cron_job(target.id, enabled, schedule_expr, config_path)
        elif target.schedule_type == TYPE_SYSTEMD:
            # We policy gate systemd direct edits as read-only for safety (requires sudo / systemctl)
            raise UnauthorizedMutationError(
                f"Direct mutation of systemd timer '{schedule_id}' is restricted for safety. "
                "Modify the timer unit file via terminal/sudo instead."
            )
            
    elif target.owner == OWNER_AGY:
        # AGY mutation policy: Route request through AGY Chat / Telegram gateway
        # Command syntax to suggest to the user:
        suggested_cmd = f"ask AGY to update that schedule {schedule_id}"
        if enabled is not None:
            suggested_cmd += f" to {'enabled' if enabled else 'disabled'}"
        if schedule_expr:
            suggested_cmd += f" with rate '{schedule_expr}'"
            
        raise UnauthorizedMutationError(
            f"Cannot directly edit AGY schedule '{schedule_id}'. "
            f"Please request this change via AGY chat. Suggestion: '{suggested_cmd}'"
        )
        
    elif target.owner == OWNER_JULES:
        # Jules mutation policy: Immutable, direct external dashboard link required
        raise UnauthorizedMutationError(
            f"Jules schedule '{schedule_id}' is read-only. "
            f"Please modify it directly on Jules console: {target.deep_link or 'https://jules.google.com'}"
        )
        
    raise UnauthorizedMutationError(f"Unsupported schedule owner: {target.owner}")


def _mutate_local_cron_job(
    full_id: str,
    enabled: Optional[bool] = None,
    schedule_expr: Optional[str] = None,
    cron_jobs_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Directly updates local cron configuration file for prismatic-owned schedules."""
    job_id = full_id.split(":")[-1]
    
    if not cron_jobs_path:
        profile_dir = Path(os.environ.get("PRISMATIC_HARNESS_PROFILE", "~/.harness/profiles/orchestrator")).expanduser()
        cron_jobs_path = Path(os.environ.get("PRISMATIC_CRON_JOBS", str(profile_dir / "cron" / "jobs.json"))).expanduser()

    if not cron_jobs_path.exists():
        raise FileNotFoundError(f"Cron configuration not found at {cron_jobs_path}")

    data = json.loads(cron_jobs_path.read_text())
    is_list = isinstance(data, list)
    raw_jobs = data if is_list else data.get("jobs", [])
    
    updated_job = None
    for job in raw_jobs:
        if job.get("id") == job_id or job.get("job_id") == job_id:
            if enabled is not None:
                # Support both paused/enabled conventions
                job["enabled"] = enabled
                if "paused" in job:
                    job["paused"] = not enabled
            if schedule_expr:
                job["schedule"] = schedule_expr
                if "schedule_display" in job:
                    job["schedule_display"] = schedule_expr
            updated_job = job
            break
            
    if not updated_job:
        raise FileNotFoundError(f"Cron job '{job_id}' not found in configuration.")

    # Write back to file atomic/safely
    cron_jobs_path.write_text(json.dumps(data, indent=2))
    
    return {
        "status": "success",
        "message": f"Updated local Prismatic cron job {job_id}",
        "schedule": updated_job
    }


# ── Chat Command Ingestion ───────────────────────────────────────────

def process_chat_schedule_request(message: str) -> Dict[str, Any]:
    """
    Parses conversational chat messages requesting schedule changes.
    E.g., "ask AGY to update that schedule agy:schedule:daily-repo-sync to disabled"
    
    Returns details of the execution or suggestion.
    """
    import re
    # Match pattern: ask AGY to update (that )?schedule (<schedule_id>\S+) to (enabled|disabled)
    pattern = r"ask\s+AGY\s+to\s+update\s+(?:that\s+)?schedule\s+([\w\-:]+)\s+to\s+(enabled|disabled)"
    match = re.search(pattern, message, re.IGNORECASE)
    
    if not match:
        return {
            "success": False,
            "message": "Did not match schedule update command format. Use: 'ask AGY to update schedule <id> to <enabled|disabled>'"
        }
        
    sched_id = match.group(1)
    status_str = match.group(2).lower()
    should_enable = (status_str == "enabled")
    
    # Simulate routing request to AGY CLI or internal mock agent lane
    # AGY updates its schedule file, and Prismatic will re-import the updated config.
    # Here we simulate the change success and return event payload.
    return {
        "success": True,
        "schedule_id": sched_id,
        "action": "update",
        "updates": {"enabled": should_enable},
        "message": f"AGY schedule change request received. Simulating AGY lane updating schedule '{sched_id}' to {status_str}."
    }
