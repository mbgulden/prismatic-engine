"""
Prismatic Fleet Manager and Automated Session Hygiene Engine.

Provides zero-touch onboarding, health inspection, automated context compression,
and session reset/rotation across multi-profile Hermes agent fleets.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger("prismatic.fleet")


class SessionHealth(str, Enum):
    HEALTHY = "HEALTHY"
    BLOATED = "BLOATED"
    STALLED = "STALLED"
    DEGENERATE = "DEGENERATE"


@dataclass
class SessionInfo:
    profile: str
    session_key: str
    session_id: str
    last_prompt_tokens: int
    total_tokens: int
    message_count: int
    health: SessionHealth
    health_reason: str
    updated_at: str
    active_turn_token: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["health"] = self.health.value
        return d


@dataclass
class ProfileStatus:
    name: str
    path: str
    has_config: bool
    has_state_db: bool
    compression_threshold_tokens: Optional[int]
    telemetry_enabled: bool
    systemd_service: str
    systemd_active: bool
    sessions: List[SessionInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["sessions"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.sessions]
        return d


class PrismaticFleetManager:
    """Automates fleet discovery, session hygiene, and configuration synchronization."""

    SYSTEMD_TEMPLATE_PATH = Path("/etc/systemd/system/hermes-gateway@.service")
    DEFAULT_HERMES_ROOT = Path("/home/ubuntu/.hermes")
    DEFAULT_HERMES_FORK_DIR = Path("/home/ubuntu/work/hermes-agent-fork")
    DEFAULT_GATEWAY_URL = "http://127.0.0.1:9000"

    # Default context window & hygiene thresholds
    DEFAULT_THRESHOLD_TOKENS = 48000
    DEFAULT_CONTEXT_WINDOW = 65536
    DEFAULT_THRESHOLD_RATIO = 0.75
    DEFAULT_THRESHOLD_MESSAGES = 40
    DEFAULT_HEADROOM_TOKENS = 16000

    def __init__(
        self,
        hermes_root: Optional[Path] = None,
        gateway_url: Optional[str] = None,
        hermes_fork_dir: Optional[Path] = None,
    ):
        self.hermes_root = Path(hermes_root or os.getenv("HERMES_ROOT", self.DEFAULT_HERMES_ROOT))
        self.gateway_url = str(gateway_url or os.getenv("PRISMATIC_GATEWAY_URL", self.DEFAULT_GATEWAY_URL)).rstrip("/")
        self.hermes_fork_dir = Path(hermes_fork_dir or self.DEFAULT_HERMES_FORK_DIR)

    def resolve_profile_thresholds(
        self,
        profile: str,
        threshold_tokens: Optional[int] = None,
        context_window: Optional[int] = None,
    ) -> Tuple[int, int, int]:
        """Resolve (threshold_tokens, context_window, degenerate_tokens) for a profile.

        Priority order for threshold_tokens:
        1. Explicitly passed threshold_tokens (if not None).
        2. Configured compression.threshold_tokens or model.compression_threshold in profile's config.yaml.
        3. 75% of context_window (if context_window configured).
        4. DEFAULT_THRESHOLD_TOKENS (48000).
        """
        cfg_path = self.get_profile_path(profile) / "config.yaml"
        cfg_threshold = None
        cfg_ctx = None
        cfg_ratio = self.DEFAULT_THRESHOLD_RATIO

        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                compression = cfg.get("compression", {})
                model = cfg.get("model", {})
                if isinstance(compression, dict):
                    cfg_threshold = compression.get("threshold_tokens")
                    cfg_ctx = compression.get("context_window")
                    cfg_ratio = float(compression.get("threshold") or compression.get("threshold_ratio") or cfg_ratio)
                if isinstance(model, dict):
                    if cfg_threshold is None:
                        cfg_threshold = model.get("compression_threshold")
                    if cfg_ctx is None:
                        cfg_ctx = model.get("context_window")
            except Exception:
                pass

        resolved_ctx = context_window or cfg_ctx or self.DEFAULT_CONTEXT_WINDOW

        if threshold_tokens is not None:
            resolved_threshold = threshold_tokens
        elif cfg_threshold is not None:
            resolved_threshold = int(cfg_threshold)
        elif context_window is not None or cfg_ctx is not None:
            resolved_threshold = int(resolved_ctx * cfg_ratio)
        else:
            resolved_threshold = self.DEFAULT_THRESHOLD_TOKENS

        # Degenerate boundary is >= 95% of context window
        degenerate_threshold = int(resolved_ctx * 0.95)

        return resolved_threshold, resolved_ctx, degenerate_threshold

    def get_profiles_dir(self) -> Path:
        return self.hermes_root / "profiles"

    def discover_profile_names(self) -> List[str]:
        """Return list of discovered profile names."""
        profiles_dir = self.get_profiles_dir()
        names = []
        if profiles_dir.is_dir():
            for p in sorted(profiles_dir.iterdir()):
                if p.is_dir() and not p.is_symlink():
                    names.append(p.name)
        if "default" not in names and (self.hermes_root / "config.yaml").exists():
            names.append("default")
        return names

    def get_profile_path(self, profile: str) -> Path:
        if profile == "default":
            return self.hermes_root
        return self.get_profiles_dir() / profile

    def _get_service_status(self, profile: str) -> Tuple[str, bool]:
        """Check systemd status for a profile (checks both template and legacy service names)."""
        template_svc = f"hermes-gateway@{profile}.service"
        legacy_candidates = [
            f"hermes-{profile}-gateway.service",
            f"hermes-gateway-{profile}.service",
        ]
        if profile == "orchestrator":
            legacy_candidates.insert(0, "hermes-orchestrator-gateway.service")

        for svc in [template_svc] + legacy_candidates:
            res = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                return svc, True

        return template_svc, False

    def inspect_session_health(
        self,
        profile: str,
        threshold_tokens: Optional[int] = None,
        threshold_messages: int = DEFAULT_THRESHOLD_MESSAGES,
        context_window: Optional[int] = None,
    ) -> List[SessionInfo]:
        """Inspect all active gateway routing sessions for a profile and diagnose health."""
        profile_path = self.get_profile_path(profile)
        db_path = profile_path / "state.db"
        if not db_path.exists():
            return []

        results: List[SessionInfo] = []
        resolved_threshold, resolved_ctx, degenerate_threshold = self.resolve_profile_thresholds(
            profile,
            threshold_tokens=threshold_tokens,
            context_window=context_window,
        )

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Query gateway routing table
            cursor.execute("SELECT session_key, entry_json, updated_at FROM gateway_routing;")
            rows = cursor.fetchall()

            for session_key, entry_json, updated_at_ts in rows:
                try:
                    entry = json.loads(entry_json)
                except Exception:
                    continue

                session_id = entry.get("session_id", "unknown")
                last_prompt_tokens = int(entry.get("last_prompt_tokens") or 0)
                total_tokens = int(entry.get("total_tokens") or 0)
                active_turn_token = entry.get("active_turn_token")
                updated_at_str = entry.get("updated_at", str(updated_at_ts))

                # Count messages in this session
                msg_count = 0
                try:
                    cursor.execute(
                        "SELECT COUNT(*) FROM messages WHERE session_id = ?;",
                        (session_id,),
                    )
                    row_cnt = cursor.fetchone()
                    if row_cnt:
                        msg_count = row_cnt[0]
                except Exception:
                    pass

                # Diagnose health
                health = SessionHealth.HEALTHY
                reason = "Session within optimal operational bounds."

                if last_prompt_tokens >= degenerate_threshold:
                    health = SessionHealth.DEGENERATE
                    reason = (
                        f"Degenerate prompt length ({last_prompt_tokens:,} tokens) "
                        f"exceeds safe attention bounds (~{degenerate_threshold:,} tokens). High risk of token spew."
                    )
                elif last_prompt_tokens >= resolved_threshold or msg_count >= threshold_messages:
                    health = SessionHealth.BLOATED
                    reason = (
                        f"Context size ({last_prompt_tokens:,} tokens, {msg_count} msgs) "
                        f"exceeds hygiene threshold ({resolved_threshold:,} tokens, {threshold_messages} msgs)."
                    )
                elif active_turn_token:
                    # Check staleness: if updated_at is older than 15 mins
                    try:
                        dt = datetime.datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                        age_mins = (datetime.datetime.now(datetime.timezone.utc) - dt.astimezone(datetime.timezone.utc)).total_seconds() / 60
                        if age_mins > 15:
                            health = SessionHealth.STALLED
                            reason = f"Turn lock held by {active_turn_token} for {age_mins:.1f} minutes without completion."
                    except Exception:
                        pass

                results.append(
                    SessionInfo(
                        profile=profile,
                        session_key=session_key,
                        session_id=session_id,
                        last_prompt_tokens=last_prompt_tokens,
                        total_tokens=total_tokens,
                        message_count=msg_count,
                        health=health,
                        health_reason=reason,
                        updated_at=updated_at_str,
                        active_turn_token=active_turn_token,
                    )
                )
            conn.close()
        except Exception as e:
            logger.warning("Failed to inspect state.db for %s: %s", profile, e)

        return results

    def discover_profiles(
        self,
        threshold_tokens: Optional[int] = None,
        threshold_messages: int = DEFAULT_THRESHOLD_MESSAGES,
        context_window: Optional[int] = None,
    ) -> List[ProfileStatus]:
        """Scan and inspect all profiles across the fleet."""
        names = self.discover_profile_names()
        statuses: List[ProfileStatus] = []

        for name in names:
            p_path = self.get_profile_path(name)
            cfg_path = p_path / "config.yaml"
            db_path = p_path / "state.db"

            threshold_cap = None
            telemetry_enabled = False

            if cfg_path.exists():
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = yaml.safe_load(f) or {}
                    compression = cfg.get("compression", {})
                    threshold_cap = compression.get("threshold_tokens")
                    plugins = cfg.get("plugins", {}).get("enabled", [])
                    telemetry_enabled = "prismatic_telemetry" in plugins
                except Exception:
                    pass

            svc_name, svc_active = self._get_service_status(name)
            sessions = self.inspect_session_health(
                name,
                threshold_tokens=threshold_tokens,
                threshold_messages=threshold_messages,
                context_window=context_window,
            )

            statuses.append(
                ProfileStatus(
                    name=name,
                    path=str(p_path),
                    has_config=cfg_path.exists(),
                    has_state_db=db_path.exists(),
                    compression_threshold_tokens=threshold_cap,
                    telemetry_enabled=telemetry_enabled,
                    systemd_service=svc_name,
                    systemd_active=svc_active,
                    sessions=sessions,
                )
            )

        return statuses

    def sync_profile_config(
        self,
        profile: str,
        threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
    ) -> Dict[str, Any]:
        """Synchronize configuration for a single profile to enforce compression & telemetry."""
        profile_path = self.get_profile_path(profile)
        cfg_path = profile_path / "config.yaml"
        if not cfg_path.exists():
            return {"profile": profile, "status": "SKIPPED", "reason": "No config.yaml found"}

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            # 1. Update compression section (48k threshold on 65k context = 73.2% utilization)
            if "compression" not in cfg or not isinstance(cfg["compression"], dict):
                cfg["compression"] = {}
            headroom = max(context_window - threshold_tokens, 10000)
            cfg["compression"]["enabled"] = True
            cfg["compression"]["threshold"] = 0.75
            cfg["compression"]["threshold_tokens"] = threshold_tokens
            cfg["compression"]["context_window"] = context_window
            cfg["compression"]["headroom_tokens"] = headroom

            # 2. Update model section if present
            if "model" in cfg and isinstance(cfg["model"], dict):
                cfg["model"]["context_window"] = context_window
                cfg["model"]["compression_threshold"] = threshold_tokens
                cfg["model"]["compression_headroom"] = headroom

            # 3. Update plugins section
            if "plugins" not in cfg or not isinstance(cfg["plugins"], dict):
                cfg["plugins"] = {}
            enabled_plugins = cfg["plugins"].get("enabled", [])
            if not isinstance(enabled_plugins, list):
                enabled_plugins = []
            if "prismatic_telemetry" not in enabled_plugins:
                enabled_plugins.append("prismatic_telemetry")
            cfg["plugins"]["enabled"] = enabled_plugins

            # 4. Update environment section for non-blocking autonomous execution
            if "environment" not in cfg or not isinstance(cfg["environment"], dict):
                cfg["environment"] = {}
            cfg["environment"]["PRISMATIC_GATEWAY_URL"] = self.gateway_url
            cfg["environment"]["HERMES_AUTONOMOUS_MODE"] = "1"
            cfg["environment"]["PRISMATIC_AUTONOMOUS"] = "1"

            # 5. Backup and write
            bak_path = profile_path / f"config.yaml.bak-prismatic-{int(time.time())}"
            shutil.copy2(cfg_path, bak_path)

            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

            return {
                "profile": profile,
                "status": "UPDATED",
                "threshold_tokens": threshold_tokens,
                "context_window": context_window,
                "telemetry_enabled": True,
                "backup": str(bak_path),
            }
        except Exception as e:
            logger.exception("Failed to sync config for profile %s: %s", profile, e)
            return {"profile": profile, "status": "ERROR", "error": str(e)}

    def ensure_global_plugins(self) -> Dict[str, Any]:
        """Ensure ~/.hermes/plugins exists and has the global prismatic_telemetry symlink."""
        global_plugins_dir = self.hermes_root / "plugins"
        global_plugins_dir.mkdir(parents=True, exist_ok=True)

        target_plugin = self.hermes_fork_dir / "plugins" / "observability" / "prismatic_telemetry"
        link_path = global_plugins_dir / "prismatic_telemetry"

        if not target_plugin.exists():
            return {
                "status": "ERROR",
                "reason": f"Target plugin directory does not exist: {target_plugin}",
            }

        if link_path.is_symlink() or link_path.exists():
            try:
                if link_path.resolve() == target_plugin.resolve():
                    return {"status": "OK", "link": str(link_path), "target": str(target_plugin)}
                link_path.unlink()
            except Exception:
                pass

        link_path.symlink_to(target_plugin)
        return {"status": "CREATED", "link": str(link_path), "target": str(target_plugin)}

    def reset_profile_session(
        self,
        profile: str,
        session_key: Optional[str] = None,
        reason: str = "fleet_hygiene_reset",
        restart_gateway: bool = True,
    ) -> Dict[str, Any]:
        """Safely reset/rotate bloated or degenerate sessions in SQLite state.db."""
        profile_path = self.get_profile_path(profile)
        db_path = profile_path / "state.db"
        if not db_path.exists():
            return {"profile": profile, "status": "ERROR", "error": "state.db not found"}

        now = datetime.datetime.now(datetime.timezone.utc)
        now_ts = now.timestamp()
        now_iso = now.isoformat()

        resets_performed = []

        try:
            conn = sqlite3.connect(str(db_path), timeout=10.0)
            cursor = conn.cursor()

            # Find matching routing rows
            if session_key:
                cursor.execute(
                    "SELECT session_key, entry_json FROM gateway_routing WHERE session_key = ?;",
                    (session_key,),
                )
            else:
                cursor.execute("SELECT session_key, entry_json FROM gateway_routing;")
            rows = cursor.fetchall()

            for skey, entry_json in rows:
                try:
                    entry = json.loads(entry_json)
                except Exception:
                    continue

                old_session_id = entry.get("session_id")
                new_session_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

                # 1. Archive old session in sessions table
                if old_session_id:
                    cursor.execute(
                        "UPDATE sessions SET ended_at = ?, end_reason = ?, archived = 1 WHERE id = ?;",
                        (now_ts, reason, old_session_id),
                    )

                # 2. Insert new session record
                source_val = entry.get("platform") or "gateway"
                cursor.execute(
                    "INSERT INTO sessions (id, source, session_key, started_at, archived) VALUES (?, ?, ?, ?, 0);",
                    (new_session_id, source_val, skey, now_ts),
                )

                # 3. Update entry_json
                entry["prev_session_id"] = old_session_id
                entry["session_id"] = new_session_id
                entry["created_at"] = now_iso
                entry["updated_at"] = now_iso
                entry["is_fresh_reset"] = True
                entry["was_auto_reset"] = True
                entry["auto_reset_reason"] = reason
                entry["suspended"] = False
                entry["resume_pending"] = False
                entry["resume_reason"] = None
                entry["active_turn_token"] = None
                entry["active_turn_started_at"] = None
                entry["input_tokens"] = 0
                entry["output_tokens"] = 0
                entry["total_tokens"] = 0
                entry["last_prompt_tokens"] = 0

                new_entry_json = json.dumps(entry)

                cursor.execute(
                    "UPDATE gateway_routing SET entry_json = ?, updated_at = ? WHERE session_key = ?;",
                    (new_entry_json, now_ts, skey),
                )

                # 4. Clear any stale turn leases
                try:
                    cursor.execute("DELETE FROM session_turn_leases WHERE session_key = ?;", (skey,))
                except Exception:
                    pass

                # 5. Clear gateway hygiene state
                try:
                    cursor.execute("DELETE FROM gateway_hygiene_state WHERE session_key = ?;", (skey,))
                except Exception:
                    pass

                resets_performed.append({
                    "session_key": skey,
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id,
                })

            conn.commit()
            conn.close()

            # Restart gateway service if requested to flush in-memory state
            restarted_svc = None
            if restart_gateway and resets_performed:
                svc_name, svc_active = self._get_service_status(profile)
                if svc_active:
                    subprocess.run(["sudo", "systemctl", "restart", "--no-block", svc_name], check=False)
                    restarted_svc = svc_name

            # Emit telemetry signal
            self._emit_signal(
                agent_id=profile,
                event_type="fleet_hygiene_reset",
                stage="fleet_hygiene",
                metadata={
                    "profile": profile,
                    "reason": reason,
                    "resets_count": len(resets_performed),
                    "resets": resets_performed,
                    "service_restarted": restarted_svc,
                },
            )

            return {
                "profile": profile,
                "status": "SUCCESS",
                "resets": resets_performed,
                "restarted_service": restarted_svc,
            }

        except Exception as e:
            logger.exception("Failed to reset session for profile %s: %s", profile, e)
            return {"profile": profile, "status": "ERROR", "error": str(e)}

    def install_systemd_template(self) -> Dict[str, Any]:
        """Install or update the standard /etc/systemd/system/hermes-gateway@.service template."""
        template_content = """[Unit]
Description=Hermes Gateway Fleet Service (%i)
After=network-online.target prismatic-gateway.service
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
TimeoutStopSec=120
Type=simple
User=ubuntu
Group=ubuntu
ExecStart=/home/ubuntu/.local/bin/hermes --profile %i gateway run --replace
WorkingDirectory=/home/ubuntu/.hermes/profiles/%i
Environment="HOME=/home/ubuntu"
Environment="USER=ubuntu"
Environment="LOGNAME=ubuntu"
Environment="PATH=/home/ubuntu/.local/share/pipx/venvs/hermes-agent/bin:/usr/bin:/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="VIRTUAL_ENV=/home/ubuntu/.local/share/pipx/venvs/hermes-agent"
Environment="HERMES_HOME=/home/ubuntu/.hermes/profiles/%i"
Environment="HERMES_CRON_SCRIPT_TIMEOUT=7200"
Environment="PRISMATIC_GATEWAY_URL=http://127.0.0.1:9000"
Environment="HERMES_AUTONOMOUS_MODE=1"
Environment="PRISMATIC_AUTONOMOUS=1"
Restart=always
RestartSec=5
RestartForceExitStatus=75
KillMode=mixed
KillSignal=SIGTERM
ExecReload=/bin/kill -USR1 $MAINPID

# Memory guard
MemoryHigh=40G
MemoryMax=48G

StandardOutput=append:/home/ubuntu/.hermes/logs/%i-gateway.log
StandardError=append:/home/ubuntu/.hermes/logs/%i-gateway.log

[Install]
WantedBy=multi-user.target
"""
        try:
            # Write via sudo tee
            proc = subprocess.run(
                ["sudo", "tee", str(self.SYSTEMD_TEMPLATE_PATH)],
                input=template_content,
                text=True,
                capture_output=True,
                check=True,
            )
            # Reload systemd
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)

            return {
                "status": "INSTALLED",
                "template_path": str(self.SYSTEMD_TEMPLATE_PATH),
            }
        except Exception as e:
            logger.exception("Failed to install systemd fleet template: %s", e)
            return {"status": "ERROR", "error": str(e)}

    def run_auto_hygiene(
        self,
        threshold_tokens: Optional[int] = None,
        threshold_messages: int = DEFAULT_THRESHOLD_MESSAGES,
        context_window: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Autonomous hygiene pass across all profiles: detects and resets bloated/degenerate sessions."""
        profiles = self.discover_profile_names()
        actions = []

        for p in profiles:
            sessions = self.inspect_session_health(
                p,
                threshold_tokens=threshold_tokens,
                threshold_messages=threshold_messages,
                context_window=context_window,
            )
            for s in sessions:
                if s.health in (SessionHealth.BLOATED, SessionHealth.DEGENERATE, SessionHealth.STALLED):
                    action_item = {
                        "profile": p,
                        "session_key": s.session_key,
                        "session_id": s.session_id,
                        "tokens": s.last_prompt_tokens,
                        "messages": s.message_count,
                        "health": s.health.value,
                        "reason": s.health_reason,
                    }
                    if not dry_run:
                        reset_res = self.reset_profile_session(
                            profile=p,
                            session_key=s.session_key,
                            reason=f"auto_hygiene_{s.health.value.lower()}",
                            restart_gateway=True,
                        )
                        action_item["reset_result"] = reset_res
                    else:
                        action_item["reset_result"] = "DRY_RUN_SKIPPED"
                    actions.append(action_item)

        return {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "profiles_scanned": len(profiles),
            "actions_taken": actions,
            "dry_run": dry_run,
        }

    def sync_fleet(
        self,
        threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS,
        threshold_messages: int = DEFAULT_THRESHOLD_MESSAGES,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        reset_bloated: bool = True,
        install_service: bool = True,
    ) -> Dict[str, Any]:
        """One-shot zero-touch fleet onboarding & synchronization."""
        results: Dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "global_plugins": self.ensure_global_plugins(),
        }

        if install_service:
            results["systemd_template"] = self.install_systemd_template()

        config_results = []
        profiles = self.discover_profile_names()
        for p in profiles:
            cfg_res = self.sync_profile_config(
                p,
                threshold_tokens=threshold_tokens,
                context_window=context_window,
            )
            config_results.append(cfg_res)
        results["config_sync"] = config_results

        if reset_bloated:
            results["hygiene_actions"] = self.run_auto_hygiene(
                threshold_tokens=threshold_tokens,
                threshold_messages=threshold_messages,
                context_window=context_window,
                dry_run=False,
            )

        # Emit fleet sync telemetry signal
        self._emit_signal(
            agent_id="fleet_manager",
            event_type="fleet_sync_complete",
            stage="fleet_sync",
            metadata={
                "profiles_count": len(profiles),
                "results": results,
            },
        )

        return results

    def _emit_signal(
        self,
        agent_id: str,
        event_type: str,
        stage: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Emit authentic telemetry signal to Prismatic Gateway."""
        url = f"{self.gateway_url}/api/gateway/signals/emit"
        payload = {
            "agent_id": agent_id,
            "event_type": event_type,
            "stage": stage,
            "metadata": metadata,
            "timestamp": time.time(),
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                pass
        except Exception:
            pass
