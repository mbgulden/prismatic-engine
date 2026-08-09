"""
Prismatic Engine — Event Dispatcher
====================================

Standalone event loop that polls Linear for label-assigned issues,
routes work to the right agent via signal providers or CLI launches,
and manages pipeline state transitions.

No Hermes dependencies — uses only stdlib, ``requests`` (optional),
and the ``prismatic`` package modules.

Usage
-----
Direct::

    python -m prismatic.dispatcher

Entry-point (after ``pip install``)::

    prismatic-engine
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import shutil
import shlex
import sys
import time
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

# ── Relative package imports ──────────────────────────────────
from .providers.signals import create_signal_provider
from .credit_policy_engine import (
    PolicyAction,
    evaluate_agent_launch,
    AGENT_PROVIDER_MAP,
)
from .budget_caps import budget_caps_configured, evaluate_budget_caps, read_budget_caps
from .telemetry import get_collector
from .capability_router import default_capability_registry, route_issue
from .lane_contracts import filter_dispatchable_issues, starvation_signal_for
from .mode_switch import get_mode_switch
from .handoff_contracts import (
    HandoffValidationResult,
    extract_handoff_packet,
    validation_result,
)
from .linear_rate_limit import (
    LinearRateLimitCircuitOpen,
    ensure_linear_circuit_closed,
    get_linear_rate_limit_snapshot,
    record_linear_response_headers,
    trip_linear_circuit_from_error,
)
from .core.governor import DistributedComputeGovernor

# ── IPC Bridge event emission (best-effort) ─────────────────────
try:
    from .gateway.ipc_bridge import send_event_via_socket

    _HAS_IPC_BRIDGE = True
except ImportError:
    _HAS_IPC_BRIDGE = False


class LinearBudgetExhaustedError(RuntimeError):
    """Raised when the Linear API budget is exhausted for this cycle."""


DISPATCHER_POLLING_BUDGET_MARKER = "DISPATCHER_POLLING_BUDGET_OK"
DEFAULT_POLL_MAX_CALLS_PER_CYCLE = int(
    os.environ.get("PRISMATIC_POLL_MAX_LINEAR_CALLS_PER_CYCLE", "6")
)
DEFAULT_LABEL_SCAN_TTL_SECONDS = int(
    os.environ.get("PRISMATIC_POLL_LABEL_SCAN_TTL_SECONDS", "300")
)
DEFAULT_ROUTE_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_ROUTE_SCAN_CADENCE", "10")
)
DEFAULT_AGENT_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_AGENT_SCAN_CADENCE", "1")
)
DEFAULT_RECOVERY_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_RECOVERY_SCAN_CADENCE", "20")
)
DEFAULT_ORIGIN_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_ORIGIN_SCAN_CADENCE", "20")
)
DEFAULT_PIPELINE_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_PIPELINE_SCAN_CADENCE", "20")
)
_POLL_CYCLE_NUMBER = 0
_CURRENT_POLL_BUDGET: "LinearCycleBudget | None" = None
_LABEL_SCAN_CACHE: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}
_LAST_POLL_BUDGET_STATUS: dict[str, Any] = {}


@dataclass
class LinearCycleBudget:
    max_calls: int
    cycle_number: int
    poll_fallback_enabled: bool
    calls_used: int = 0
    calls_by_source: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0
    skipped_sections: list[str] = field(default_factory=list)
    last_skip_reason: str | None = None

    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def consume(self, source: str) -> None:
        if self.calls_used >= self.max_calls:
            self.last_skip_reason = f"linear call budget exhausted before {source}"
            raise LinearBudgetExhaustedError(self.last_skip_reason)
        self.calls_used += 1
        self.calls_by_source[source] = self.calls_by_source.get(source, 0) + 1

    def skip(self, section: str, reason: str) -> None:
        if section not in self.skipped_sections:
            self.skipped_sections.append(section)
        self.last_skip_reason = reason

    def as_dict(self, *, rate_limit_cooldown_active: bool = False) -> dict[str, Any]:
        return {
            "marker": DISPATCHER_POLLING_BUDGET_MARKER,
            "poll_fallback_enabled": self.poll_fallback_enabled,
            "cycle_number": self.cycle_number,
            "max_calls_per_cycle": self.max_calls,
            "calls_used_this_cycle": self.calls_used,
            "last_cycle_calls": self.calls_used,
            "calls_remaining_this_cycle": self.remaining(),
            "calls_by_source": dict(sorted(self.calls_by_source.items())),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "skipped_sections": list(self.skipped_sections),
            "last_skip_reason": self.last_skip_reason,
            "rate_limit_cooldown_active": rate_limit_cooldown_active,
            "label_scan_ttl_seconds": label_scan_ttl_seconds(),
            "cadence": {
                "pipeline_scan": scan_cadence("pipeline_scan"),
                "route_scan": scan_cadence("route_scan"),
                "agent_scan": scan_cadence("agent_scan"),
                "recovery_scan": scan_cadence("recovery_scan"),
                "origin_scan": scan_cadence("origin_scan"),
            },
            "non_claims": {
                "webhook_queue_active": False,
                "assigned_agent_event_dispatch": False,
                "linear_mutation_applied": False,
            },
        }


mode_switch = get_mode_switch()
_governor = DistributedComputeGovernor()
_active_processes: dict[tuple[str, str], subprocess.Popen] = {}


def _track_agent_process(agent_name: str, task_id: str, proc: subprocess.Popen) -> None:
    _active_processes[(agent_name, task_id)] = proc
    try:
        _governor.update_pid(agent_name, task_id, proc.pid)
    except Exception:
        pass


def _terminate_proc(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def _finalize_agent_launch(
    agent_name: str, task_id: str, proc: subprocess.Popen
) -> bool:
    try:
        _governor.update_pid(agent_name, task_id, proc.pid)
        _track_agent_process(agent_name, task_id, proc)
        return True
    except Exception:
        _terminate_proc(proc)
        try:
            _governor.release(agent_name, task_id)
        except Exception:
            pass
        return False


def check_active_processes() -> int:
    released = 0
    for key, proc in list(_active_processes.items()):
        agent_name, task_id = key
        try:
            if proc.poll() is None:
                try:
                    _governor.heartbeat(agent_name, task_id)
                except Exception:
                    pass
                continue
        except Exception:
            pass
        _active_processes.pop(key, None)
        try:
            _governor.release(agent_name, task_id)
        except Exception:
            pass
        released += 1
    return released


def _handoff_preflight_message(identifier: str, result: HandoffValidationResult) -> str:
    errors = "\n".join(f"- {error}" for error in result.errors)
    if not errors:
        errors = "- unknown handoff contract validation failure"
    return (
        "🚫 **Handoff contract preflight failed**\n\n"
        f"Issue/task: `{identifier}`\n"
        f"Status: `{result.status}`\n"
        f"Reason: `{result.reason}`\n"
        f"Target agent: `{result.target_agent or 'needs_manual_review'}`\n\n"
        f"Errors:\n{errors}\n\n"
        "No agent was launched. Fix the handoff packet and rerun dispatch."
    )


def handoff_dispatch_preflight(
    container: Any, agent_name: str, identifier: str = ""
) -> HandoffValidationResult | None:
    """Fail closed for invalid embedded handoff packets before launching agents.

    Missing handoff metadata means the issue/task is not using the GRO-549
    handoff contract yet and should continue through the existing dispatch path.
    """
    packet = extract_handoff_packet(container)
    if packet is None:
        return None
    result = validation_result(packet)
    if result.ok and result.target_agent != agent_name:
        return HandoffValidationResult(
            ok=False,
            status="blocked",
            reason="target_agent_mismatch",
            errors=(
                f"handoff target agent {result.target_agent!r} does not match "
                f"dispatch lane {agent_name!r}",
            ),
            target_agent=result.target_agent,
        )
    return result


def _mark_handoff_preflight_failure(
    issue_id: str, identifier: str, result: HandoffValidationResult
) -> None:
    try:
        add_comment(issue_id, _handoff_preflight_message(identifier, result))
    except Exception:
        pass


def dispatch_local_tasks(dedup: Any, local_task_queue: Any | None = None) -> int:
    if local_task_queue is None:
        try:
            from prismatic.local_tasks import get_default_queue

            local_task_queue = get_default_queue()
        except Exception:
            return 0
    dispatched = 0
    for task in local_task_queue.list_queued(limit=25):
        preflight = handoff_dispatch_preflight(task, task.agent, task.id)
        if preflight is not None and not preflight.ok:
            status = "needs_manual_review" if preflight.is_manual_review else "blocked"
            local_task_queue.update_status(
                task.id,
                status,
                metadata_patch={
                    "handoff_preflight_status": preflight.status,
                    "handoff_preflight_reason": preflight.reason,
                    "handoff_preflight_errors": list(preflight.errors),
                },
            )
            print(
                f"[dispatcher] 🚫 Handoff preflight {preflight.status} "
                f"for local task {task.id}: {preflight.reason}"
            )
            continue
        launcher = AGENT_LAUNCHERS.get(task.agent)
        if not launcher:
            continue
        result = launcher(task.id, title=task.title, workspace=task.workspace)
        if result:
            metadata_patch = None
            if preflight is not None:
                metadata_patch = {
                    "handoff_preflight_status": preflight.status,
                    "handoff_preflight_reason": preflight.reason,
                }
            local_task_queue.update_status(
                task.id, "dispatched", metadata_patch=metadata_patch
            )
            dispatched += 1
    return dispatched


def _emit_agent_event(event_type: str, agent_name: str, issue_id: str, **extra) -> None:
    """Emit an agent lifecycle event to the IPC bridge (best-effort)."""
    if not _HAS_IPC_BRIDGE:
        return
    try:
        send_event_via_socket(
            event_type=event_type,
            source=f"dispatcher:{agent_name}",
            payload={"agent": agent_name, "issue_id": issue_id, **extra},
        )
    except Exception:
        pass  # Best-effort — don't break dispatch over event emission


# ── Token metrics parsing (GRO-2980.1 / GRO-2990) ──────────────────────────
#
# Each agent provider prints token counts in its own stdout format.
# We parse with regex so the dispatcher doesn't need provider-specific
# SDK dependencies. Returns None if no token-like output is found.

_TOKEN_PATTERNS: list[tuple[str, str, str]] = [
    # (provider_prefix, prompt_pattern, completion_pattern)
    # Ollama (used by local-llm agents: fred, kai, ned)
    (
        "ollama",
        r'"prompt_eval_count"\s*:\s*(\d+)',
        r'"eval_count"\s*:\s*(\d+)',
    ),
    # Anthropic (claude-code → Jules)
    (
        "anthropic",
        r'"input_tokens"\s*:\s*(\d+)',
        r'"output_tokens"\s*:\s*(\d+)',
    ),
    # OpenAI / GitHub Copilot (codex)
    (
        "openai",
        r'"prompt_tokens"\s*:\s*(\d+)',
        r'"completion_tokens"\s*:\s*(\d+)',
    ),
    # Google Antigravity (AGY / Gemini)
    (
        "google-antigravity",
        r'"promptTokenCount"\s*:\s*(\d+)',
        r'"candidatesTokenCount"\s*:\s*(\d+)',
    ),
]


def _parse_token_metrics(provider: str, output: str) -> dict[str, int] | None:
    """Parse token counts from agent stdout.

    Args:
        provider: One of the values in ``AGENT_PROVIDER_MAP`` —
            e.g. ``"local-llm"``, ``"claude-code"``, ``"github-copilot"``,
            ``"google-antigravity"``.  Falls back to generic Ollama-style
            parsing when the provider is unknown.
        output: Captured stdout/stderr from the agent process.

    Returns:
        Dict with ``prompt_tokens`` + ``completion_tokens`` (and any
        matched ``total_tokens``) if found, else ``None``.
    """
    if not output:
        return None

    # Map AGENT_PROVIDER_MAP value → parser prefix
    provider_to_prefix = {
        "local-llm": "ollama",
        "claude-code": "anthropic",
        "github-copilot": "openai",
        "google-antigravity": "google-antigravity",
    }
    prefix = provider_to_prefix.get(provider, "ollama")

    prompt_pat: str | None = None
    completion_pat: str | None = None
    for pfx, p_pat, c_pat in _TOKEN_PATTERNS:
        if pfx == prefix:
            prompt_pat = p_pat
            completion_pat = c_pat
            break

    if prompt_pat is None or completion_pat is None:
        return None

    prompt_m = re.search(prompt_pat, output)
    completion_m = re.search(completion_pat, output)
    if not prompt_m and not completion_m:
        return None

    return {
        "prompt_tokens": int(prompt_m.group(1)) if prompt_m else 0,
        "completion_tokens": int(completion_m.group(1)) if completion_m else 0,
    }


def _drain_and_record_tokens(
    proc: "subprocess.Popen | None",
    run_id: str,
    agent_name: str,
    provider: str,
    timeout: float = 10.0,
) -> None:
    """Drain a finished agent process and emit a token-metrics event.

    Best-effort — never raises.  Called from the dispatch_once flow
    right after ``record_agent_run`` so that the agent_run row exists
    alongside any tokens row that follows.

    Args:
        proc: The ``subprocess.Popen`` returned by a launcher.
        run_id: Matches the ``record_agent_run`` run_id.
        agent_name: ``"fred" | "kai" | "agy" | "jules" | "codex"``.
        provider: From ``AGENT_PROVIDER_MAP``.
        timeout: Seconds to wait for ``communicate`` to drain the pipe.
    """
    if proc is None:
        return
    try:
        stdout_bytes, _ = proc.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        # Process didn't exit cleanly within timeout — kill and try one
        # more time without blocking.
        try:
            proc.kill()
            stdout_bytes, _ = proc.communicate(timeout=2.0)
        except Exception:
            return
    except Exception:
        return

    if not stdout_bytes:
        return

    try:
        output = stdout_bytes.decode("utf-8", errors="replace")
    except Exception:
        return

    metrics = _parse_token_metrics(provider, output)
    if metrics is None:
        return

    try:
        from prismatic.telemetry import get_collector

        collector = get_collector()
        collector.record_tokens(
            run_id=run_id,
            agent=agent_name,
            provider=provider,
            prompt_tokens=metrics["prompt_tokens"],
            completion_tokens=metrics["completion_tokens"],
        )
    except Exception:
        pass  # Telemetry is best-effort


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

TEAM_ID: str = os.environ.get("PRISMATIC_TEAM_ID", "")

DEFAULT_DB_PATH: str = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

# Agent binary paths — override via env vars
AGY_PATH: str = os.environ.get("AGY_PATH", "agy")
JULES_PATH: str = os.environ.get("JULES_PATH", "jules")
CODEX_PATH: str = os.environ.get("CODEX_PATH", "codex")

# Polling interval (seconds)
POLL_INTERVAL: int = int(os.environ.get("PRISMATIC_POLL_INTERVAL", "30"))
MAX_CYCLES_BEFORE_RECOVER: int = int(
    os.environ.get("PRISMATIC_MAX_CYCLES_BEFORE_RECOVER", "6")
)

# Nudge directory for file-based signal providers
NUDGE_DIR: str = os.environ.get("PRISMATIC_NUDGE_DIR", "/tmp/prismatic")

# Pipeline metrics log for dashboard consumption
PIPELINE_METRICS_PATH: str = os.environ.get(
    "PRISMATIC_METRICS_PATH", "/tmp/pipeline_metrics.jsonl"
)

# Durable worker-launch ledger.  Stored beside the event-router state so a
# launch can be traced after the dispatcher process exits.
LAUNCH_RECORDS_DB_PATH: str = os.environ.get(
    "PRISMATIC_LAUNCH_RECORDS_DB_PATH", DEFAULT_DB_PATH
)

# AGY model routing configuration
AGY_CONFIG_PATH: str = os.path.join(
    os.environ.get("HOME", os.path.expanduser("~")),
    ".antigravity",
    "config.json",
)

# Default fallback chain for AGY model routing
AGY_DEFAULT_FALLBACK: list[str] = ["agent:agy-flash-high", "agent:agy"]
AGY_DEFAULT_MODEL: str = "gemini-3.5-flash-med"


def _load_agy_model_config() -> dict[str, Any]:
    """Load the AGY model routing configuration from disk.

    Returns the config dict, or an empty dict if the file
    doesn't exist or can't be parsed.
    """
    if not os.path.exists(AGY_CONFIG_PATH):
        return {}
    try:
        with open(AGY_CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_agy_model_from_labels(labels: list[str]) -> str | None:
    """Map issue labels to an AGY ``--model`` CLI flag value.

    Checks each label against the ``model_bindings`` in the AGY
    config.  Returns the first matching model string, or ``None``
    if no label matches (use AGY default).
    """
    config = _load_agy_model_config()
    bindings = config.get("model_bindings", {})

    for label in labels:
        if label in bindings:
            return bindings[label]

    return None


def _get_agy_fallback_model(labels: list[str]) -> str | None:
    """Determine the fallback model for when a premium model fails.

    Reads the ``fallback_chain`` from the config and returns the
    first model that is NOT the currently-assigned one.  Falls
    back to the built-in default chain.
    """
    config = _load_agy_model_config()
    chain = config.get("fallback_chain", AGY_DEFAULT_FALLBACK)
    bindings = config.get("model_bindings", {})

    # Find the currently assigned model label
    current_label = None
    for label in labels:
        if label in bindings:
            current_label = label
            break

    # Walk the fallback chain, skipping the current label
    for fb_label in chain:
        if fb_label != current_label and fb_label in bindings:
            return bindings[fb_label]

    return AGY_DEFAULT_MODEL


def log_completed_pipeline_metrics(
    issue_id: str,
    agent: str,
    status: str,
    reason: str = "",
    cost: int = 0,
    **kwargs,
) -> None:
    """Append a line to the pipeline metrics JSONL file.

    This is consumed by ``scripts/pipeline_dashboard.py`` for
    real-time pipeline health monitoring.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue_id": issue_id,
        "agent": agent,
        "status": status,
        "reason": reason,
        "cost": cost,
        **kwargs,
    }
    try:
        metrics_dir = os.path.dirname(PIPELINE_METRICS_PATH)
        if metrics_dir:
            os.makedirs(metrics_dir, exist_ok=True)
        with open(PIPELINE_METRICS_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        print(f"[dispatcher] Failed to write metrics: {exc}")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def poll_fallback_enabled() -> bool:
    value = os.environ.get("PRISMATIC_POLL_FALLBACK_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def poll_max_calls_per_cycle() -> int:
    return _env_int(
        "PRISMATIC_POLL_MAX_LINEAR_CALLS_PER_CYCLE", DEFAULT_POLL_MAX_CALLS_PER_CYCLE
    )


def label_scan_ttl_seconds() -> int:
    return _env_int(
        "PRISMATIC_POLL_LABEL_SCAN_TTL_SECONDS", DEFAULT_LABEL_SCAN_TTL_SECONDS
    )


def scan_cadence(section: str) -> int:
    defaults = {
        "pipeline_scan": DEFAULT_PIPELINE_SCAN_CADENCE,
        "route_scan": DEFAULT_ROUTE_SCAN_CADENCE,
        "agent_scan": DEFAULT_AGENT_SCAN_CADENCE,
        "recovery_scan": DEFAULT_RECOVERY_SCAN_CADENCE,
        "origin_scan": DEFAULT_ORIGIN_SCAN_CADENCE,
    }
    env_names = {
        "pipeline_scan": "PRISMATIC_POLL_PIPELINE_SCAN_CADENCE",
        "route_scan": "PRISMATIC_POLL_ROUTE_SCAN_CADENCE",
        "agent_scan": "PRISMATIC_POLL_AGENT_SCAN_CADENCE",
        "recovery_scan": "PRISMATIC_POLL_RECOVERY_SCAN_CADENCE",
        "origin_scan": "PRISMATIC_POLL_ORIGIN_SCAN_CADENCE",
    }
    return max(1, _env_int(env_names[section], defaults[section]))


def _polling_budget_state_path() -> Path:
    explicit = os.environ.get("PRISMATIC_DISPATCHER_POLLING_BUDGET_STATE")
    if explicit:
        return Path(explicit)
    state_dir = Path(
        os.environ.get("PRISMATIC_STATE_DIR", str(Path.cwd() / "prismatic_state"))
    )
    return state_dir / "dispatcher_polling_budget_state.json"


def _persist_polling_budget_status(status: dict[str, Any]) -> None:
    global _LAST_POLL_BUDGET_STATUS
    _LAST_POLL_BUDGET_STATUS = status
    try:
        path = _polling_budget_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(status, indent=2, sort_keys=True))
        tmp.replace(path)
    except Exception as exc:
        print(f"[dispatcher] polling budget status write failed: {exc}")


def get_dispatcher_polling_budget_snapshot() -> dict[str, Any]:
    if _LAST_POLL_BUDGET_STATUS:
        return dict(_LAST_POLL_BUDGET_STATUS)
    path = _polling_budget_state_path()
    try:
        raw = json.loads(path.read_text())
        if isinstance(raw, dict):
            return raw
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    snapshot = get_linear_rate_limit_snapshot()
    return {
        "marker": DISPATCHER_POLLING_BUDGET_MARKER,
        "poll_fallback_enabled": poll_fallback_enabled(),
        "cycle_number": 0,
        "max_calls_per_cycle": poll_max_calls_per_cycle(),
        "calls_used_this_cycle": 0,
        "last_cycle_calls": 0,
        "calls_remaining_this_cycle": poll_max_calls_per_cycle(),
        "calls_by_source": {},
        "cache_hits": 0,
        "cache_misses": 0,
        "skipped_sections": [],
        "last_skip_reason": None,
        "rate_limit_cooldown_active": bool(snapshot.get("cooldown_active")),
        "label_scan_ttl_seconds": label_scan_ttl_seconds(),
        "cadence": {
            "pipeline_scan": scan_cadence("pipeline_scan"),
            "route_scan": scan_cadence("route_scan"),
            "agent_scan": scan_cadence("agent_scan"),
            "recovery_scan": scan_cadence("recovery_scan"),
            "origin_scan": scan_cadence("origin_scan"),
        },
        "non_claims": {
            "webhook_queue_active": False,
            "assigned_agent_event_dispatch": False,
            "linear_mutation_applied": False,
        },
    }


def section_due(section: str, cycle_number: int) -> bool:
    cadence = scan_cadence(section)
    return cadence <= 1 or cycle_number % cadence == 0


def skip_budget_section(section: str, reason: str) -> None:
    if _CURRENT_POLL_BUDGET is not None:
        _CURRENT_POLL_BUDGET.skip(section, reason)
    print(f"[dispatcher] polling budget skip {section}: {reason}")


# ═══════════════════════════════════════════════════════════════
# Linear GraphQL helpers
# ═══════════════════════════════════════════════════════════════


def _linear_api_key() -> str:
    """Get the Linear API key from the environment.

    Raises RuntimeError if ``LINEAR_API_KEY`` is not set.
    """
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        raise RuntimeError("LINEAR_API_KEY environment variable is required")
    return key


def gql(
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    source: str = "dispatcher.gql",
) -> dict[str, Any]:
    """Execute a Linear GraphQL query or mutation with request-count circuit breaking.

    Uses the ``LINEAR_API_KEY`` env var for authentication. No
    ``Bearer`` prefix is added — the raw key value is used directly
    as the HTTP ``Authorization`` header (Linear's API token format).
    """
    import urllib.request
    import urllib.error

    try:
        ensure_linear_circuit_closed(source=source)
    except LinearRateLimitCircuitOpen as exc:
        raise LinearBudgetExhaustedError(str(exc)) from exc

    if _CURRENT_POLL_BUDGET is not None:
        _CURRENT_POLL_BUDGET.consume(source)

    api_key = _linear_api_key()
    payload = json.dumps(
        {
            "query": query,
            "variables": variables or {},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,  # No "Bearer" prefix
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            record_linear_response_headers(resp.headers, source=source)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode(errors="replace")
        try:
            error_payload: Any = json.loads(raw_body)
        except json.JSONDecodeError:
            error_payload = raw_body
        tripped = trip_linear_circuit_from_error(error_payload, source=source)
        if tripped:
            raise LinearBudgetExhaustedError(
                f"Linear API rate-limit circuit open: {tripped.get('reason')}"
            ) from exc
        raise RuntimeError(f"Linear API HTTP {exc.code}: {raw_body[:500]}") from exc
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Linear API request failed: {exc}") from exc

    if "errors" in body:
        tripped = trip_linear_circuit_from_error(body, source=source)
        if tripped:
            raise LinearBudgetExhaustedError(
                f"Linear API rate-limit circuit open: {tripped.get('reason')}"
            )
        raise RuntimeError(
            f"Linear API error(s): {json.dumps(body['errors'], indent=2)[:1000]}"
        )

    return body.get("data", {})


def linear_broad_poll_allowed(*, source: str = "dispatcher.broad_poll") -> bool:
    """Return False when request-count cooldown should skip broad Linear scans."""

    snapshot = get_linear_rate_limit_snapshot()
    if snapshot.get("cooldown_active"):
        print(
            "[dispatcher] Linear rate-limit circuit open; "
            f"skipping broad poll source={source} until {snapshot.get('cooldown_until')} "
            f"reason={snapshot.get('reason')}"
        )
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# Linear Label / Issue helpers
# ═══════════════════════════════════════════════════════════════


def get_label_id(label_name: str, *, team_id: str | None = None) -> str | None:
    """Get (or create) the Linear label ID for *label_name*.

    If the label does not exist in the given team, it is created
    automatically.

    Args:
        label_name: Display name of the label (e.g. ``"agent:fred"``).
        team_id: Team ID override. Falls back to ``TEAM_ID`` constant.

    Returns:
        Label ID string, or ``None`` if lookup/creation failed.
    """
    tid = team_id or TEAM_ID
    if not tid:
        raise RuntimeError(
            "TEAM_ID is not set — provide team_id or set PRISMATIC_TEAM_ID"
        )

    # Look up existing label
    query = """
    query GetTeamLabels($teamId: String!) {
        team(id: $teamId) {
            labels {
                nodes {
                    id
                    name
                }
            }
        }
    }
    """
    data = gql(query, {"teamId": tid})
    labels = data.get("team", {}).get("labels", {}).get("nodes", [])
    for label in labels:
        if label["name"] == label_name:
            return label["id"]

    # Create the label
    create_query = """
    mutation CreateLabel($teamId: String!, $name: String!) {
        issueLabelCreate(input: {teamId: $teamId, name: $name}) {
            issueLabel { id name }
            success
        }
    }
    """
    result = gql(create_query, {"teamId": tid, "name": label_name})
    created = result.get("issueLabelCreate", {}).get("issueLabel")
    if created:
        return created["id"]

    return None


def get_issues_with_label(
    label_name: str,
    *,
    team_id: str | None = None,
    max_issues: int = 20,
) -> list[dict[str, Any]]:
    """Query issues that have a specific label by *label_name*.

    Uses the label label (name-based) approach — iterates all issues
    in the team and filters by label name client-side. Efficient for
    teams with fewer than ~200 active issues.

    Args:
        label_name: Label name to search for.
        team_id: Team ID override.
        max_issues: Maximum issues to return.

    Returns:
        List of issue dicts with keys: ``id``, ``title``, ``description``,
        ``state``, ``assignee``, ``labels``, ``url``.
    """
    tid = team_id or TEAM_ID
    if not tid:
        raise RuntimeError("TEAM_ID is not set")

    cache_key = (tid, label_name, max_issues)
    ttl = label_scan_ttl_seconds()
    now = time.monotonic()
    if ttl > 0:
        cached = _LABEL_SCAN_CACHE.get(cache_key)
        if cached and now - cached[0] <= ttl:
            if _CURRENT_POLL_BUDGET is not None:
                _CURRENT_POLL_BUDGET.cache_hits += 1
            return json.loads(json.dumps(cached[1]))
    if _CURRENT_POLL_BUDGET is not None:
        _CURRENT_POLL_BUDGET.cache_misses += 1

    query = """
    query TeamIssues($teamId: String!, $first: Int!) {
        team(id: $teamId) {
            issues(first: $first, orderBy: updatedAt) {
                nodes {
                    id
                    identifier
                    title
                    description
                    state { name type }
                    assignee { id name }
                    labels { nodes { id name } }
                    url
                }
            }
        }
    }
    """
    data = gql(
        query,
        {"teamId": tid, "first": max_issues},
        source=f"dispatcher.get_issues_with_label:{label_name}",
    )
    issues = data.get("team", {}).get("issues", {}).get("nodes", [])

    results = []
    for issue in issues:
        label_names = [lab["name"] for lab in issue.get("labels", {}).get("nodes", [])]
        if label_name in label_names:
            results.append(
                {
                    "id": issue["id"],
                    "identifier": issue.get("identifier", ""),
                    "title": issue.get("title", ""),
                    "description": issue.get("description", ""),
                    "state": issue.get("state", {}),
                    "assignee": issue.get("assignee"),
                    "labels": label_names,
                    "url": issue.get("url", ""),
                }
            )

    if ttl > 0:
        _LABEL_SCAN_CACHE[cache_key] = (now, json.loads(json.dumps(results)))
    return results


DISPATCH_READY_LABEL = "dispatch:ready"


def issue_label_names(issue: dict[str, Any]) -> list[str]:
    """Return label names from either normalized or raw Linear issue shapes."""
    labels = issue.get("labels", [])
    if isinstance(labels, list):
        names: list[str] = []
        for label in labels:
            if isinstance(label, str):
                names.append(label)
            elif isinstance(label, dict):
                name = label.get("name")
                if name:
                    names.append(str(name))
        return names
    if isinstance(labels, dict):
        return [
            str(label.get("name"))
            for label in labels.get("nodes", [])
            if isinstance(label, dict) and label.get("name")
        ]
    return []


def is_dispatch_ready(issue: dict[str, Any]) -> bool:
    """True only when the issue carries the explicit launch gate label."""
    return DISPATCH_READY_LABEL in issue_label_names(issue)


def report_lane_starvation(
    agent_name: str, candidate_count: int, gated_count: int
) -> None:
    """Emit a visible no-runnable-work signal for an agent lane."""
    label = f"agent:{agent_name}"
    if candidate_count == 0:
        print(
            f"[dispatcher] 🟡 STARVED {label}: no candidate issues found for this lane"
        )
        return
    print(
        f"[dispatcher] 🟡 STARVED {label}: {candidate_count} candidate "
        f"issue(s), {gated_count} missing {DISPATCH_READY_LABEL}; "
        "nothing runnable"
    )


def get_issue_labels(issue_id: str) -> list[dict[str, str]]:
    """Get the current labels on a specific issue.

    Args:
        issue_id: Linear issue UUID.

    Returns:
        List of ``{"id": ..., "name": ...}`` dicts.
    """
    query = """
    query IssueLabels($issueId: String!) {
        issue(id: $issueId) {
            labels { nodes { id name } }
        }
    }
    """
    data = gql(query, {"issueId": issue_id})
    return data.get("issue", {}).get("labels", {}).get("nodes", [])


def set_labels(issue_id: str, label_ids: list[str]) -> bool:
    """Set the exact set of labels on an issue (replaces all existing).

    Args:
        issue_id: Linear issue UUID.
        label_ids: Complete list of label IDs to assign.

    Returns:
        ``True`` on success.
    """
    query = """
    mutation SetLabels($issueId: String!, $labelIds: [String!]!) {
        issueUpdate(id: $issueId, input: {labelIds: {set: $labelIds}}) {
            success
        }
    }
    """
    data = gql(query, {"issueId": issue_id, "labelIds": label_ids})
    return data.get("issueUpdate", {}).get("success", False)


def transition_label(
    issue_id: str,
    remove_label: str,
    add_label: str,
    *,
    team_id: str | None = None,
) -> bool:
    """Remove one label by name and add another.

    This is the core pipeline handoff operation: remove the current
    agent label and add the next agent label.

    Args:
        issue_id: Linear issue UUID.
        remove_label: Name of the label to remove (e.g. ``"agent:fred"``).
        add_label: Name of the label to add (e.g. ``"agent:kai"``).
        team_id: Team ID for label resolution.

    Returns:
        ``True`` if the transition succeeded.
    """
    tid = team_id or TEAM_ID
    current = get_issue_labels(issue_id)
    current_ids = [lab["id"] for lab in current]
    current_names = [lab["name"] for lab in current]

    # Remove the old label
    new_ids = [lab["id"] for lab in current if lab["name"] != remove_label]

    # Add the new label if not already present
    if add_label and add_label not in current_names:
        add_id = get_label_id(add_label, team_id=tid)
        if add_id:
            new_ids.append(add_id)

    # If nothing changed, skip the mutation
    if set(new_ids) == set(current_ids):
        return True

    return set_labels(issue_id, new_ids)


def add_comment(issue_id: str, body: str) -> bool:
    """Post a comment on a Linear issue.

    Args:
        issue_id: Linear issue UUID.
        body: Comment text (Markdown-supported).

    Returns:
        ``True`` if the comment was posted.
    """
    query = """
    mutation AddComment($issueId: String!, $body: String!) {
        commentCreate(input: {issueId: $issueId, body: $body}) {
            success
            comment { id }
        }
    }
    """
    data = gql(query, {"issueId": issue_id, "body": body})
    return data.get("commentCreate", {}).get("success", False)


# ═══════════════════════════════════════════════════════════════
# Durable Launch Records
# ═══════════════════════════════════════════════════════════════


def _launch_records_db_path() -> str:
    """Return the SQLite database path used for durable launch records."""
    return os.environ.get("PRISMATIC_LAUNCH_RECORDS_DB_PATH", LAUNCH_RECORDS_DB_PATH)


def _init_launch_records_table(conn: sqlite3.Connection) -> None:
    """Create the durable launch-record table and indexes if needed."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS launch_records (
            run_id TEXT PRIMARY KEY,
            issue_id TEXT NOT NULL,
            identifier TEXT,
            agent_name TEXT NOT NULL,
            pid INTEGER,
            command_json TEXT NOT NULL,
            handle_type TEXT NOT NULL,
            handle TEXT NOT NULL,
            sandbox_path TEXT,
            worktree_path TEXT,
            branch TEXT,
            execution_context TEXT,
            labels_json TEXT,
            status TEXT NOT NULL DEFAULT 'launched',
            created_at TEXT NOT NULL,
            cycle_id TEXT,
            request_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_launch_records_issue_created
        ON launch_records(issue_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_launch_records_agent_created
        ON launch_records(agent_name, created_at)
        """
    )
    conn.commit()


def _current_git_branch(worktree_path: str) -> str:
    """Best-effort branch discovery for a launch worktree."""
    try:
        result = subprocess.run(
            ["git", "-C", worktree_path, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return os.environ.get("PRISMATIC_BRANCH", "")


def _derive_launch_handle(
    *,
    agent_name: str,
    issue_id: str,
    cmd: list[str],
    worktree_path: str,
    branch: str,
    sandbox_path: str | None = None,
    execution_context: str | None = None,
) -> tuple[str, str, str]:
    """Return ``(handle_type, handle, execution_context)`` for a launch.

    Preference order is explicit sandbox path, existing worktree path, then a
    JSON execution-context fallback. This guarantees every row has a durable,
    non-empty handle even for agents that do not use AGY-style sandboxes.
    """
    context = execution_context or json.dumps(
        {
            "agent": agent_name,
            "issue_id": issue_id,
            "cwd": worktree_path,
            "branch": branch,
            "cmd": cmd,
        },
        sort_keys=True,
    )
    if sandbox_path:
        return "sandbox", sandbox_path, context
    if worktree_path:
        return "worktree", worktree_path, context
    return "execution_context", context, context


def record_launch_record(
    *,
    agent_name: str,
    issue_id: str,
    cmd: list[str],
    pid: int | None = None,
    identifier: str | None = None,
    labels: list[str] | None = None,
    cycle_id: str | None = None,
    request_id: str | None = None,
    sandbox_path: str | None = None,
    worktree_path: str | None = None,
    branch: str | None = None,
    execution_context: str | None = None,
    status: str = "launched",
    db_path: str | None = None,
) -> str:
    """Persist a unique, traceable worker-launch record.

    The returned ``run_id`` is globally unique and can be used to trace the
    process PID, command, issue, branch/worktree, and sandbox/execution handle
    after the dispatcher exits.
    """
    run_id = f"launch-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}"
    created_at = datetime.now(timezone.utc).isoformat()
    wt_path = worktree_path or os.environ.get("PRISMATIC_WORKTREE_PATH") or os.getcwd()
    branch_name = branch if branch is not None else _current_git_branch(wt_path)
    sandbox = sandbox_path or os.environ.get("PRISMATIC_SANDBOX_PATH")
    handle_type, handle, context = _derive_launch_handle(
        agent_name=agent_name,
        issue_id=issue_id,
        cmd=cmd,
        worktree_path=wt_path,
        branch=branch_name,
        sandbox_path=sandbox,
        execution_context=execution_context,
    )
    target_db = db_path or _launch_records_db_path()
    db_dir = os.path.dirname(target_db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with sqlite3.connect(target_db) as conn:
        _init_launch_records_table(conn)
        conn.execute(
            """
            INSERT INTO launch_records (
                run_id, issue_id, identifier, agent_name, pid, command_json,
                handle_type, handle, sandbox_path, worktree_path, branch,
                execution_context, labels_json, status, created_at, cycle_id,
                request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                issue_id,
                identifier or issue_id,
                agent_name,
                pid,
                json.dumps(cmd),
                handle_type,
                handle,
                sandbox,
                wt_path,
                branch_name,
                context,
                json.dumps(labels or []),
                status,
                created_at,
                cycle_id,
                request_id,
            ),
        )
        conn.commit()
    return run_id


# ═══════════════════════════════════════════════════════════════
# Agent Configuration
# ═══════════════════════════════════════════════════════════════

AGENT_CONFIG: dict[str, dict[str, Any]] = {
    "fred": {
        "executable": AGY_PATH,  # fred is a Hermes/AGY instance
        "mode": "signal",
        "timeout": 300,
        "next_label": "agent:kai",
        "description": "Hermes orchestrator — first in pipeline",
    },
    "kai": {
        "executable": "kai",
        "mode": "signal",
        "timeout": 600,
        "next_label": "agent:agy",
        "description": "Active Oahu Tours bot — review & deploy",
    },
    "agy": {
        "executable": AGY_PATH,
        "mode": "launch",
        "timeout": 900,
        "next_label": "agent:jules",
        "description": "Antigravity CLI — code generation",
    },
    "george": {
        "executable": "hermes --profile george",
        "mode": "visible_hermes",
        "timeout": 600,
        "next_label": "",
        "description": "Prismatic workflow/dashboard verification guard",
    },
    "jules": {
        "executable": JULES_PATH,
        "mode": "launch",
        "timeout": 600,
        "next_label": "agent:codex",
        "description": "Jules CLI — testing & QA",
    },
    "codex": {
        "executable": CODEX_PATH,
        "mode": "launch",
        "timeout": 1200,
        "next_label": "",  # terminal — pipeline complete
        "description": "Codex CLI — final polish & PR",
    },
}


# Jules host-path pre-screen: Jules sessions cannot safely inspect host-only
# paths such as the operator home directory or systemd state. Route those bounded ops to Ned
# before launch so capacity is not consumed by an impossible Jules task.
HOME_UBUNTU_MARKER = "/home/" + "ubuntu"
_HOST_LEVEL_PATTERNS = [
    HOME_UBUNTU_MARKER,
    "~/.config",
    "/etc",
    "systemd",
    "crontab",
]


def detect_host_level_patterns(issue: dict[str, Any]) -> list[str]:
    text = f"{issue.get('title') or ''}\n{issue.get('description') or ''}".lower()
    matches: list[str] = []
    for pattern in _HOST_LEVEL_PATTERNS:
        if pattern.lower() in text:
            matches.append(pattern)
    return matches


def reroute_jules_host_path_issue(issue: dict[str, Any], matches: list[str]) -> bool:
    issue_id = str(issue.get("id") or "")
    identifier = str(issue.get("identifier") or issue_id)
    if not issue_id:
        return False
    labels = get_issue_labels(issue_id)
    existing = [label for label in labels if label.get("name") != "agent:jules"]
    ned_label = get_label_id("agent:ned")
    if not ned_label:
        return False
    label_ids: list[str] = []
    for label in existing:
        label_id = label.get("id")
        if label_id:
            label_ids.append(label_id)
    if ned_label not in label_ids:
        label_ids.append(ned_label)
    if not set_labels(issue_id, label_ids):
        return False
    safe_matches = ", ".join(str(match)[:80] for match in matches[:8])
    add_comment(
        issue_id,
        "Jules host-path pre-screen: rerouted "
        f"{identifier} from agent:jules to agent:ned because Jules cannot safely access host-level paths/patterns: "
        f"{safe_matches}. Added agent:ned; preserved other labels.",
    )
    return True


# ═══════════════════════════════════════════════════════════════
# Agent Launchers
# ═══════════════════════════════════════════════════════════════

# Default signal provider instance — file-based, writing to NUDGE_DIR
_signal_provider: Any = None


def _get_signal_provider():
    """Lazy-init the default file-based signal provider."""
    global _signal_provider
    if _signal_provider is None:
        _signal_provider = create_signal_provider(
            {
                "type": "file",
                "directory": NUDGE_DIR,
            }
        )
    return _signal_provider


def signal_fred(issue_id: str, title: str = "", priority: int = 3) -> bool:
    """Signal agent:fred by writing a nudge file.

    Args:
        issue_id: Linear issue identifier/ID.
        title: Human-readable summary.
        priority: Signal priority (0-5).

    Returns:
        ``True`` if the signal was written.
    """
    provider = _get_signal_provider()
    return provider.send_work(
        target="fred",
        issue_id=issue_id,
        title=title or f"Work on {issue_id}",
        priority=priority,
    )


def signal_george(issue_id: str, title: str = "", priority: int = 3) -> bool:
    """Signal agent:george by writing a nudge file as fallback for visible Hermes execution."""
    provider = _get_signal_provider()
    return provider.send_work(
        target="george",
        issue_id=issue_id,
        title=title or f"Verify {issue_id}",
        priority=priority,
    )


def signal_kai(
    issue_id: str,
    title: str = "",
    priority: int = 3,
    signal_type: str = "",
) -> bool:
    """Signal agent:kai by writing a nudge file.

    Args:
        issue_id: Linear issue identifier/ID.
        title: Human-readable summary.
        priority: Signal priority (0-5).
        signal_type: Optional signal classification
            (e.g. ``"agy_review_complete"``).

    Returns:
        ``True`` if the signal was written.
    """
    provider = _get_signal_provider()
    result = provider.send_work(
        target="kai",
        issue_id=issue_id,
        title=title or f"Work on {issue_id}",
        priority=priority,
        signal_type=signal_type,
    )
    # ── Telemetry: record Kai orchestrator dispatch ───────────
    if result:
        try:
            import uuid

            collector = get_collector()
            status = "dispatched" if signal_type != "agy_review_complete" else "review"
            collector.record_agent_run(
                run_id=f"kai-orch-{uuid.uuid4().hex[:8]}",
                agent="kai",
                issue_id=issue_id,
                provider=AGENT_PROVIDER_MAP.get("kai", ""),
                status=status,
                credits_spent=0,
            )
        except Exception:
            pass  # Telemetry is best-effort
    # ── End telemetry ─────────────────────────────────────────
    return result


def _visible_agent_stream_message(
    *,
    agent: str,
    issue_id: str,
    status: str,
    title: str = "",
    run_id: str = "",
    reason: str = "",
) -> str:
    lines = [
        f"🌊 Prismatic assigned-agent stream: {status}",
        "",
        f"agent={agent}",
        f"issue={issue_id}",
    ]
    if title:
        lines.append(f"title={title}")
    if run_id:
        lines.append(f"run_id={run_id}")
    if reason:
        lines.append(f"reason={reason}")
    lines.extend(
        [
            "",
            "This is the Telegram-visible cockpit layer over the durable Linear queue.",
            "Linear remains source of truth; final RESULT/MARKER packet still writes back separately.",
        ]
    )
    return "\n".join(lines)


def emit_visible_agent_stream_event(
    agent: str,
    issue_id: str,
    status: str,
    *,
    title: str = "",
    run_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Send a Telegram-visible wake/execute/sleep breadcrumb for assigned-agent dispatch.

    This is intentionally best-effort: it restores operator visibility without
    making Telegram delivery a hard dependency for the durable queue. Set
    PRISMATIC_VISIBLE_AGENT_STREAM=0 to disable, PRISMATIC_VISIBLE_WAKE_DRY_RUN=1
    for tests, and PRISMATIC_VISIBLE_WAKE_LOG=/path to capture emitted messages.
    """
    if os.environ.get("PRISMATIC_VISIBLE_AGENT_STREAM", "1") in {
        "0",
        "false",
        "False",
        "no",
    }:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    message = _visible_agent_stream_message(
        agent=agent,
        issue_id=issue_id,
        status=status,
        title=title,
        run_id=run_id,
        reason=reason,
    )
    try:
        from prismatic.agent_signal_stream import record_agent_signal

        severity = (
            "error"
            if "FAILED" in status
            else "warning"
            if "BLOCKED" in status
            else "success"
            if status in {"WAKE_DISPATCHED", "EXECUTION_STARTED"}
            else "info"
        )
        record_agent_signal(
            agent=agent,
            event_type=status,
            issue_id=issue_id,
            status=status,
            message=message,
            run_id=run_id,
            source="dispatcher-visible-stream",
            severity=severity,
            log_path=reason if str(reason).startswith("/") else "",
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
    subject = f"[Prismatic] {agent} {status} {issue_id}"
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
                subject,
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
        "stdout": (proc.stdout or "")[-500:],
        "stderr": (proc.stderr or "")[-500:],
        "target": target,
        "profile": profile,
    }


def _assigned_agent_execution_prompt(
    agent: str, issue_id: str, title: str = "", run_id: str = ""
) -> str:
    return "\n".join(
        [
            f"You are {agent.upper()} working one Prismatic Engine Linear task: {issue_id}.",
            "",
            f"Title: {title or issue_id}",
            f"Assigned run id: {run_id}",
            "",
            "Operate through the durable Linear/source-of-truth lane, but keep the user-facing output compact.",
            "Use tools to inspect the issue/repo before editing. If blocked, return a real BLOCKED packet.",
            "",
            "Required final compact packet:",
            "COMMAND=<main command(s) or action taken>",
            "RESULT=<PASS|BLOCKED|FAIL>",
            "LOG=<path to detailed log/artifact>",
            "SCOPE=<files/features verified>",
            "AD_HOC_OR_CANONICAL=<ad-hoc targeted|canonical suite>",
            "NOT_CLAIMING=<explicit non-claims>",
            "MARKER=<ISSUE_SPECIFIC_OK_OR_BLOCKED>",
            "",
            "Do not claim Prompt4 green, Prompt5 unlocked, production deployed, or canonical suite green unless actually verified.",
        ]
    )


def launch_visible_hermes_agent(
    agent: str,
    issue_id: str,
    *,
    title: str = "",
    labels: list[str] | None = None,
    identifier: str | None = None,
    cycle_id: str | None = None,
    request_id: str | None = None,
    run_id: str = "",
) -> subprocess.Popen | None:
    """Launch Fred/Kai as actual Hermes profile executions with durable logs.

    This is the hybrid path: Fred/Kai get a real Hermes execution instead of only
    a stale file nudge, while Telegram/dashboard breadcrumbs and Linear writeback
    remain separate. Set PRISMATIC_VISIBLE_HERMES_EXECUTION=0 to fall back to the
    old signal provider.
    """
    if os.environ.get("PRISMATIC_VISIBLE_HERMES_EXECUTION", "1") in {
        "0",
        "false",
        "False",
        "no",
    }:
        return None
    hermes = shutil.which(os.environ.get("PRISMATIC_HERMES_BIN", "hermes"))
    if not hermes:
        return None
    profile = os.environ.get(f"PRISMATIC_{agent.upper()}_HERMES_PROFILE", agent)
    log_dir = Path(
        os.environ.get("PRISMATIC_AGENT_RUN_LOG_DIR", "/tmp/prismatic-agent-runs")
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_issue = re.sub(r"[^A-Za-z0-9_.-]+", "-", identifier or issue_id or "task")[:80]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"hermes-{agent}-{safe_issue}-{ts}.log"
    prompt = _assigned_agent_execution_prompt(
        agent, identifier or issue_id, title=title, run_id=run_id
    )
    cmd = [hermes, "--profile", profile, "-z", prompt]
    try:
        emit_visible_agent_stream_event(
            agent,
            identifier or issue_id,
            "EXECUTION_STARTED",
            title=title,
            run_id=run_id,
            reason=str(log_path),
        )
        from prismatic.agent_signal_stream import record_agent_signal

        record_agent_signal(
            agent=agent,
            event_type="EXECUTION_STARTED",
            issue_id=identifier or issue_id,
            status="running",
            message=f"Hermes visible execution started for {identifier or issue_id}",
            run_id=run_id,
            source="dispatcher",
            severity="success",
            log_path=str(log_path),
        )
        out_handle = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdout=out_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        launch_record_id = record_launch_record(
            agent_name=agent,
            issue_id=issue_id,
            identifier=identifier or issue_id,
            cmd=cmd + ["--log-file", str(log_path)],
            pid=proc.pid,
            labels=labels,
            cycle_id=cycle_id,
            request_id=request_id,
        )
        _emit_agent_event(
            "agent_launched", agent, issue_id, pid=proc.pid, run_id=launch_record_id
        )
        return proc
    except (OSError, subprocess.SubprocessError) as exc:
        emit_visible_agent_stream_event(
            agent,
            identifier or issue_id,
            "EXECUTION_FAILED",
            title=title,
            run_id=run_id,
            reason=str(exc),
        )
        return None


AGY_SHARED_SKILL_PACKS = [
    "shared/prismatic-completed-work-contract",
    "shared/prismatic-proof-packet",
    "shared/prismatic-non-claims",
    "shared/prismatic-safe-file-scope",
]

AGY_AGENT_SKILL_PACKS = [
    "agy/agy-structured-result-packet",
    "agy/agy-one-task-scope",
    "agy/agy-dashboard-work",
    "agy/agy-model-preflight",
]


def _redact_agent_context_text(value: str) -> str:
    """Best-effort redaction before writing work-packet context files."""
    redacted = re.sub(
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s`'\"]+",
        r"\1=[REDACTED]",
        value,
    )
    redacted = re.sub(r"ghp_[A-Za-z0-9_]{20,}", "[REDACTED_GITHUB_TOKEN]", redacted)
    redacted = re.sub(
        r"github_pat_[A-Za-z0-9_]{20,}", "[REDACTED_GITHUB_TOKEN]", redacted
    )
    redacted = re.sub(
        r"xox[baprs]-[A-Za-z0-9-]{10,}", "[REDACTED_SLACK_TOKEN]", redacted
    )
    redacted = re.sub(r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]", redacted)
    return redacted


def _write_agy_context_pack(
    *,
    context_dir: Path,
    issue_id: str,
    identifier: str,
    title_or_task: str,
    expected_marker: str,
    blocked_marker: str,
    labels: list[str] | None,
    worktree_path: str,
    log_path: Path,
) -> dict[str, str]:
    """Write Kai/Michael-style work packet files for AGY print-mode launches.

    Keep the CLI prompt small and put durable workflow memory in files. AGY is
    instructed to read the work packet, then emit the same compact completed-work
    packet contract Fred documented for future agent lanes.
    """
    context_dir.mkdir(parents=True, exist_ok=True)
    safe_title = _redact_agent_context_text(title_or_task or identifier or issue_id)
    safe_labels = [_redact_agent_context_text(str(label)) for label in (labels or [])]
    shared_packs = ",".join(AGY_SHARED_SKILL_PACKS)
    agent_packs = ",".join(AGY_AGENT_SKILL_PACKS)

    work_packet = f"""# AGY Work Packet — {identifier}

Status marker: `AGY_CLI_CONTEXT_PACK_OK`

## Assignment

| Field | Value |
|---|---|
| agent | `agy` |
| issue_id | `{issue_id}` |
| identifier | `{identifier}` |
| title_or_task | `{safe_title}` |
| worktree_path | `{worktree_path}` |
| output_log | `{log_path}` |

## Labels

```text
{chr(10).join(safe_labels) if safe_labels else "none_provided"}
```

## Scope rules

1. Work only this assignment.
2. Do not launch other agents.
3. Do not enable auto-merge.
4. Do not deploy production.
5. Do not create a real GitHub PR unless this exact packet explicitly says it is authorized.
6. Keep detailed command/test output in a log or artifact file; stdout must end with the compact packet.

## Required final compact output

```text
skill_pack_state=loaded
shared_skill_packs={shared_packs}
agent_skill_packs={agent_packs}
packet_contract_version=prismatic-completed-work-v1
packet_validation=passed
COMMAND=<exact command/proof you ran or observation-only proof>
RESULT=<PASS|BLOCKED|FAIL>
LOG=<path or summary>
SCOPE=<what you verified>
AD_HOC_OR_CANONICAL=<ad-hoc targeted|canonical suite>
NOT_CLAIMING=<explicit non-claims>
MARKER={expected_marker}
```

If blocked, use `MARKER={blocked_marker}` and include a concrete blocker.
"""

    packet_contract = f"""# AGY Packet Contract

This is the standardized Prismatic completed-work output contract. It is the
same contract used by Fred/George/Kai review lanes so AGY output can flow into
completed-work ingestion, dashboard status, Linear writeback, raw-output repair,
and future merge/review gates without bespoke parsing.

## Skill packs represented in this packet

```text
{chr(10).join([*AGY_SHARED_SKILL_PACKS, *AGY_AGENT_SKILL_PACKS])}
```

## Non-claims to include unless explicitly proven and authorized

```text
auto_merge_enabled
production_deploy
real_github_pr_created
live_Linear_mutations_without_approval
bulk_agy_dispatch
canonical_full_suite_green
```
"""

    context_pack = f"""# AGY CLI Context Pack

Read these files before working:

1. `WORK_PACKET.md` — assignment, scope, proof, marker, and output contract.
2. `PACKET_CONTRACT.md` — standardized output/non-claims contract.

## Launch optimization

The dispatcher intentionally keeps the `agy --print` prompt tiny and stores
workflow memory here on disk. This reduces prompt bloat while preserving the
standardized work-packet theory Michael/Kai have been using: durable context in
files, compact packet on stdout, infrastructure fallback when output is malformed.

## Expected marker

```text
{expected_marker}
```

## Blocked marker

```text
{blocked_marker}
```
"""

    files = {
        "work_packet": context_dir / "WORK_PACKET.md",
        "packet_contract": context_dir / "PACKET_CONTRACT.md",
        "context_pack": context_dir / "CONTEXT_PACK.md",
    }
    files["work_packet"].write_text(work_packet, encoding="utf-8")
    files["packet_contract"].write_text(packet_contract, encoding="utf-8")
    files["context_pack"].write_text(context_pack, encoding="utf-8")
    return {key: str(path) for key, path in files.items()}


def launch_agy(
    issue_id: str,
    task: str = "",
    labels: list[str] | None = None,
    title: str = "",
    identifier: str | None = None,
    cycle_id: str | None = None,
    request_id: str | None = None,
) -> subprocess.Popen | None:
    """Launch the AGY CLI in headless mode for the given issue.

    The AGY process is started as a background subprocess with the
    issue ID passed via the ``--issue`` flag.  If the issue has an
    AGY model-specific label (e.g. ``agent:agy-sonnet``), the
    corresponding ``--model`` flag is added automatically.

    Before launching, the :class:`CircuitBreakerRouter` checks live
    telemetry for cooldown timers and quota exhaustion signals.  If
    the circuit is open, the ``--model`` flag is rewritten to use a
    fallback model from the priority chain.

    Args:
        issue_id: Linear issue UUID or identifier.
        task: Optional task description.
        labels: Optional list of label names from the Linear issue.
            If not provided, labels are fetched from the Linear API.

    Returns:
        ``subprocess.Popen`` handle, or ``None`` if launch failed.
    """
    try:
        from prismatic.providers.github import GitHubProvider

        github = GitHubProvider()
        if hasattr(github, "has_credentials") and not github.has_credentials():
            add_comment(
                issue_id,
                "AGY dispatch blocked: GitHub API credentials are missing. Configure GitHub auth before launching AGY/Jules workflow.",
            )
            return None
    except Exception:
        pass

    resolved_agy_path = AGY_PATH if os.path.isabs(AGY_PATH) else shutil.which(AGY_PATH)
    if not resolved_agy_path or not os.path.exists(resolved_agy_path):
        print(f"[dispatcher] AGY binary not found at {AGY_PATH}")
        return None

    try:
        if not task and title:
            task = title
        expected_marker = (
            "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK"
            if (identifier or issue_id) == "GRO-3954"
            else f"AGY_ASSIGNED_AGENT_{re.sub(r'[^A-Za-z0-9]+', '_', identifier or issue_id).upper()}_OK"
        )
        blocked_marker = (
            "AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED"
            if (identifier or issue_id) == "GRO-3954"
            else f"AGY_ASSIGNED_AGENT_{re.sub(r'[^A-Za-z0-9]+', '_', identifier or issue_id).upper()}_BLOCKED"
        )
        run_log_dir = Path(
            os.environ.get("PRISMATIC_AGENT_RUN_LOG_DIR", "/tmp/prismatic-agent-runs")
        )
        run_log_dir.mkdir(parents=True, exist_ok=True)
        log_token = re.sub(r"[^A-Za-z0-9_.-]+", "-", identifier or issue_id)[:80]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = run_log_dir / f"agy-{log_token}-{timestamp}.log"
        worktree_path = os.environ.get("PRISMATIC_WORKTREE_PATH") or os.getcwd()
        context_dir = run_log_dir / f"agy-{log_token}-{timestamp}-context"
        context_files = _write_agy_context_pack(
            context_dir=context_dir,
            issue_id=issue_id,
            identifier=identifier or issue_id,
            title_or_task=task or title or identifier or issue_id,
            expected_marker=expected_marker,
            blocked_marker=blocked_marker,
            labels=labels,
            worktree_path=worktree_path,
            log_path=log_path,
        )
        prompt = (
            f"You are AGY working one Prismatic Engine task: {identifier or issue_id}.\n"
            f"Read this context pack first: {context_files['context_pack']}\n"
            f"Then follow this work packet exactly: {context_files['work_packet']}\n"
            "Keep stdout compact. Finish with the exact standardized completed-work packet lines from WORK_PACKET.md.\n"
            f"Expected success marker: {expected_marker}. Blocked marker: {blocked_marker}.\n"
        )
        cmd = [
            resolved_agy_path,
            "--print",
            prompt,
            "--dangerously-skip-permissions",
            "--print-timeout",
            os.environ.get("PRISMATIC_AGY_PRINT_TIMEOUT", "45m0s"),
            "--add-dir",
            worktree_path,
            "--add-dir",
            str(context_dir),
            "--log-file",
            str(log_path),
        ]

        # ── Resolve issue labels if not provided ────────────────
        if labels is None:
            try:
                label_objs = get_issue_labels(issue_id)
                labels = [lab["name"] for lab in label_objs]
            except Exception:
                labels = []

        # ── AGY model routing: inject --model flag ──────────────
        model = get_agy_model_from_labels(labels)
        if model:
            # Check for --model already in cmd (from circuit breaker or other)
            existing_model = None
            for i, arg in enumerate(cmd):
                if arg == "--model" and i + 1 < len(cmd):
                    existing_model = cmd[i + 1]
                    break
            if not existing_model:
                cmd.extend(["--model", model])
                print(f"[dispatcher] AGY model routing: {issue_id} → model={model}")

        # ── Circuit breaker: check live telemetry for quota/cooldown ──
        try:
            from prismatic.core.router import check_and_route_agy

            cmd, cb_state = check_and_route_agy(issue_id, cmd)
            if cb_state.fallback_applied:
                print(
                    f"[dispatcher] Circuit breaker: {issue_id} → "
                    f"model={cb_state.recommended_model} "
                    f"(reason: {cb_state.fallback_reason})"
                )
        except Exception as exc:
            # Circuit breaker failure is non-fatal — launch with defaults
            print(f"[dispatcher] Circuit breaker check failed: {exc}")

        if os.environ.get("PRISMATIC_AGY_FORCE_PACKET_WRAPPER", "1") != "0":
            # AGY's --log-file can contain internal/session logs instead of the
            # user-facing --print result. Run through a small shell wrapper so
            # stdout/stderr are always appended to the same durable output log,
            # and append a conservative BLOCKED packet if AGY exits without the
            # exact compact completed-work packet required by the reconciler.
            quoted_cmd = " ".join(shlex.quote(part) for part in cmd)
            expected_re = shlex.quote(f"^MARKER={expected_marker}$")
            blocked_re = shlex.quote(f"^MARKER={blocked_marker}$")
            wrapper = "\n".join(
                [
                    "set +e",
                    "printf '%s\\n' 'AGY_OUTPUT_CAPTURE_WRAPPER_STARTED' >> \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    f"printf '%s\\n' 'context_pack_path={context_files['context_pack']}' >> \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    f"printf '%s\\n' 'work_packet_path={context_files['work_packet']}' >> \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    "printf '%s\\n' 'skill_pack_state=loaded' >> \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    f"printf '%s\\n' 'shared_skill_packs={','.join(AGY_SHARED_SKILL_PACKS)}' >> \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    f"printf '%s\\n' 'agent_skill_packs={','.join(AGY_AGENT_SKILL_PACKS)}' >> \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    "printf '%s\\n' 'packet_contract_version=prismatic-completed-work-v1' >> \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    f'{quoted_cmd} >> "$PRISMATIC_AGY_OUTPUT_LOG" 2>&1',
                    "rc=$?",
                    f'if ! grep -Eq \'^(RESULT=(PASS|BLOCKED|FAIL))$\' "$PRISMATIC_AGY_OUTPUT_LOG" || (! grep -Eq {expected_re} "$PRISMATIC_AGY_OUTPUT_LOG" && ! grep -Eq {blocked_re} "$PRISMATIC_AGY_OUTPUT_LOG"); then',
                    "  {",
                    "    printf '%s\\n' 'COMMAND=agy --print <prompt> --print-timeout --add-dir --log-file'",
                    "    printf '%s\\n' 'RESULT=BLOCKED'",
                    "    printf 'LOG=%s\\n' \"$PRISMATIC_AGY_OUTPUT_LOG\"",
                    "    printf '%s\\n' 'SCOPE=AGY print-mode completed-work packet capture'",
                    "    printf '%s\\n' 'AD_HOC_OR_CANONICAL=ad-hoc targeted'",
                    "    printf '%s\\n' 'NOT_CLAIMING=AGY task completed,Prompt4 green,Prompt5 unlocked,production deployed,canonical suite green,auto_merge_enabled'",
                    f"    printf '%s\\n' 'MARKER={blocked_marker}'",
                    "    printf '%s\\n' ''",
                    "    printf '%s\\n' 'AGY output capture wrapper appended this conservative blocker because AGY exited without exact completed-work packet lines.'",
                    '  } >> "$PRISMATIC_AGY_OUTPUT_LOG"',
                    "fi",
                    "exit $rc",
                ]
            )
            cmd = [
                "env",
                f"PRISMATIC_AGY_OUTPUT_LOG={log_path}",
                "bash",
                "-lc",
                wrapper,
            ]

        launch_cmd = cmd
        # When the dispatcher is run by the one-shot webhook drain service,
        # child processes left in that systemd cgroup can be SIGTERM'd as soon
        # as the drainer exits. Start AGY in a user transient scope when
        # available so the actual agent execution survives the drain process.
        if os.environ.get(
            "PRISMATIC_AGY_USE_SYSTEMD_SCOPE", "1"
        ) != "0" and shutil.which("systemd-run"):
            unit_token = f"prismatic-agy-{log_token}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            launch_cmd = [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                "--unit",
                unit_token,
                *cmd,
            ]
        out_handle = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            launch_cmd,
            stdout=out_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        run_id = record_launch_record(
            agent_name="agy",
            issue_id=issue_id,
            identifier=identifier or issue_id,
            cmd=launch_cmd,
            pid=proc.pid,
            labels=labels,
            cycle_id=cycle_id,
            request_id=request_id,
            execution_context=json.dumps(
                {
                    "agent": "agy",
                    "issue_id": issue_id,
                    "identifier": identifier or issue_id,
                    "context_pack_dir": str(context_dir),
                    "context_pack": context_files,
                    "output_log": str(log_path),
                    "worktree_path": worktree_path,
                    "expected_marker": expected_marker,
                    "blocked_marker": blocked_marker,
                    "marker": "AGY_CLI_CONTEXT_PACK_OK",
                },
                sort_keys=True,
            ),
        )
        print(f"[dispatcher] Launched AGY (pid={proc.pid}) for issue {issue_id}")
        _emit_agent_event(
            "agent_launched", "agy", issue_id, pid=proc.pid, run_id=run_id
        )
        return proc
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[dispatcher] Failed to launch AGY: {exc}")
        return None


JULES_SHARED_SKILL_PACKS = [
    "shared/prismatic-completed-work-contract",
    "shared/prismatic-proof-packet",
    "shared/prismatic-non-claims",
    "shared/prismatic-safe-file-scope",
]

JULES_AGENT_SKILL_PACKS = [
    "jules/jules-session-result-packet",
    "jules/jules-bounded-review-scope",
    "jules/jules-session-handle-capture",
]


def _write_jules_context_pack(
    *,
    context_dir: Path,
    issue_id: str,
    identifier: str,
    title_or_task: str,
    expected_marker: str,
    blocked_marker: str,
    labels: list[str] | None,
    worktree_path: str,
    log_path: Path,
) -> dict[str, str]:
    """Write durable Jules context files for async ``jules new`` sessions."""
    context_dir.mkdir(parents=True, exist_ok=True)
    safe_title = _redact_agent_context_text(title_or_task or identifier or issue_id)
    safe_labels = [_redact_agent_context_text(str(label)) for label in (labels or [])]
    shared_packs = ",".join(JULES_SHARED_SKILL_PACKS)
    agent_packs = ",".join(JULES_AGENT_SKILL_PACKS)

    work_packet = f"""# Jules Work Packet — {identifier}

Status marker: `JULES_CLI_SESSION_CONTEXT_PACK_OK`

## Assignment

| Field | Value |
|---|---|
| agent | `jules` |
| issue_id | `{issue_id}` |
| identifier | `{identifier}` |
| title_or_task | `{safe_title}` |
| worktree_path | `{worktree_path}` |
| session_capture_log | `{log_path}` |

## Labels

```text
{chr(10).join(safe_labels) if safe_labels else "none_provided"}
```

## Jules scope rules

1. Treat this as one bounded Jules review/test/QA session.
2. Do not launch other agents.
3. Do not enable auto-merge or deploy production.
4. Do not create or merge real GitHub PRs unless explicitly authorized in this packet.
5. Keep noisy detail in Jules session output or artifacts; final pulled results must normalize into the compact packet below.

## Required normalized result packet

```text
skill_pack_state=loaded
shared_skill_packs={shared_packs}
agent_skill_packs={agent_packs}
packet_contract_version=prismatic-completed-work-v1
packet_validation=passed
COMMAND=<jules new / remote pull command or observation proof>
RESULT=<PASS|BLOCKED|FAIL>
LOG=<Jules session log/result path>
SCOPE=<what Jules reviewed/tested>
AD_HOC_OR_CANONICAL=<ad-hoc targeted|canonical suite>
NOT_CLAIMING=<explicit non-claims>
MARKER={expected_marker}
```

If blocked, use `MARKER={blocked_marker}` with the concrete blocker.
"""

    packet_contract = f"""# Jules Packet Contract

Jules is async/session-based on this host. Dispatch must use `jules new`, store
the session capture log/handle, and later reconcile `jules remote pull` output
into the same Prismatic completed-work packet contract used by AGY/Fred/George/Kai.

## Skill packs represented

```text
{chr(10).join([*JULES_SHARED_SKILL_PACKS, *JULES_AGENT_SKILL_PACKS])}
```

## Non-claims to include unless explicitly proven and authorized

```text
auto_merge_enabled
production_deploy
real_github_pr_created
live_Linear_mutations_without_approval
bulk_jules_dispatch
canonical_full_suite_green
```
"""

    context_pack = f"""# Jules CLI Context Pack

Read these files before working:

1. `WORK_PACKET.md` — assignment, bounded scope, markers, and result packet shape.
2. `PACKET_CONTRACT.md` — standardized output/non-claims contract.

## Launch optimization

The dispatcher intentionally uses the installed Jules CLI shape:

```text
jules new <compact prompt>
```

It does not use unsupported AGY-style flags such as `--issue`, `--task`,
`--print`, `--log-file`, `--add-dir`, or `--model`. Durable workflow memory
lives in this context directory; the Jules session handle/output is captured in
the launch log for later reconciliation.

## Expected marker

```text
{expected_marker}
```

## Blocked marker

```text
{blocked_marker}
```
"""

    files = {
        "work_packet": context_dir / "WORK_PACKET.md",
        "packet_contract": context_dir / "PACKET_CONTRACT.md",
        "context_pack": context_dir / "CONTEXT_PACK.md",
    }
    files["work_packet"].write_text(work_packet, encoding="utf-8")
    files["packet_contract"].write_text(packet_contract, encoding="utf-8")
    files["context_pack"].write_text(context_pack, encoding="utf-8")
    return {key: str(path) for key, path in files.items()}


def launch_jules(
    issue_id: str,
    task: str = "",
    title: str = "",
    identifier: str | None = None,
    labels: list[str] | None = None,
    cycle_id: str | None = None,
    request_id: str | None = None,
) -> subprocess.Popen | None:
    """Launch an async Jules CLI session for the given issue.

    Jules on this host uses ``jules new`` session creation rather than AGY-style
    ``--issue``/``--task`` flags. The dispatcher therefore writes durable context
    files, launches a compact prompt, captures the session output log, and stores
    enough metadata for a later ``jules remote pull`` reconciliation step.
    """
    resolved_jules_path = (
        JULES_PATH if os.path.isabs(JULES_PATH) else shutil.which(JULES_PATH)
    )
    if not resolved_jules_path or not os.path.exists(resolved_jules_path):
        print(f"[dispatcher] Jules binary not found at {JULES_PATH}")
        return None

    try:
        if not task and title:
            task = title
        expected_marker = f"JULES_ASSIGNED_AGENT_{re.sub(r'[^A-Za-z0-9]+', '_', identifier or issue_id).upper()}_OK"
        blocked_marker = f"JULES_ASSIGNED_AGENT_{re.sub(r'[^A-Za-z0-9]+', '_', identifier or issue_id).upper()}_BLOCKED"
        run_log_dir = Path(
            os.environ.get("PRISMATIC_AGENT_RUN_LOG_DIR", "/tmp/prismatic-agent-runs")
        )
        run_log_dir.mkdir(parents=True, exist_ok=True)
        log_token = re.sub(r"[^A-Za-z0-9_.-]+", "-", identifier or issue_id)[:80]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = run_log_dir / f"jules-{log_token}-{timestamp}.log"
        worktree_path = os.environ.get("PRISMATIC_WORKTREE_PATH") or os.getcwd()
        context_dir = run_log_dir / f"jules-{log_token}-{timestamp}-context"
        context_files = _write_jules_context_pack(
            context_dir=context_dir,
            issue_id=issue_id,
            identifier=identifier or issue_id,
            title_or_task=task or title or identifier or issue_id,
            expected_marker=expected_marker,
            blocked_marker=blocked_marker,
            labels=labels,
            worktree_path=worktree_path,
            log_path=log_path,
        )
        prompt = (
            f"You are Jules working one bounded Prismatic Engine review/test task: {identifier or issue_id}.\n"
            f"Read this context pack first: {context_files['context_pack']}\n"
            f"Then follow this work packet exactly: {context_files['work_packet']}\n"
            "When your session result is pulled, it must normalize into the compact completed-work packet in WORK_PACKET.md.\n"
            f"Expected success marker: {expected_marker}. Blocked marker: {blocked_marker}.\n"
        )
        cmd = [resolved_jules_path, "new", prompt]
        jules_repo = os.environ.get("PRISMATIC_JULES_REPO")
        if jules_repo:
            cmd = [resolved_jules_path, "new", "--repo", jules_repo, prompt]

        from prismatic.jules_capacity import record_jules_launch

        stable_launch_identity = request_id or (
            f"jules:{identifier or issue_id}:{cycle_id}" if cycle_id else None
        )
        capacity_launch_key = record_jules_launch(
            issue_id=identifier or issue_id,
            repository=os.environ.get("PRISMATIC_JULES_REPO") or worktree_path,
            source_path=str(log_path),
            launch_identity=stable_launch_identity,
            request_id=request_id,
            lifecycle_status="accepted",
        )
        out_handle = open(log_path, "a", encoding="utf-8")
        out_handle.write("JULES_SESSION_CAPTURE_STARTED\n")
        out_handle.write(f"context_pack_path={context_files['context_pack']}\n")
        out_handle.write(f"work_packet_path={context_files['work_packet']}\n")
        out_handle.write(f"capacity_launch_key={capacity_launch_key}\n")
        out_handle.write("skill_pack_state=loaded\n")
        out_handle.write(f"shared_skill_packs={','.join(JULES_SHARED_SKILL_PACKS)}\n")
        out_handle.write(f"agent_skill_packs={','.join(JULES_AGENT_SKILL_PACKS)}\n")
        out_handle.write("packet_contract_version=prismatic-completed-work-v1\n")
        out_handle.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=out_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=worktree_path,
            start_new_session=True,
        )
        run_id = record_launch_record(
            agent_name="jules",
            issue_id=issue_id,
            identifier=identifier or issue_id,
            cmd=cmd,
            pid=proc.pid,
            labels=labels,
            cycle_id=cycle_id,
            request_id=request_id,
            execution_context=json.dumps(
                {
                    "agent": "jules",
                    "issue_id": issue_id,
                    "identifier": identifier or issue_id,
                    "context_pack_dir": str(context_dir),
                    "context_pack": context_files,
                    "capacity_launch_key": capacity_launch_key,
                    "session_capture_log": str(log_path),
                    "worktree_path": worktree_path,
                    "expected_marker": expected_marker,
                    "blocked_marker": blocked_marker,
                    "reconcile_hint": "jules remote list --session && jules remote pull --session <session_id>",
                    "marker": "JULES_CLI_SESSION_CONTEXT_PACK_OK",
                },
                sort_keys=True,
            ),
        )
        print(f"[dispatcher] Launched Jules (pid={proc.pid}) for issue {issue_id}")
        _emit_agent_event(
            "agent_launched", "jules", issue_id, pid=proc.pid, run_id=run_id
        )
        return proc
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[dispatcher] Failed to launch Jules: {exc}")
        try:
            from prismatic.jules_capacity import (
                record_jules_launch,
                update_jules_lifecycle,
            )

            if "capacity_launch_key" in locals():
                update_jules_lifecycle(
                    launch_key=locals()["capacity_launch_key"],
                    lifecycle_status="failed",
                    error_class="cli_error",
                )
            else:
                record_jules_launch(
                    issue_id=identifier or issue_id,
                    repository=os.environ.get("PRISMATIC_JULES_REPO")
                    or (os.environ.get("PRISMATIC_WORKTREE_PATH") or os.getcwd()),
                    source_path=str(locals().get("log_path", "")) or None,
                    launch_identity=request_id
                    or (
                        f"jules:{identifier or issue_id}:{cycle_id}"
                        if cycle_id
                        else None
                    ),
                    request_id=request_id,
                    lifecycle_status="failed",
                    error_class="cli_error",
                )
        except Exception:
            pass
        return None


def launch_codex(
    issue_id: str,
    task: str = "",
    title: str = "",
    identifier: str | None = None,
    labels: list[str] | None = None,
    cycle_id: str | None = None,
    request_id: str | None = None,
) -> subprocess.Popen | None:
    """Launch the Codex CLI for the given issue.

    Args:
        issue_id: Linear issue UUID or identifier.
        task: Optional task description.

    Returns:
        ``subprocess.Popen`` handle, or ``None`` if launch failed.
    """
    if not os.path.exists(CODEX_PATH):
        print(f"[dispatcher] Codex binary not found at {CODEX_PATH}")
        return None

    try:
        if not _governor.acquire("codex", issue_id, "local"):
            return None
        if not task and title:
            task = title
        cmd = [CODEX_PATH, "--issue", issue_id]
        if task:
            cmd.extend(["--task", task])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        run_id = record_launch_record(
            agent_name="codex",
            issue_id=issue_id,
            identifier=identifier or issue_id,
            cmd=cmd,
            pid=proc.pid,
            labels=labels,
            cycle_id=cycle_id,
            request_id=request_id,
        )
        if not _finalize_agent_launch("codex", issue_id, proc):
            return None
        print(f"[dispatcher] Launched Codex (pid={proc.pid}) for issue {issue_id}")
        _emit_agent_event(
            "agent_launched", "codex", issue_id, pid=proc.pid, run_id=run_id
        )
        return proc
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[dispatcher] Failed to launch Codex: {exc}")
        return None


# Map agent name → launch function
AGENT_LAUNCHERS: dict[str, Callable[..., Any]] = {
    "fred": signal_fred,
    "kai": signal_kai,
    "agy": launch_agy,
    "george": signal_george,
    "jules": launch_jules,
    "codex": launch_codex,
}

ASSIGNED_AGENT_EVENT_DISPATCH_MARKER = "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
ASSIGNED_AGENT_RESULT_WRITEBACK_MARKER = "ASSIGNED_AGENT_RESULT_WRITEBACK_OK"
ASSIGNED_AGENT_DISPATCH_RECOVERY_MARKER = "ASSIGNED_AGENT_DISPATCH_RECOVERY_OK"
ASSIGNED_AGENT_KNOWN_AGENTS = {"kai", "fred", "agy", "george"}
ASSIGNED_AGENT_TERMINAL_STATUSES = {
    "dispatched",
    "completed",
    "no_op",
    "failed",
    "stale",
    "needs_manual_review",
    "blocked_preflight",
    "preflight_failed",
    "deferred_rate_limit",
}


@dataclass
class AssignedAgentResolution:
    status: str
    target_agent: str = ""
    routing_source: str = ""
    reason: str = ""
    candidates: list[str] = field(default_factory=list)


@dataclass
class AssignedAgentPreflight:
    status: str
    allowed: bool
    reason: str = ""


def _assigned_agent_enabled(agent: str) -> bool:
    disabled = {
        item.strip().lower()
        for item in os.environ.get("PRISMATIC_DISABLED_AGENTS", "").split(",")
        if item.strip()
    }
    enabled = {
        item.strip().lower()
        for item in os.environ.get(
            "PRISMATIC_ENABLED_AGENTS", "kai,fred,agy,george"
        ).split(",")
        if item.strip()
    }
    return agent in enabled and agent not in disabled


def _assigned_agent_payload(row_or_payload: dict[str, Any]) -> dict[str, Any]:
    raw = row_or_payload.get("raw_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return row_or_payload


def _assigned_agent_label_candidates(payload: dict[str, Any]) -> list[tuple[str, str]]:
    maybe_data = payload.get("data")
    data: dict[str, Any] = maybe_data if isinstance(maybe_data, dict) else {}
    labels = data.get("labels") or payload.get("labels") or {}
    if isinstance(labels, dict):
        nodes = labels.get("nodes", [])
    elif isinstance(labels, list):
        nodes = labels
    else:
        nodes = []
    candidates: list[tuple[str, str]] = []
    for node in nodes:
        name = str(node.get("name") if isinstance(node, dict) else node or "").strip()
        normalized = name.replace("::", ":")
        if normalized.startswith("agent:"):
            agent = normalized.split(":", 1)[1].strip().lower()
            if agent:
                candidates.append((agent, "label"))
    for key in ("agent", "agent_name", "target_agent"):
        value = payload.get(key) or data.get(key)
        if value:
            candidates.append((str(value).strip().lower(), key))
    return candidates


def resolve_assigned_agent(row_or_payload: dict[str, Any]) -> AssignedAgentResolution:
    """Resolve exactly one intended agent from durable event/task metadata."""
    payload = _assigned_agent_payload(row_or_payload)
    candidates = _assigned_agent_label_candidates(payload)
    unique = sorted({agent for agent, _source in candidates if agent})
    if not unique:
        return AssignedAgentResolution(
            "needs_manual_review", reason="no known agent metadata", candidates=[]
        )
    unknown = [agent for agent in unique if agent not in ASSIGNED_AGENT_KNOWN_AGENTS]
    if unknown:
        return AssignedAgentResolution(
            "needs_manual_review",
            reason=f"unknown/disabled agent: {','.join(unknown)}",
            candidates=unique,
        )
    if len(unique) != 1:
        return AssignedAgentResolution(
            "needs_manual_review",
            reason=f"conflicting agents: {','.join(unique)}",
            candidates=unique,
        )
    agent = unique[0]
    routing_source = next(
        (source for cand, source in candidates if cand == agent), "metadata"
    )
    return AssignedAgentResolution(
        "resolved", target_agent=agent, routing_source=routing_source, candidates=unique
    )


def preflight_assigned_agent(
    row: dict[str, Any],
    resolution: AssignedAgentResolution,
    *,
    launchers: dict[str, Callable[..., Any]] | None = None,
) -> AssignedAgentPreflight:
    """Fail closed before waking exactly one resolved agent."""
    status = str(row.get("dispatch_status") or row.get("status") or "pending").lower()
    if status in ASSIGNED_AGENT_TERMINAL_STATUSES or status in {
        "claimed",
        "processing",
        "running",
    }:
        return AssignedAgentPreflight(
            "blocked_preflight", False, f"row already {status}"
        )
    if resolution.status != "resolved" or not resolution.target_agent:
        return AssignedAgentPreflight(
            "needs_manual_review", False, resolution.reason or "agent unresolved"
        )
    agent = resolution.target_agent
    if not _assigned_agent_enabled(agent):
        return AssignedAgentPreflight(
            "blocked_preflight", False, f"agent disabled: {agent}"
        )
    launcher_map = launchers or AGENT_LAUNCHERS
    payload = _assigned_agent_payload(row)
    handoff_result = handoff_dispatch_preflight(
        payload, agent, str(row.get("identifier") or "")
    )
    if handoff_result is not None and not handoff_result.ok:
        return AssignedAgentPreflight(
            handoff_result.status
            if handoff_result.is_manual_review
            else "blocked_preflight",
            False,
            handoff_result.reason,
        )
    if agent not in launcher_map:
        return AssignedAgentPreflight(
            "blocked_preflight", False, f"no launcher for {agent}"
        )
    if agent == "agy" and not os.environ.get("PRISMATIC_ASSIGNED_AGENT_DRY_RUN"):
        agy_exists = (
            os.path.exists(AGY_PATH)
            if os.path.isabs(AGY_PATH)
            else bool(shutil.which(AGY_PATH))
        )
        if not agy_exists:
            return AssignedAgentPreflight(
                "blocked_preflight", False, f"AGY binary missing: {AGY_PATH}"
            )
    if agent == "george" and not os.environ.get("PRISMATIC_ASSIGNED_AGENT_DRY_RUN"):
        hermes_exists = bool(
            shutil.which(os.environ.get("PRISMATIC_HERMES_BIN", "hermes"))
        )
        profile_dir = Path(
            os.environ.get(
                "PRISMATIC_GEORGE_PROFILE_DIR",
                str(Path.home() / ".hermes" / "profiles" / "george"),
            )
        )
        if not hermes_exists:
            return AssignedAgentPreflight(
                "blocked_preflight", False, "Hermes binary missing for George"
            )
        if not profile_dir.exists():
            return AssignedAgentPreflight(
                "blocked_preflight", False, f"George profile missing: {profile_dir}"
            )
    try:
        ensure_linear_circuit_closed(source="assigned_agent_event_dispatch.preflight")
    except LinearRateLimitCircuitOpen as exc:
        return AssignedAgentPreflight("deferred_rate_limit", False, str(exc))
    except Exception as exc:
        return AssignedAgentPreflight(
            "blocked_preflight", False, f"rate-limit gate unavailable: {exc}"
        )
    return AssignedAgentPreflight("passed", True, "ok")


def dispatch_assigned_agent_event(
    row: dict[str, Any],
    *,
    launchers: dict[str, Callable[..., Any]] | None = None,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    """Resolve, preflight, and wake exactly one intended agent for one queue event."""
    from .ingestion_queue import update_assigned_dispatch_state

    event_id = str(row.get("event_id") or row.get("id") or "")
    identifier = str(row.get("identifier") or "")
    payload = _assigned_agent_payload(row)
    labels = [agent for agent, _source in _assigned_agent_label_candidates(payload)]
    resolution = resolve_assigned_agent(row)
    update_assigned_dispatch_state(
        event_id,
        target_agent=resolution.target_agent,
        routing_source=resolution.routing_source,
        resolver_status=resolution.status,
        last_error=resolution.reason,
        dispatch_status="pending"
        if resolution.status == "resolved"
        else "needs_manual_review",
    )
    if resolution.status != "resolved":
        return {
            "ok": False,
            "marker": ASSIGNED_AGENT_EVENT_DISPATCH_MARKER,
            "status": "needs_manual_review",
            "target_agent": "",
            "wakes": [],
            "reason": resolution.reason,
        }
    preflight = preflight_assigned_agent(row, resolution, launchers=launchers)
    if not preflight.allowed:
        status = (
            "deferred_rate_limit"
            if preflight.status == "deferred_rate_limit"
            else "needs_manual_review"
            if preflight.status == "needs_manual_review"
            else "blocked_preflight"
        )
        update_assigned_dispatch_state(
            event_id,
            target_agent=resolution.target_agent,
            routing_source=resolution.routing_source,
            resolver_status=resolution.status,
            preflight_status=preflight.status,
            dispatch_status=status,
            last_error=preflight.reason,
        )
        return {
            "ok": False,
            "marker": ASSIGNED_AGENT_EVENT_DISPATCH_MARKER,
            "status": status,
            "target_agent": resolution.target_agent,
            "wakes": [],
            "reason": preflight.reason,
        }
    target = resolution.target_agent
    run_id = f"assigned-{target}-{uuid.uuid4().hex[:10]}"
    dry = (
        bool(os.environ.get("PRISMATIC_ASSIGNED_AGENT_DRY_RUN"))
        if dry_run is None
        else bool(dry_run)
    )
    if dry:
        update_assigned_dispatch_state(
            event_id,
            target_agent=target,
            routing_source=resolution.routing_source,
            resolver_status=resolution.status,
            preflight_status=preflight.status,
            dispatch_status="dispatched",
            claim_owner=target,
            run_id=run_id,
            last_error="dry-run wake recorded",
        )
        return {
            "ok": True,
            "marker": ASSIGNED_AGENT_EVENT_DISPATCH_MARKER,
            "status": "dispatched",
            "target_agent": target,
            "wakes": [target],
            "run_id": run_id,
            "dry_run": True,
        }
    launcher_map = launchers or AGENT_LAUNCHERS
    title = str(payload.get("title") or identifier)
    emit_visible_agent_stream_event(
        target, identifier, "WAKE_STARTED", title=title, run_id=run_id
    )
    if target in {"fred", "kai", "george"}:
        proc = None
        if launcher_map is AGENT_LAUNCHERS:
            proc = launch_visible_hermes_agent(
                target,
                identifier,
                title=title,
                labels=labels,
                identifier=identifier,
                cycle_id=str(row.get("cycle_id") or ""),
                request_id=str(row.get("request_id") or row.get("event_id") or ""),
                run_id=run_id,
            )
        if not proc:
            proc = launcher_map[target](identifier, title=title, priority=3)
    else:
        proc = launcher_map[target](
            identifier, title=title, labels=labels, identifier=identifier
        )
    if proc:
        emit_visible_agent_stream_event(
            target, identifier, "WAKE_DISPATCHED", title=title, run_id=run_id
        )
        update_assigned_dispatch_state(
            event_id,
            target_agent=target,
            routing_source=resolution.routing_source,
            resolver_status=resolution.status,
            preflight_status=preflight.status,
            dispatch_status="dispatched",
            claim_owner=target,
            run_id=run_id,
            last_error="",
        )
        return {
            "ok": True,
            "marker": ASSIGNED_AGENT_EVENT_DISPATCH_MARKER,
            "status": "dispatched",
            "target_agent": target,
            "wakes": [target],
            "run_id": run_id,
        }
    emit_visible_agent_stream_event(
        target,
        identifier,
        "WAKE_FAILED",
        title=title,
        run_id=run_id,
        reason="launcher returned no process/result",
    )
    update_assigned_dispatch_state(
        event_id,
        target_agent=target,
        routing_source=resolution.routing_source,
        resolver_status=resolution.status,
        preflight_status=preflight.status,
        dispatch_status="failed",
        last_error="launcher returned no process/result",
    )
    return {
        "ok": False,
        "marker": ASSIGNED_AGENT_EVENT_DISPATCH_MARKER,
        "status": "failed",
        "target_agent": target,
        "wakes": [],
        "reason": "launcher returned no process/result",
    }


def _assigned_result_preview(
    *,
    identifier: str,
    target_agent: str,
    result_status: str,
    result_summary: str,
    blocker_summary: str,
    run_id: str,
) -> str:
    title = (
        "completed"
        if result_status == "completed"
        else ("blocked" if result_status == "blocked" else "failed")
    )
    detail = result_summary or blocker_summary or "No details provided."
    return (
        f"[{ASSIGNED_AGENT_RESULT_WRITEBACK_MARKER}] {target_agent or 'assigned agent'} {title} {identifier}.\n\n"
        f"Run: {run_id or 'unknown'}\n"
        f"Status: {result_status}\n"
        f"Summary: {detail}\n"
        "\nDry-run writeback proof: no live Linear mutation was made."
    )


def record_assigned_agent_result_writeback(
    *,
    event_id: str = "",
    run_id: str = "",
    identifier: str = "",
    result_status: str,
    result_summary: str = "",
    blocker_summary: str = "",
    dry_run: bool | None = None,
    raw_output_text: str = "",
    raw_output_artifact_path: str = "",
) -> dict[str, Any]:
    """Persist agent completion/blocker result and safe Linear writeback state.

    Live Linear mutation is intentionally fail-closed unless
    PRISMATIC_LINEAR_WRITEBACK_AUTHORIZED=1 and dry_run=False. The default path
    records the exact comment/update preview as durable operator-visible state.
    """
    from .ingestion_queue import (
        QUEUE_TABLE,
        _connect,
        normalize_row,
        record_result_writeback,
    )

    if result_status not in {"completed", "blocked", "failed"}:
        raise ValueError(f"unsupported result_status: {result_status}")
    with _connect() as conn:
        clauses: list[str] = []
        params: list[str] = []
        if event_id:
            clauses.append("event_id = ?")
            params.append(event_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if identifier:
            clauses.append("identifier = ?")
            params.append(identifier)
        if not clauses:
            return {
                "ok": False,
                "marker": ASSIGNED_AGENT_RESULT_WRITEBACK_MARKER,
                "status": "not_found",
                "reason": "event_id, run_id, or identifier required",
                "linear_mutation": False,
            }
        row = conn.execute(
            f"SELECT * FROM {QUEUE_TABLE} WHERE {' OR '.join(clauses)} ORDER BY COALESCE(updated_at, received_at, 0) DESC, id DESC LIMIT 1",
            params,
        ).fetchone()
    if row is None:
        return {
            "ok": False,
            "marker": ASSIGNED_AGENT_RESULT_WRITEBACK_MARKER,
            "status": "not_found",
            "reason": "queue row not found",
            "linear_mutation": False,
        }
    item = normalize_row(row)
    resolved_event_id = str(item.get("event_id") or event_id)
    raw_output_capture: dict[str, Any] | None = None
    if raw_output_text.strip():
        try:
            from .agent_raw_output_queue import persist_raw_output

            target_agent = str(
                item.get("target_agent")
                or item.get("claim_owner")
                or item.get("agent_name")
                or "assigned-agent"
            )
            source_event_id = ":".join(
                part
                for part in (
                    "assigned_agent_result_writeback",
                    target_agent,
                    str(item.get("identifier") or identifier),
                    str(item.get("run_id") or run_id),
                )
                if part
            )
            captured = persist_raw_output(
                raw_text=raw_output_text,
                agent=target_agent,
                task_id=str(item.get("identifier") or identifier),
                source_event_id=source_event_id,
                raw_text_or_artifact_path=raw_output_artifact_path,
                expected_agent=target_agent,
            )
            raw_output_capture = {
                "ok": True,
                "raw_output_id": captured.raw_output_id,
                "normalization_status": captured.normalization_status,
                "source_event_id": captured.source_event_id,
            }
        except Exception as exc:  # fail-safe: writeback state remains source of truth
            raw_output_capture = {"ok": False, "reason": str(exc)}
    preview = _assigned_result_preview(
        identifier=str(item.get("identifier") or identifier),
        target_agent=str(
            item.get("target_agent")
            or item.get("claim_owner")
            or item.get("agent_name")
            or "assigned-agent"
        ),
        result_status=result_status,
        result_summary=result_summary,
        blocker_summary=blocker_summary,
        run_id=str(item.get("run_id") or run_id),
    )
    requested_live = dry_run is False
    authorized = os.environ.get("PRISMATIC_LINEAR_WRITEBACK_AUTHORIZED") == "1"
    if requested_live and not authorized:
        updated = record_result_writeback(
            resolved_event_id,
            result_status=result_status,
            result_summary=result_summary,
            blocker_summary=blocker_summary,
            writeback_status="blocked_live_unauthorized",
            writeback_mode="linear_comment_preview",
            writeback_preview=preview,
            retry_status="operator_authorization_required",
            recovery_status="writeback_blocked",
            last_error="live Linear writeback requested without PRISMATIC_LINEAR_WRITEBACK_AUTHORIZED=1",
        )
        return {
            "ok": False,
            "marker": ASSIGNED_AGENT_RESULT_WRITEBACK_MARKER,
            "status": "blocked_live_unauthorized",
            "item": updated,
            "writeback_preview": preview,
            "linear_mutation": False,
            "raw_output_capture": raw_output_capture,
        }
    # Authorized live mutation remains intentionally unimplemented in this slice;
    # proving dry-run writeback is the safe acceptance target.
    updated = record_result_writeback(
        resolved_event_id,
        result_status=result_status,
        result_summary=result_summary,
        blocker_summary=blocker_summary,
        writeback_status="dry_run",
        writeback_mode="linear_comment_preview",
        writeback_preview=preview,
    )
    return {
        "ok": True,
        "marker": ASSIGNED_AGENT_RESULT_WRITEBACK_MARKER,
        "status": "dry_run",
        "item": updated,
        "writeback_preview": preview,
        "linear_mutation": False,
        "raw_output_capture": raw_output_capture,
    }


def run_assigned_agent_dispatch_recovery(
    *,
    identifier: str,
    result_status: str = "completed",
    result_summary: str = "",
    blocker_summary: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Prove resolver → preflight → wake → result writeback for one controlled task.

    This is intentionally narrow: it operates on exactly one pending durable queue row
    for *identifier*, uses the exact-agent dispatch primitive, and records only the
    safe dry-run Linear writeback preview unless a future explicit live path is
    separately authorized and implemented.
    """
    dispatch = dispatch_issue_by_identifier(identifier, dry_run=dry_run)
    if not dispatch or not dispatch.get("ok"):
        return {
            "ok": False,
            "marker": ASSIGNED_AGENT_DISPATCH_RECOVERY_MARKER,
            "status": dispatch.get("status")
            if isinstance(dispatch, dict)
            else "dispatch_failed",
            "dispatch": dispatch,
            "writeback": None,
            "linear_mutation": False,
        }
    writeback = record_assigned_agent_result_writeback(
        run_id=str(dispatch.get("run_id") or ""),
        identifier=identifier,
        result_status=result_status,
        result_summary=result_summary,
        blocker_summary=blocker_summary,
        dry_run=True,
    )
    item = writeback.get("item") if isinstance(writeback, dict) else {}
    phases = {
        "resolver": bool(item and item.get("resolver_status") == "resolved"),
        "preflight": bool(item and item.get("preflight_status") == "passed"),
        "wake": bool(dispatch.get("wakes") and len(dispatch.get("wakes") or []) == 1),
        "result_writeback": bool(
            writeback.get("ok") and writeback.get("linear_mutation") is False
        ),
    }
    ok = all(phases.values())
    return {
        "ok": ok,
        "marker": ASSIGNED_AGENT_DISPATCH_RECOVERY_MARKER,
        "status": "ok" if ok else "incomplete",
        "identifier": identifier,
        "target_agent": dispatch.get("target_agent"),
        "run_id": dispatch.get("run_id"),
        "phases": phases,
        "dispatch": dispatch,
        "writeback": writeback,
        "linear_mutation": False,
    }


def dispatch_issue_by_identifier(
    identifier: str, **kwargs: Any
) -> dict[str, Any] | None:
    """Compatibility entrypoint for bounded queue drain: exact-agent only, no broad scan."""
    from .ingestion_queue import QUEUE_TABLE, _connect, normalize_row

    with _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM {QUEUE_TABLE} WHERE identifier = ? AND dispatch_status = 'pending' ORDER BY COALESCE(received_at, 0) ASC, id ASC LIMIT 1",
            (identifier,),
        ).fetchone()
    if row is None:
        return {
            "ok": False,
            "status": "no_op",
            "reason": "no pending queue row for identifier",
            "wakes": [],
        }
    return dispatch_assigned_agent_event(normalize_row(row), **kwargs)


# ═══════════════════════════════════════════════════════════════
# Process observer — fixes GRO-2979 / GRO-2978 closure gap.
#
# The 5 launchers above spawn agents (agy/jules/codex as subprocess.Popen,
# fred/kai as nudge-file writes). Until GRO-2979, every spawned process was
# fire-and-forget: the dispatcher's outer loop never observed the proc, so
# telemetry_agent_runs rows stayed status='dispatched' forever, and a single
# in-lane issue would re-dispatch on every cycle (GRO-2051 re-dispatched 178
# times in 5 days before the bug was diagnosed).
#
# The fix is structural: register every spawned Popen and drain its exit
# status back into TelemetryCollector.update_agent_run(). Signal-based
# launchers (fred/kai) already mark completion via the file watcher in the
# recipient — only Popen-launching agents need observer wiring.
# ═══════════════════════════════════════════════════════════════

# run_id → (proc, start_monotonic)
_PENDING_PROCS: dict[str, tuple[subprocess.Popen, float]] = {}
_PENDING_LOCK = threading.Lock()
_OBSERVER_THREAD_STARTED = threading.Lock()


def register_proc_for_observation(run_id: str, proc: subprocess.Popen) -> None:
    """Register a subprocess.Popen for closure-write observation.

    The observer thread polls each registered proc and, on exit, writes a
    `telemetry_agent_runs` UPDATE via ``TelemetryCollector.update_agent_run``.
    Safe to call from any thread; no-ops if the proc has already exited
    (closes the window in which fire-and-forget launchers could leak).
    """
    if proc is None:
        return
    with _PENDING_LOCK:
        _PENDING_PROCS[run_id] = (proc, time.monotonic())
    _ensure_observer_started()


def _ensure_observer_started() -> None:
    """Start the observer thread on first registration. Idempotent."""
    global _OBSERVER_THREAD
    with _OBSERVER_THREAD_STARTED:
        if getattr(_ensure_observer_started, "_already", False):
            return
        t = threading.Thread(
            target=_observer_loop,
            name="prismatic-proc-observer",
            daemon=True,
        )
        t.start()
        _ensure_observer_started._already = True  # type: ignore[attr-defined]


def _observer_loop() -> None:
    """Daemon thread: observe every registered proc, write closure on exit.

    Polls each proc every 2 seconds. On exit, captures stdout/stderr from
    /proc/<pid>/fd if available (Linux), determines status (exit_code 0 →
    completed, non-zero → failed), and calls update_agent_run(). Removes
    the proc from the pending set so it isn't double-processed.
    """
    while True:
        try:
            ready: list[tuple[str, subprocess.Popen]] = []
            with _PENDING_LOCK:
                for run_id, (proc, started_at) in list(_PENDING_PROCS.items()):
                    if proc.poll() is not None:
                        ready.append((run_id, proc))
                        del _PENDING_PROCS[run_id]

            for run_id, proc in ready:
                try:
                    rc = proc.returncode
                    status = "completed" if rc == 0 else "failed"
                    stderr = ""
                    try:
                        # Best-effort: capture a snippet of stderr from the
                        # child if it was redirected to a pipe we own.
                        # The launchers use DEVNULL today, so this is a
                        # no-op placeholder for the future --report-exit
                        # CLI flag.
                        stderr = ""
                    except Exception:
                        pass
                    try:
                        from .telemetry import get_collector

                        collector = get_collector()
                        collector.update_agent_run(
                            run_id=run_id,
                            status=status,
                            exit_code=rc,
                            error_message=stderr or None,
                        )
                    except Exception as exc:
                        print(
                            f"[dispatcher] observer: update_agent_run "
                            f"failed for {run_id}: {exc}"
                        )
                except Exception as exc:
                    print(
                        f"[dispatcher] observer: unexpected error for {run_id}: {exc}"
                    )
        except Exception as exc:
            print(f"[dispatcher] observer loop crashed: {exc}")
        time.sleep(2.0)


# ═══════════════════════════════════════════════════════════════
# Pipeline Router (thin wrapper around prismatic.router)
# ═══════════════════════════════════════════════════════════════


def load_pipeline_templates(config_path: str = "") -> dict[str, Any]:
    """Load pipeline definitions from a YAML/JSON config file.

    The default config path is ``~/.config/prismatic/pipelines.yaml``,
    overridable via the ``PRISMATIC_PIPELINE_CONFIG`` env var.

    Delegates to :func:`prismatic.router.load_pipeline_templates`.
    """
    from .router import load_pipeline_templates as _load

    default_config = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "prismatic"
        / "pipelines.yaml"
    )

    path = (
        config_path
        or os.environ.get("PRISMATIC_PIPELINE_CONFIG")
        or str(default_config)
    )
    return _load(path)


def detect_pipeline_type(
    issue: dict[str, Any],
    pipelines: dict[str, Any] | None = None,
) -> str | None:
    """Auto-detect pipeline type from issue labels/keywords.

    Delegates to :func:`prismatic.router.detect_pipeline_type`.
    """
    from .router import detect_pipeline_type as _detect

    if pipelines is None:
        try:
            pipelines = load_pipeline_templates()
        except (FileNotFoundError, ValueError):
            pipelines = {"pipelines": {}}

    return _detect(issue, pipelines)


def apply_pipeline(
    issue_id: str,
    pipeline_type: str,
    pipelines: dict[str, Any] | None = None,
) -> bool:
    """Set the first agent label and append pipeline context.

    Delegates to :func:`prismatic.router.apply_pipeline`.

    Returns:
        ``True`` on success.
    """
    from .router import apply_pipeline as _apply

    if pipelines is None:
        try:
            pipelines = load_pipeline_templates()
        except (FileNotFoundError, ValueError):
            pipelines = {"pipelines": {}}

    try:
        _apply(issue_id, pipeline_type, pipelines)
        return True
    except (ValueError, RuntimeError) as exc:
        print(f"[dispatcher] apply_pipeline failed: {exc}")
        return False


def setup_pipeline_issues(max_issues: int = 20) -> list[dict[str, Any]]:
    """Discover issues that match pipeline triggers and set them up.

    Scans the team's issues for pipeline-type labels (``pipeline::*``)
    or keyword triggers, then applies the first agent label.

    Args:
        max_issues: Max issues to scan.

    Returns:
        List of issue dicts that were successfully set up.
    """
    pipelines = load_pipeline_templates()
    if not pipelines.get("pipelines"):
        print("[dispatcher] No pipeline templates found")
        return []

    query = """
    query TeamIssues($teamId: String!, $first: Int!) {
        team(id: $teamId) {
            issues(first: $first, orderBy: updatedAt) {
                nodes {
                    id
                    identifier
                    title
                    description
                    labels { nodes { id name } }
                }
            }
        }
    }
    """
    data = gql(query, {"teamId": TEAM_ID, "first": max_issues})
    issues = data.get("team", {}).get("issues", {}).get("nodes", [])

    setup_issues = []
    for issue in issues:
        issue_dict = {
            "id": issue["id"],
            "identifier": issue.get("identifier", ""),
            "title": issue.get("title", ""),
            "description": issue.get("description", ""),
            "labels": [lab["name"] for lab in issue.get("labels", {}).get("nodes", [])],
        }

        # Skip if already has an agent label. Linear uses the single-colon
        # ``agent:name`` form; keep the legacy double-colon check for older
        # local fixtures.
        if any(
            lab.startswith("agent:") or lab.startswith("agent::")
            for lab in issue_dict["labels"]
        ):
            continue

        pipeline_type = detect_pipeline_type(issue_dict, pipelines)
        if pipeline_type:
            success = apply_pipeline(issue["id"], pipeline_type, pipelines)
            if success:
                print(
                    f"[dispatcher] Set up pipeline {pipeline_type!r} "
                    f"on {issue_dict['identifier']}: {issue_dict['title']}"
                )
                setup_issues.append(issue_dict)

    return setup_issues


# ═══════════════════════════════════════════════════════════════
# AGY Process Management
# ═══════════════════════════════════════════════════════════════


def cleanup_stale_agy(max_age_minutes: int = 5) -> int:
    """Kill AGY processes older than *max_age_minutes*.

    Scans the process table for ``agy`` processes and sends SIGTERM
    to any that have been running longer than the threshold.

    Args:
        max_age_minutes: Maximum allowed lifetime in minutes.

    Returns:
        Number of processes killed.
    """
    import subprocess as _subprocess

    killed = 0

    try:
        # Use ps to find AGY processes with their start times
        result = _subprocess.run(
            ["ps", "-eo", "pid,etime,args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        for line in result.stdout.splitlines():
            if "agy" not in line.lower():
                continue
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid_str = parts[0]
            etime_str = parts[1]

            # Parse elapsed time format: [[DD-]hh:]mm:ss
            age_seconds = _parse_etime(etime_str)
            if age_seconds is None:
                continue

            # Kill if older than max_age_minutes
            if age_seconds > max_age_minutes * 60:
                try:
                    os.kill(int(pid_str), signal.SIGTERM)
                    killed += 1
                    print(
                        f"[dispatcher] Killed stale AGY pid={pid_str} (age={etime_str})"
                    )
                except (OSError, ValueError) as exc:
                    print(f"[dispatcher] Failed to kill AGY pid={pid_str}: {exc}")

    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        print(f"[dispatcher] cleanup_stale_agy failed: {exc}")

    return killed


def _parse_etime(etime_str: str) -> float | None:
    """Parse ``ps`` elapsed-time format into total seconds.

    Formats handled::

        MM:SS
        HH:MM:SS
        DD-HH:MM:SS
    """
    try:
        if "-" in etime_str:
            days, rest = etime_str.split("-", 1)
            days = int(days)
        else:
            days = 0
            rest = etime_str

        parts = rest.strip().split(":")
        if len(parts) == 2:
            hours, minutes, seconds = 0, int(parts[0]), int(parts[1])
        elif len(parts) == 3:
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            return None

        return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
    except (ValueError, IndexError):
        return None


def recover_stalled_agy(
    max_retries: int = 3,
    escalate_to: str = "fred",
) -> None:
    """Retry stalled AGY tasks, then escalate to another agent.

    A stalled AGY task is one where the issue still has an
    ``agent:agy`` label after ``MAX_CYCLES_BEFORE_RECOVER``
    dispatcher cycles with no visible progress.

    .. note::
        This requires the deduplication database to track cycle
        counts per issue. The database path is ``DEFAULT_DB_PATH``.

    Args:
        max_retries: Max retry attempts before escalation.
        escalate_to: Agent to escalate to after exhausting retries.
    """
    db_path = DEFAULT_DB_PATH
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agy_stall_tracker (
            issue_id TEXT PRIMARY KEY,
            cycle_count INTEGER DEFAULT 0,
            last_seen TEXT,
            escalated INTEGER DEFAULT 0
        )
        """
    )

    try:
        # Find issues with agent:agy label that have been seen multiple cycles
        # Linear label format is single colon: agent:<name>
        issues = get_issues_with_label("agent:agy")

        for issue in issues:
            issue_id = issue["id"]

            # Update or increment cycle count
            cursor.execute(
                "SELECT cycle_count FROM agy_stall_tracker WHERE issue_id = ?",
                (issue_id,),
            )
            row = cursor.fetchone()

            if row:
                cycle_count = row[0] + 1
            else:
                cycle_count = 1

            cursor.execute(
                """
                INSERT OR REPLACE INTO agy_stall_tracker
                    (issue_id, cycle_count, last_seen, escalated)
                VALUES (?, ?, ?, 0)
                """,
                (issue_id, cycle_count, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

            if cycle_count >= max_retries:
                transition = ("execute", "review")
                if not mode_switch.request_approval(
                    *transition,
                    is_escalation=True,
                    reason=f"AGY stalled after {max_retries} cycles",
                ):
                    add_comment(
                        issue_id,
                        f"Transition paused: Escalation {transition[0]} -> {transition[1]} after AGY stalled. Comment /approve to continue.",
                    )
                    continue
                # Escalate — kill AGY and transition to escalate_to agent
                cleanup_stale_agy(max_age_minutes=0)  # Kill all AGY processes

                # Transition label
                transition_label(
                    issue_id,
                    remove_label="agent:agy",
                    add_label=f"agent:{escalate_to}",
                )

                # Post escalation comment
                add_comment(
                    issue_id,
                    f"⚠️ **AGY stalled** after {max_retries} cycles. "
                    f"Escalating to **{escalate_to}**.",
                )

                # Mark as escalated
                cursor.execute(
                    "UPDATE agy_stall_tracker SET escalated = 1 WHERE issue_id = ?",
                    (issue_id,),
                )
                conn.commit()

                # Signal the escalation target
                launcher = AGENT_LAUNCHERS.get(escalate_to)
                if launcher:
                    launcher(
                        issue_id,
                        title=f"Escalated: {issue.get('title', issue_id)}",
                        priority=5,
                    )

                print(
                    f"[dispatcher] Escalated stalled AGY issue "
                    f"{issue.get('identifier', issue_id)} to {escalate_to}"
                )

            else:
                print(
                    f"[dispatcher] AGY issue {issue.get('identifier', issue_id)} "
                    f"stalled (cycle {cycle_count}/{max_retries})"
                )

    except Exception as exc:
        print(f"[dispatcher] recover_stalled_agy failed: {exc}")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# Agent Command Parsing
# ═══════════════════════════════════════════════════════════════


def process_agent_commands(
    issue: dict[str, Any],
    pipelines: dict[str, Any] | None = None,
) -> list[str]:
    """Parse ``/agent:<name>`` commands from Linear issue comments.

    Looks at the most recent comments on the issue and extracts
    any ``/agent:<name>`` directives. This allows users to manually
    route work by commenting on an issue.

    Args:
        issue: Issue dict (must have ``id`` key).
        pipelines: Optional pipeline config (loaded automatically if
            not provided).

    Returns:
        List of agent names that were successfully dispatched to.
    """
    issue_id = issue.get("id", "")
    if not issue_id:
        return []

    # Fetch the last 5 comments
    query = """
    query IssueComments($issueId: String!) {
        issue(id: $issueId) {
            comments(first: 5, orderBy: createdAt, includeArchived: false) {
                nodes {
                    id
                    body
                    createdAt
                }
            }
        }
    }
    """
    try:
        data = gql(query, {"issueId": issue_id})
    except RuntimeError as exc:
        print(f"[dispatcher] Failed to fetch comments: {exc}")
        return []

    comments = data.get("issue", {}).get("comments", {}).get("nodes", [])
    dispatched = []

    # Process from newest to oldest
    for comment in reversed(comments):
        body = comment.get("body", "")
        # Match /agent:name or /agent:name:arg
        matches = re.findall(r"/agent:(\w+)(?::(\w+))?", body)
        for agent_name, arg in matches:
            agent_name = agent_name.lower()

            if agent_name in AGENT_LAUNCHERS:
                launcher = AGENT_LAUNCHERS[agent_name]
                result = launcher(
                    issue_id,
                    title=issue.get("title", ""),
                    priority=3 if arg != "urgent" else 5,
                )
                if result:
                    dispatched.append(agent_name)
                    print(
                        f"[dispatcher] Dispatched /agent:{agent_name} "
                        f"on {issue.get('identifier', issue_id)}"
                    )
            else:
                print(
                    f"[dispatcher] Unknown agent '{agent_name}' "
                    f"in command on {issue.get('identifier', issue_id)}"
                )

    return dispatched


# ═══════════════════════════════════════════════════════════════
# Deduplication (stub)
# ═══════════════════════════════════════════════════════════════


class EventRouterDedup:
    """Deduplication tracker for event router cycles.

    Tracks which issues have been processed in which cycle to prevent
    redundant dispatches. Uses a SQLite database at *db_path*.

    This is a minimal standalone version (no external deps).
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        db_dir = os.path.dirname(self._db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        # Row factory so cursor rows support dict-style access (matches
        # the canonical implementation in prismatic/dedup.py).
        self._conn.row_factory = sqlite3.Row
        # Lock for thread-safe dispatch counter access (matches the
        # canonical implementation in prismatic/dedup.py).
        import threading as _threading
        self._lock = _threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_log (
                issue_id TEXT NOT NULL,
                agent_label TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (issue_id, agent_label, cycle_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dedup_cycle
            ON dedup_log(cycle_id)
            """
        )
        # ── Label snapshots — track which labels issues had per cycle ──
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS label_snapshots (
                issue_id TEXT NOT NULL,
                label_name TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (issue_id, label_name, cycle_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_label_snapshots_issue
            ON label_snapshots(issue_id, label_name)
            """
        )
        self._conn.commit()

    def is_processed(self, issue_id: str, agent_label: str, cycle_id: str) -> bool:
        """Check if this issue+agent+cycle was already processed."""
        cursor = self._conn.execute(
            "SELECT 1 FROM dedup_log WHERE issue_id=? AND agent_label=? AND cycle_id=?",
            (issue_id, agent_label, cycle_id),
        )
        return cursor.fetchone() is not None

    def mark_processed(self, issue_id: str, agent_label: str, cycle_id: str) -> None:
        """Record that this issue+agent+cycle was processed."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO dedup_log (issue_id, agent_label, cycle_id, processed_at)
            VALUES (?, ?, ?, ?)
            """,
            (issue_id, agent_label, cycle_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def get_cycle_count(self, issue_id: str, agent_label: str) -> int:
        """Count how many cycles this issue has been sitting on a label."""
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM dedup_log WHERE issue_id=? AND agent_label=?",
            (issue_id, agent_label),
        )
        return cursor.fetchone()[0]

    def snapshot_labels(
        self, issue_id: str, label_names: list[str], cycle_id: str
    ) -> None:
        """Record the current label set for an issue in this cycle."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO label_snapshots
            (issue_id, label_name, cycle_id, seen_at)
            VALUES (?, ?, ?, ?)
            """,
            [(issue_id, name, cycle_id, now) for name in label_names],
        )
        self._conn.commit()

    def had_label(self, issue_id: str, label_name: str) -> bool:
        """Check if an issue ever had a specific label in any cycle."""
        cursor = self._conn.execute(
            "SELECT 1 FROM label_snapshots WHERE issue_id=? AND label_name=?",
            (issue_id, label_name),
        )
        return cursor.fetchone() is not None

    def had_labels(self, issue_id: str, label_names: list[str]) -> bool:
        """Check if an issue ever had ALL of the specified labels."""
        placeholders = ",".join("?" * len(label_names))
        params = [issue_id] + label_names
        cursor = self._conn.execute(
            f"SELECT COUNT(DISTINCT label_name) FROM label_snapshots "
            f"WHERE issue_id=? AND label_name IN ({placeholders})",
            params,
        )
        count = cursor.fetchone()[0]
        return count == len(label_names)

    def close(self) -> None:
        self._conn.close()

    # -- Dispatch cap (GRO-2979 regression prevention) -------------------
    # Counter that prevents per-issue retry storms. Mirrors the canonical
    # implementation in prismatic/dedup.py so the dispatcher loop can
    # throttle when an issue has been re-dispatched too many times in the
    # configured window without a closure row.

    # Hard cap on dispatches per issue (defends against retry storms;
    # GRO-2051 re-dispatched 178 times before this existed).
    MAX_DISPATCH_COUNT_PER_ISSUE = int(
        os.environ.get("PRISMATIC_MAX_DISPATCH_PER_ISSUE", "20")
    )
    # Window for the "stuck in <48h" rule.
    MAX_DISPATCH_WINDOW_HOURS = int(
        os.environ.get("PRISMATIC_MAX_DISPATCH_WINDOW_HOURS", "48")
    )

    def _count_dispatches(self, issue_id: str) -> int:
        """Return the total recorded dispatch count for *issue_id*."""
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS dispatch_counts (
                    issue_id TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    last_dispatched_at REAL,
                    first_dispatched_at REAL
                )"""
            )
            row = self._conn.execute(
                "SELECT count FROM dispatch_counts WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            return row["count"] if row else 0

    def record_dispatch(self, issue_id: str) -> int:
        """Bump the dispatch counter for *issue_id*. Returns new count.

        Idempotent against back-to-back calls: every dispatch increments
        exactly once.
        """
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS dispatch_counts (
                    issue_id TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    last_dispatched_at REAL,
                    first_dispatched_at REAL
                )"""
            )
            now = time.time()
            self._conn.execute(
                """INSERT INTO dispatch_counts (issue_id, count, last_dispatched_at, first_dispatched_at)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(issue_id) DO UPDATE SET
                       count = count + 1,
                       last_dispatched_at = ?""",
                (issue_id, now, now, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT count FROM dispatch_counts WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            return row["count"] if row else 0

    def is_over_dispatch_cap(
        self, issue_id: str, *, window_hours: int | None = None
    ) -> bool:
        """True if *issue_id* has exceeded the dispatch cap inside the window.

        Reads ``dispatch_counts.count`` and ``last_dispatched_at``; returns
        True when both:
          (a) count >= ``MAX_DISPATCH_COUNT_PER_ISSUE``, AND
          (b) last_dispatched_at is within ``window_hours`` (default
              ``MAX_DISPATCH_WINDOW_HOURS``).
        """
        if window_hours is None:
            window_hours = self.MAX_DISPATCH_WINDOW_HOURS
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS dispatch_counts (
                    issue_id TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    last_dispatched_at REAL,
                    first_dispatched_at REAL
                )"""
            )
            row = self._conn.execute(
                "SELECT count, last_dispatched_at FROM dispatch_counts WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
        if not row:
            return False
        count = row["count"]
        last_at = row["last_dispatched_at"] or 0.0
        if count < self.MAX_DISPATCH_COUNT_PER_ISSUE:
            return False
        window_seconds = window_hours * 3600
        return (time.time() - last_at) <= window_seconds


# Re-export for external callers
dedup = EventRouterDedup


# ═══════════════════════════════════════════════════════════════
# Origin Completion Detection (Generalized Peer-Review Loop)
# ═══════════════════════════════════════════════════════════════


def detect_origin_completions(
    dedup: EventRouterDedup,
    cycle_id: str,
) -> int:
    """Detect completed review loops and signal origin agents.

    When an issue transitions through a reviewer agent (e.g. ``agent:agy``)
    and lands at ``agent:fred``, the dispatcher determines which agent
    originally requested the review and signals them to pick up results.

    This closes the feedback loop: Kai→AGY→Kai, Ned→AGY→Ned, etc.
    — any agent can request peer review and get results back automatically.

    Strategy:
      1. Record label snapshots for all agent-labeled issues (builds
         history across cycles).
      2. Query all ``agent:fred`` issues.
      3. For each, check the label history for a reviewer agent (typically
         ``agent:agy``) and an origin agent (any other agent that preceded
         the reviewer).
      4. If origin found and not already signalled this cycle, signal the
         origin with ``signal_type: "review_complete"`` and
         ``origin_agent`` metadata.

    Returns:
        Number of origin signals sent.
    """
    signalled = 0

    # 1. Snapshot: record current labels for issues the dispatcher
    #    has seen (builds label history over cycles)
    #    Linear label format is single colon: agent:<name>
    agent_labels = [f"agent:{name}" for name in AGENT_CONFIG]
    for label_name in agent_labels:
        try:
            issues = get_issues_with_label(label_name, max_issues=50)
        except Exception:
            continue
        for issue in issues:
            try:
                dedup.snapshot_labels(
                    issue["id"],
                    issue.get("labels", []),
                    cycle_id,
                )
            except Exception:
                pass

    # 2. Query agent:fred issues (the ACTUAL Linear label format)
    try:
        fred_issues = get_issues_with_label("agent:fred", max_issues=100)
    except Exception:
        return 0

    # 3. Detect origin→reviewer→fred transitions
    for issue in fred_issues:
        issue_id = issue["id"]
        identifier = issue.get("identifier", issue_id)
        current_labels = issue.get("labels", [])

        # Skip if still has agent:agy (transition not complete)
        if "agent:agy" in current_labels:
            continue

        # Build the dedup key for this specific detection
        dedup_key = f"origin_complete:{identifier}"

        # Skip if already signalled this cycle
        if dedup.is_processed(issue_id, dedup_key, cycle_id):
            continue

        # Determine the origin agent: look through all configured agents
        # for one that both (a) previously appeared on this issue and
        # (b) is NOT the current reviewer (agy) or the terminal (fred)
        origin_agent = None
        for agent_name in AGENT_CONFIG:
            label = f"agent:{agent_name}"
            if label in ("agent:agy", "agent:fred", "agent:done"):
                continue
            if dedup.had_label(issue_id, label):
                # Also require that agent:agy was in the history
                # (confirms this was a review, not a direct dispatch)
                if dedup.had_label(issue_id, "agent:agy"):
                    origin_agent = agent_name
                    break

        if not origin_agent:
            continue

        # Record snapshot for current labels
        dedup.snapshot_labels(issue_id, current_labels, cycle_id)

        # 4. Signal the origin agent
        provider = _get_signal_provider()
        ok = provider.send_work(
            target=origin_agent,
            issue_id=identifier,
            title=(f"Review complete: {issue.get('title', identifier)}"),
            priority=2,  # High priority — origin should act on this
            signal_type="review_complete",
            origin_agent=origin_agent,
        )
        if ok:
            dedup.mark_processed(issue_id, dedup_key, cycle_id)
            signalled += 1
            print(
                f"[dispatcher] 🔔 {origin_agent.capitalize()} signalled "
                f"(review complete): {identifier}"
            )
            # ── Telemetry: record validation (review verdict) ──────
            try:
                collector = get_collector()
                collector.record_validation(
                    run_id=f"review-{origin_agent}-{identifier}",
                    agent="agy",
                    event_type="review_verdict",
                    total=1,
                    passed=1,
                    failed=0,
                )
            except Exception:
                pass  # Telemetry is best-effort
            # ── End telemetry ───────────────────────────────────────
            # Post a brief comment
            try:
                add_comment(
                    issue_id,
                    f"🔔 **Review complete** — **{origin_agent}** has been "
                    f"notified to review the results.",
                )
            except Exception:
                pass

    return signalled


def route_dispatch_ready_issues(max_issues: int = 50) -> int:
    """Assign unclaimed ``dispatch:ready`` issues by capability and capacity.

    This is the bridge from label-only dispatch to capability-aware routing:
    issues that are ready but do not yet have an ``agent:*`` label are routed
    to the least-loaded eligible worker lane.  Existing agent labels are
    respected; this helper never steals already-claimed work.
    """
    try:
        ready_issues = get_issues_with_label("dispatch:ready", max_issues=max_issues)
    except Exception as exc:
        print(f"[dispatcher] capability routing fetch failed: {exc}")
        return 0

    # Snapshot current load by counting active issues per configured lane.
    # Linear label format is single colon: agent:<name>
    loads: dict[str, int] = {}
    for agent_name in AGENT_CONFIG:
        try:
            loads[agent_name] = len(
                get_issues_with_label(f"agent:{agent_name}", max_issues=100)
            )
        except Exception:
            loads[agent_name] = 0

    registry = default_capability_registry(AGENT_CONFIG).with_loads(loads)
    routed = 0
    for issue in ready_issues:
        label_names = issue.get("labels", [])
        if any(label.startswith("agent:") for label in label_names):
            continue

        decision = route_issue(issue, registry)
        if not decision.selected or not decision.label:
            identifier = issue.get("identifier", issue.get("id", ""))
            print(
                f"[dispatcher] capability routing skipped {identifier}: {decision.reason}"
            )
            continue

        issue_id = issue["id"]
        try:
            current_labels = get_issue_labels(issue_id)
            current_ids = [label["id"] for label in current_labels]
            selected_label_id = get_label_id(decision.label)
            if not selected_label_id:
                print(
                    f"[dispatcher] capability routing could not resolve label "
                    f"{decision.label!r} for {issue.get('identifier', issue_id)}"
                )
                continue
            if selected_label_id not in current_ids:
                set_labels(issue_id, [*current_ids, selected_label_id])
            registry.reserve(decision.selected.name)
            routed += 1
            print(
                f"[dispatcher] capability routed "
                f"{issue.get('identifier', issue_id)} → {decision.label} "
                f"({decision.reason})"
            )
        except Exception as exc:
            print(
                f"[dispatcher] capability routing failed for "
                f"{issue.get('identifier', issue_id)}: {exc}"
            )

    return routed


# ═══════════════════════════════════════════════════════════════
# Main Dispatch Loop
# ═══════════════════════════════════════════════════════════════


def dispatch_once(
    dedup: EventRouterDedup,
    pipelines: dict[str, Any] | None = None,
    local_task_queue: Any | None = None,
) -> dict[str, int]:
    """Run a single dispatch cycle.

    Process flow:
      1. Discover new pipeline issues (``setup_pipeline_issues``).
      2. For each configured agent, find issues with ``agent:<name>``
         label that haven't been dispatched this cycle.
      3. Dispatch each issue to its agent's launch function.
      4. Clean up stale AGY processes.
      5. Recover stalled AGY tasks (after N cycles).

    Args:
        dedup: Deduplication tracker instance.
        pipelines: Pre-loaded pipeline templates (loaded automatically
            if not provided).

    Returns:
        Dict with counts: ``dispatched``, ``pipeline_setup``,
        ``stale_killed``, ``errors``.
    """
    global _POLL_CYCLE_NUMBER, _CURRENT_POLL_BUDGET
    _POLL_CYCLE_NUMBER += 1
    cycle_number = _POLL_CYCLE_NUMBER
    cycle_budget = LinearCycleBudget(
        max_calls=max(1, poll_max_calls_per_cycle()),
        cycle_number=cycle_number,
        poll_fallback_enabled=poll_fallback_enabled(),
    )
    _CURRENT_POLL_BUDGET = cycle_budget
    counts: dict[str, int] = {
        "dispatched": 0,
        "pipeline_setup": 0,
        "capability_routed": 0,
        "stale_killed": 0,
        "errors": 0,
        "starved": 0,
        "missing_dispatch_ready": 0,
        "governor_pruned": 0,
        "local_dispatched": 0,
        "linear_circuit_open": 0,
        "broad_poll_skipped": 0,
        "linear_calls_used": 0,
        "linear_call_budget_exhausted": 0,
        "poll_cache_hits": 0,
        "poll_cache_misses": 0,
        "host_path_rerouted": 0,
    }
    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    try:
        counts["governor_pruned"] = _governor.prune_stale()
    except Exception as exc:
        print(f"[dispatcher] governor prune error: {exc}")

    try:
        counts["local_dispatched"] = dispatch_local_tasks(
            dedup, local_task_queue=local_task_queue
        )
    except Exception as exc:
        print(f"[dispatcher] local task dispatch error: {exc}")
        counts["errors"] += 1

    if pipelines is None:
        try:
            pipelines = load_pipeline_templates()
        except (FileNotFoundError, ValueError):
            pipelines = {"pipelines": {}}

    rate_limit_snapshot = get_linear_rate_limit_snapshot()
    linear_poll_allowed = poll_fallback_enabled() and linear_broad_poll_allowed(
        source="dispatcher.dispatch_once"
    )
    if not poll_fallback_enabled():
        counts["broad_poll_skipped"] = 1
        skip_budget_section(
            "poll_fallback", "poll fallback disabled; webhook/event path is primary"
        )
    elif not linear_poll_allowed:
        counts["linear_circuit_open"] = 1
        counts["broad_poll_skipped"] = 1
        skip_budget_section(
            "rate_limit_cooldown",
            "Linear rate-limit cooldown active before broad scans",
        )

    # 1. Set up new pipeline issues
    if linear_poll_allowed and section_due("pipeline_scan", cycle_number):
        try:
            setup_issues = setup_pipeline_issues()
            counts["pipeline_setup"] = len(setup_issues)
        except LinearBudgetExhaustedError as exc:
            print(
                f"[dispatcher] setup_pipeline_issues skipped by Linear budget/circuit: {exc}"
            )
            counts["linear_call_budget_exhausted"] = 1
            counts["broad_poll_skipped"] = 1
            linear_poll_allowed = False
        except Exception as exc:
            print(f"[dispatcher] setup_pipeline_issues error: {exc}")
            counts["errors"] += 1
    elif linear_poll_allowed:
        counts["broad_poll_skipped"] = 1
        skip_budget_section(
            "pipeline_scan",
            f"cadence skip cycle={cycle_number} cadence={scan_cadence('pipeline_scan')}",
        )

    # 1b. Assign ready-but-unclaimed work by capability + capacity.
    if linear_poll_allowed and section_due("route_scan", cycle_number):
        try:
            counts["capability_routed"] = route_dispatch_ready_issues()
        except LinearBudgetExhaustedError as exc:
            print(
                f"[dispatcher] route_dispatch_ready_issues skipped by Linear budget/circuit: {exc}"
            )
            counts["linear_call_budget_exhausted"] = 1
            counts["broad_poll_skipped"] = 1
            linear_poll_allowed = False
        except Exception as exc:
            print(f"[dispatcher] route_dispatch_ready_issues error: {exc}")
            counts["errors"] += 1
    elif linear_poll_allowed:
        counts["broad_poll_skipped"] = 1
        skip_budget_section(
            "route_scan",
            f"cadence skip cycle={cycle_number} cadence={scan_cadence('route_scan')}",
        )

    # ── AI Ultra Credit Tracker ───────────────────────────
    throttle_dispatch = False
    try:
        from .credit_tracker import AIUltraCreditTracker

        tracker = AIUltraCreditTracker()

        # Scan workspace assets/designs/research for new media artifacts
        # (also scan /tmp for any temporary media generated during current run)
        workspace_root = os.getcwd()
        for media_dir in ["assets", "designs", "research", "/tmp"]:
            full_path = (
                os.path.join(workspace_root, media_dir)
                if media_dir != "/tmp"
                else "/tmp"
            )
            if os.path.exists(full_path):
                tracker.parse_media_artifacts(
                    full_path, run_id_prefix=f"cycle-{cycle_id}"
                )

        # Check burn velocity and exhaustion warning
        alert = tracker.evaluate_exhaustion_warning(lookback_hours=1.0)
        if alert:
            throttle_dispatch = True
            print(
                f"[dispatcher] ⚠️ Credit exhaustion warning alert! Throttling active. Message: {alert['message']}"
            )
            # Post comment to Linear if any issues are active
    except Exception as exc:
        print(f"[dispatcher] Credit tracking/alert error: {exc}")
    # ── End Credit Tracker ─────────────────────────────────

    # 2. Dispatch to each agent. Use the explicit lane contract for each
    #    queue instead of treating ``agent:*`` as a complete routing rule.
    agent_scan_due = linear_poll_allowed and section_due("agent_scan", cycle_number)
    if linear_poll_allowed and not agent_scan_due:
        counts["broad_poll_skipped"] = 1
        skip_budget_section(
            "agent_scan",
            f"cadence skip cycle={cycle_number} cadence={scan_cadence('agent_scan')}",
        )
    for agent_name, config in AGENT_CONFIG.items():
        if not linear_poll_allowed or not agent_scan_due:
            counts["broad_poll_skipped"] = 1
            continue
        label = f"agent:{agent_name}"
        try:
            # max_issues=100 (GRO-4616): older dispatch:ready issues like
            # GRO-4445 (last updated 2026-08-06) get bumped out of the
            # team-wide top-20 window if we use the default.
            issues = get_issues_with_label(label, max_issues=100)
        except LinearBudgetExhaustedError as exc:
            print(
                f"[dispatcher] Error fetching issues for {label}: Linear budget/circuit open: {exc}"
            )
            counts["linear_call_budget_exhausted"] = 1
            counts["broad_poll_skipped"] = 1
            linear_poll_allowed = False
            continue
        except Exception as exc:
            print(f"[dispatcher] Error fetching issues for {label}: {exc}")
            counts["errors"] += 1
            continue

        runnable_issues = [
            issue
            for issue in issues
            if is_dispatch_ready(issue) or label in issue.get("labels", [])
        ]
        missing_gate = len(issues) - len(runnable_issues)
        if missing_gate:
            print(
                f"[dispatcher] {agent_name}: skipping {missing_gate} issue(s) "
                f"without {DISPATCH_READY_LABEL}"
            )
            counts["missing_dispatch_ready"] += missing_gate

        if all(label in issue.get("labels", []) for issue in runnable_issues):
            held_issues = []
        else:
            runnable_issues, held_issues = filter_dispatchable_issues(
                runnable_issues, agent_name
            )
        for held_issue, hold_reason in held_issues:
            identifier = held_issue.get("identifier", held_issue.get("id", "<unknown>"))
            print(f"[dispatcher] ⏸️  Held {label} → {identifier}: {hold_reason}")
            counts["held"] = counts.get("held", 0) + 1

        if not runnable_issues:
            report_lane_starvation(agent_name, len(issues), missing_gate)
            try:
                signal_name = starvation_signal_for(agent_name)
            except KeyError:
                signal_name = f"{agent_name}_queue_empty"
            print(f"[dispatcher] Starvation signal: {signal_name}")
            counts["starved"] += 1
            continue

        for issue in runnable_issues:
            issue_id = issue["id"]
            identifier = issue.get("identifier", issue_id)

            # ── Dispatch cap (GRO-2979 regression prevention) ─────
            # If this issue has been re-dispatched too many times in
            # the configured window without a closure, skip and alert.
            try:
                over_cap = dedup.is_over_dispatch_cap(issue_id)
                if isinstance(over_cap, bool) and over_cap:
                    stuck_count = dedup._count_dispatches(issue_id)
                    print(
                        f"[dispatcher] ⚠️  STUCK {agent_name} → {identifier}: "
                        f"{stuck_count} dispatches in "
                        f"{dedup.MAX_DISPATCH_WINDOW_HOURS}h "
                        f"(cap={dedup.MAX_DISPATCH_COUNT_PER_ISSUE}). "
                        f"Skipping dispatch; needs human triage."
                    )
                    try:
                        add_comment(
                            issue_id,
                            f"⚠️ **Auto-marked stuck**: {stuck_count} "
                            f"dispatches in "
                            f"{dedup.MAX_DISPATCH_WINDOW_HOURS}h with no "
                            f"closure. Cap is "
                            f"{dedup.MAX_DISPATCH_COUNT_PER_ISSUE}. "
                            f"Pausing dispatch — needs review.",
                        )
                    except Exception:
                        pass
                    counts.setdefault("stuck", 0)
                    counts["stuck"] += 1
                    continue
            except Exception as exc:
                # Cap check is best-effort; never block on telemetry.
                print(f"[dispatcher] dispatch-cap check failed: {exc}")
            # ── End dispatch cap ───────────────────────────────────

            # Skip if already dispatched this cycle
            if dedup.is_processed(issue_id, label, cycle_id):
                continue

            if agent_name == "jules":
                host_matches = detect_host_level_patterns(issue)
                if host_matches and reroute_jules_host_path_issue(issue, host_matches):
                    counts["host_path_rerouted"] = (
                        counts.get("host_path_rerouted", 0) + 1
                    )
                    dedup.mark_processed(issue_id, label, cycle_id)
                    continue

            launcher = AGENT_LAUNCHERS.get(agent_name)
            if not launcher:
                continue

            # ── Mode-switch transition gate ───────────────────────
            transition = {
                "agy": ("dispatch", "execute"),
                "jules": ("execute", "review"),
                "codex": ("execute", "review"),
            }.get(agent_name, ("dispatch", "execute"))
            if not mode_switch.request_approval(*transition):
                comments = []
                try:
                    comments = (
                        gql(
                            "query($id:String!){ issue(id:$id){ comments(last:10){ nodes{ body } } } }",
                            {"id": issue_id},
                        )
                        .get("issue", {})
                        .get("comments", {})
                        .get("nodes", [])
                    )
                except Exception:
                    comments = []
                approved = any("/approve" in str(c.get("body", "")) for c in comments)
                if approved:
                    mode_switch.approve_transition(*transition)
                else:
                    try:
                        add_comment(
                            issue_id,
                            f"Transition paused: {transition[0]} -> {transition[1]}. Comment /approve to continue.",
                        )
                    except Exception:
                        pass
                    counts["pending_approval"] = counts.get("pending_approval", 0) + 1
                    dedup.mark_processed(issue_id, label, cycle_id)
                    continue

            # ── Credit policy enforcement ───────────────────────────
            label = (
                f"agent:{agent_name}"
                if not agent_name.startswith("agent:")
                else agent_name
            )
            decision = evaluate_agent_launch(
                label, issue_id, operation="code_generation"
            )
            if decision.action == PolicyAction.DENY:
                identifier = issue.get("identifier", issue_id)
                print(
                    f"[dispatcher] 🚫 BLOCKED {agent_name} → {identifier}: "
                    f"{decision.reason}"
                )
                try:
                    add_comment(
                        issue_id,
                        f"🚫 **Credit policy blocked**: {decision.reason}\n"
                        f"Estimated cost: {decision.estimated_cost} credits.",
                    )
                except Exception:
                    pass
                # Log to metrics
                log_completed_pipeline_metrics(
                    issue_id=issue_id,
                    agent=agent_name,
                    status="blocked",
                    reason=decision.reason,
                    cost=decision.estimated_cost,
                )
                counts["blocked"] = counts.get("blocked", 0) + 1
                dedup.mark_processed(issue_id, label, cycle_id)
                continue
            elif decision.action == PolicyAction.WARN:
                identifier = issue.get("identifier", issue_id)
                print(
                    f"[dispatcher] ⚠️  WARN {agent_name} → {identifier}: "
                    f"{decision.reason}"
                )
            elif decision.action == PolicyAction.ASK_USER:
                # Headless dispatcher cannot ask user — log and skip
                identifier = issue.get("identifier", issue_id)
                print(
                    f"[dispatcher] ❓ ASK_USER {agent_name} → {identifier}: "
                    f"{decision.reason}"
                )
                counts["pending_approval"] = counts.get("pending_approval", 0) + 1
                dedup.mark_processed(issue_id, label, cycle_id)
                continue
            # ── Telemetry: record credit evaluation ──────────────
            try:
                collector = get_collector()
                collector.record_credit(
                    run_id=f"{cycle_id}-{agent_name}-{issue.get('identifier', issue_id)}",
                    agent=agent_name,
                    provider=AGENT_PROVIDER_MAP.get(agent_name, ""),
                    credits_spent=decision.estimated_cost,
                    operation="code_generation",
                )
            except Exception:
                pass  # Telemetry is best-effort
            # ── End credit telemetry ───────────────────────────────
            # ── End credit policy ──────────────────────────────────

            if budget_caps_configured():
                try:
                    dashboard_data = get_collector().get_dashboard_data(hours=24)
                    daily_spend = float(dashboard_data.get("total_credits", 0) or 0)
                except Exception:
                    daily_spend = 0.0
                budget_decision = evaluate_budget_caps(daily_spend, read_budget_caps())
                if not budget_decision.allowed:
                    identifier = issue.get("identifier", issue_id)
                    print(
                        f"[dispatcher] 💰 AUTO-PAUSED {agent_name} → {identifier}: "
                        f"{budget_decision.reason}"
                    )
                    try:
                        add_comment(
                            issue_id,
                            "💰 **Budget cap auto-pause**: "
                            f"{budget_decision.reason}. Dispatch skipped before agent launch.",
                        )
                    except Exception:
                        pass
                    try:
                        estimated_cost = int(decision.estimated_cost)
                    except (TypeError, ValueError):
                        estimated_cost = 0
                    log_completed_pipeline_metrics(
                        issue_id=issue_id,
                        agent=agent_name,
                        status="blocked",
                        reason=budget_decision.reason,
                        cost=estimated_cost,
                    )
                    counts["budget_paused"] = counts.get("budget_paused", 0) + 1
                    dedup.mark_processed(issue_id, label, cycle_id)
                    continue

            try:
                if throttle_dispatch:
                    print(
                        f"[dispatcher] ⚠️ Throttling dispatch of {agent_name} (5s delay) due to high credit burn velocity."
                    )
                    time.sleep(5)
                launch_kwargs: dict[str, Any] = {"title": issue.get("title", "")}
                if config.get("mode") == "launch":
                    launch_kwargs.update(
                        {
                            "identifier": issue.get("identifier", issue_id),
                            "labels": issue.get("labels", []),
                            "cycle_id": cycle_id,
                        }
                    )
                result = launcher(issue_id, **launch_kwargs)
                if result:
                    dedup.mark_processed(issue_id, label, cycle_id)
                    counts["dispatched"] += 1
                    agent_name_pretty = agent_name.capitalize()
                    identifier = issue.get("identifier", issue_id)
                    print(
                        f"[dispatcher] 🚀 Dispatched {agent_name_pretty} "
                        f"→ {identifier}: {issue.get('title', '')}"
                    )
                    # ── Telemetry: record agent run ──────────────────
                    run_id = f"{cycle_id}-{agent_name}-{identifier}"
                    provider = AGENT_PROVIDER_MAP.get(agent_name, "")
                    collector = get_collector()
                    collector.record_agent_run(
                        run_id=run_id,
                        agent=agent_name,
                        issue_id=identifier,
                        provider=provider,
                        status="dispatched",
                        credits_spent=decision.estimated_cost,
                    )
                    # ── End telemetry ──────────────────────────────────
                    # ── Process observer (GRO-2979) ──────────────────
                    # If the launcher returned a subprocess.Popen, register
                    # it for closure observation and token-drain metrics.
                    # Signal-based launchers (fred/kai) return bool.
                    if isinstance(result, subprocess.Popen):
                        try:
                            register_proc_for_observation(run_id, result)
                        except Exception as exc:
                            # Observability is best-effort; never block dispatch
                            print(
                                f"[dispatcher] register_proc_for_observation "
                                f"failed for {identifier}: {exc}"
                            )
                        _drain_and_record_tokens(
                            proc=result,
                            run_id=run_id,
                            agent_name=agent_name,
                            provider=provider,
                        )
                    # ── End process observer/token drain ───────────────
                    # ── Dispatch counter (GRO-2979) ──────────────────
                    # Bump per-issue counter so the cap can detect storms.
                    try:
                        dedup.record_dispatch(issue_id)
                    except Exception as exc:
                        print(f"[dispatcher] record_dispatch failed: {exc}")
                    # ── End dispatch counter ──────────────────────────
                    # Emit agent_launched event to IPC bridge
                    _emit_agent_event(
                        "agent_launched", agent_name, identifier, cycle_id=cycle_id
                    )
                    # Post a comment tracking the dispatch
                    try:
                        add_comment(
                            issue_id,
                            f"🤖 **{agent_name_pretty}** picked up this issue "
                            f"(cycle {cycle_id})",
                        )
                    except Exception:
                        pass  # Non-critical
            except Exception as exc:
                print(
                    f"[dispatcher] Error dispatching {agent_name} "
                    f"→ {issue.get('identifier', issue_id)}: {exc}"
                )
                counts["errors"] += 1

    # 3. Clean up stale AGY processes
    try:
        killed = cleanup_stale_agy(max_age_minutes=5)
        counts["stale_killed"] = killed
    except Exception as exc:
        print(f"[dispatcher] cleanup_stale_agy error: {exc}")
        counts["errors"] += 1

    # 4. Recover stalled AGY (after enough cycles)
    if linear_poll_allowed and section_due("recovery_scan", cycle_number):
        try:
            recover_stalled_agy(max_retries=MAX_CYCLES_BEFORE_RECOVER)
        except LinearBudgetExhaustedError as exc:
            print(
                f"[dispatcher] recover_stalled_agy skipped by Linear budget/circuit: {exc}"
            )
            counts["linear_call_budget_exhausted"] = 1
            counts["broad_poll_skipped"] = 1
            linear_poll_allowed = False
        except Exception as exc:
            print(f"[dispatcher] recover_stalled_agy error: {exc}")
            counts["errors"] += 1
    elif linear_poll_allowed:
        counts["broad_poll_skipped"] = 1
        skip_budget_section(
            "recovery_scan",
            f"cadence skip cycle={cycle_number} cadence={scan_cadence('recovery_scan')}",
        )

    # 5. Detect origin completions — signal origin agents when reviews finish
    if linear_poll_allowed and section_due("origin_scan", cycle_number):
        try:
            origin_count = detect_origin_completions(dedup, cycle_id)
            if origin_count:
                print(
                    f"[dispatcher] 🔔 Signaled {origin_count} "
                    f"origin agent(s) for review completion"
                )
                counts["dispatched"] += origin_count
        except LinearBudgetExhaustedError as exc:
            print(
                f"[dispatcher] detect_origin_completions skipped by Linear budget/circuit: {exc}"
            )
            counts["linear_call_budget_exhausted"] = 1
            counts["broad_poll_skipped"] = 1
        except Exception as exc:
            print(f"[dispatcher] detect_origin_completions error: {exc}")
            counts["errors"] += 1
    elif linear_poll_allowed:
        counts["broad_poll_skipped"] = 1
        skip_budget_section(
            "origin_scan",
            f"cadence skip cycle={cycle_number} cadence={scan_cadence('origin_scan')}",
        )

    counts["linear_calls_used"] = cycle_budget.calls_used
    counts["poll_cache_hits"] = cycle_budget.cache_hits
    counts["poll_cache_misses"] = cycle_budget.cache_misses
    status = cycle_budget.as_dict(
        rate_limit_cooldown_active=bool(rate_limit_snapshot.get("cooldown_active"))
    )
    status["counts"] = dict(counts)
    _persist_polling_budget_status(status)
    _CURRENT_POLL_BUDGET = None
    return counts


def main_loop(
    interval: int = POLL_INTERVAL,
    once: bool = False,
) -> None:
    """Run the dispatcher event loop.

    Args:
        interval: Seconds between dispatch cycles.
        once: If ``True``, run a single cycle and exit.
    """
    print(f"[dispatcher] Prismatic Engine v{__import__('prismatic').__version__}")
    print(f"[dispatcher] TEAM_ID={TEAM_ID}")
    print(f"[dispatcher] Poll interval={interval}s")
    print(f"[dispatcher] State DB={DEFAULT_DB_PATH}")
    print()

    dedup = EventRouterDedup()
    collector = get_collector()
    print(f"[dispatcher] Telemetry collector active → {DEFAULT_DB_PATH}")

    try:
        pipelines = load_pipeline_templates()
        pipeline_count = len(pipelines.get("pipelines", {}))
        print(f"[dispatcher] Loaded {pipeline_count} pipeline template(s)")
    except FileNotFoundError:
        print("[dispatcher] No pipeline config found — running in ad-hoc mode")
        pipelines = {"pipelines": {}}
    except Exception as exc:
        print(f"[dispatcher] Warning: could not load pipelines: {exc}")
        pipelines = {"pipelines": {}}

    cycle = 0
    while True:
        cycle += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n{'=' * 60}")
        print(f"[dispatcher] Cycle {cycle} — {now}")
        print(f"{'=' * 60}")

        try:
            counts = dispatch_once(dedup, pipelines)
            print(
                f"[dispatcher] Cycle {cycle} summary: "
                f"{counts['dispatched']} dispatched, "
                f"{counts['pipeline_setup']} pipeline setups, "
                f"{counts.get('capability_routed', 0)} capability-routed, "
                f"{counts['stale_killed']} stale killed, "
                f"{counts.get('starved', 0)} starved lanes, "
                f"{counts.get('missing_dispatch_ready', 0)} missing-ready, "
                f"{counts['errors']} errors"
            )
            # ── GRO-3121: wakeup-empty metric ──────────────
            # When the dispatcher fires its polling loop but finds
            # nothing to dispatch, log it as an empty wakeup. This is
            # the polling-cost baseline Michael wants before deciding
            # to replace polling with webhook subscription. One row per
            # empty cycle — the factory digest surfaces the aggregate.
            try:
                if counts.get("dispatched", 0) == 0 and counts.get("errors", 0) == 0:
                    collector.record_wakeup_empty(
                        agent="dispatcher",
                        cycle_id=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                        reason="queue_empty",
                    )
            except Exception:
                pass  # Telemetry is best-effort — never break the loop
            # ── End wakeup-empty metric ──────────────────────────
            # ── Telemetry: log cycle metrics ─────────────────────
            if counts.get("dispatched", 0) > 0:
                dashboard = collector.get_dashboard_data(hours=1)
                loops = sum(r.get("cnt", 0) for r in dashboard.get("loops", []))
                tripped = dashboard.get("breakers_tripped", 0)
                if loops or tripped:
                    print(
                        f"[telemetry] Last hour: {loops} loop events, "
                        f"{tripped} breaker(s) tripped"
                    )
            # ── End telemetry ─────────────────────────────────────
        except KeyboardInterrupt:
            print("\n[dispatcher] Interrupted — shutting down")
            break
        except Exception as exc:
            print(f"[dispatcher] Fatal error in cycle {cycle}: {exc}")
            counts = {
                "dispatched": 0,
                "pipeline_setup": 0,
                "stale_killed": 0,
                "errors": 1,
            }

        if once:
            break

        time.sleep(interval)

    dedup.close()
    print("[dispatcher] Shutdown complete")


def init_config(force: bool = False) -> None:
    """Initialize default configuration files in the user's config directory."""
    config_dir = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "prismatic"
    )
    config_dir.mkdir(parents=True, exist_ok=True)

    template_dir = Path(__file__).parent / "templates" / "config"
    if not template_dir.is_dir():
        print(f"[dispatcher] Error: Template directory not found at {template_dir}")
        return

    copied = 0
    skipped = 0
    for template_file in template_dir.glob("*.yaml"):
        target_file = config_dir / template_file.name
        if target_file.exists() and not force:
            print(f"[dispatcher] Skipping existing config: {target_file}")
            skipped += 1
            continue

        try:
            import shutil

            shutil.copy2(template_file, target_file)
            print(f"[dispatcher] Initialized {target_file}")
            copied += 1
        except OSError as exc:
            print(f"[dispatcher] Error copying {template_file.name}: {exc}")

    print(
        f"\n[dispatcher] Initialization complete: {copied} copied, {skipped} skipped."
    )
    print(f"[dispatcher] Config directory: {config_dir}")


def cmd_billing_report(args: Any) -> None:
    """CLI handler for billing-report subcommand (Phase 4.4)."""
    from prismatic.billing.cost_attribution import CostAttributionEngine

    engine = CostAttributionEngine()

    # ── Set attribution mode ──
    if args.set_attribution:
        issue_id, client_id, project_id = args.set_attribution
        engine.set_attribution(issue_id, client_id, project_id)
        print(f"✓ Mapped {issue_id} → client={client_id}, project={project_id}")
        return

    # ── Report mode ──
    if args.projection:
        proj = engine.project_costs(client_id=args.client, project_id=args.project)
        print(f"\n{'=' * 60}")
        print("  Cost Projection (7-day rolling average)")
        print(f"{'=' * 60}")
        print(f"  Client:      {args.client or 'all'}")
        print(f"  Project:     {args.project or 'all'}")
        print(
            f"  Days of data: {len([c for c in proj.daily_costs if c > 0])}/{len(proj.daily_costs)}"
        )
        print(f"  Avg daily:   ${proj.average_daily:.4f}")
        print(f"  Projected/mo: ${proj.projected_monthly:.4f}")
        print(f"  Trend:       {proj.trend} (confidence: {proj.confidence})")
        print(f"{'=' * 60}\n")
        if proj.daily_costs:
            print("  Daily costs:")
            for i, cost in enumerate(proj.daily_costs):
                day = (
                    datetime.now(timezone.utc)
                    - timedelta(days=len(proj.daily_costs) - 1 - i)
                ).strftime("%Y-%m-%d")
                bar = "█" * min(int(cost * 50), 50) if cost > 0 else ""
                print(f"    {day}: ${cost:8.4f} {bar}")
            print()

    # ── Billing report ──
    if args.format == "json":
        print(
            engine.generate_report_json(client_id=args.client, project_id=args.project)
        )
    elif args.format == "csv":
        print(
            engine.generate_report_csv(client_id=args.client, project_id=args.project)
        )
    else:
        reports = engine.generate_report(client_id=args.client, project_id=args.project)
        if not reports:
            print("\n  No billing data found for the specified period.")
            return

        print(f"\n{'=' * 70}")
        print("  Client Cost Attribution Report")
        print(f"{'=' * 70}")
        for report in reports:
            print(f"\n  Client:  {report.client_id}")
            print(f"  Project: {report.project_id}")
            print(f"  Total:   ${report.total_cost_usd:.6f}")
            print(f"  Period:  {report.period_start[:10]} → {report.period_end[:10]}")
            print(f"  {'─' * 50}")
            if report.agent_breakdown:
                print("  Agent Breakdown:")
                for agent, adata in sorted(
                    report.agent_breakdown.items(),
                    key=lambda x: x[1]["cost_usd"],
                    reverse=True,
                ):
                    print(
                        f"    {agent:30s} ${adata['cost_usd']:10.6f}  ({adata['entries']} entries)"
                    )
            if report.model_breakdown:
                print("  Model Breakdown:")
                for model, mdata in sorted(
                    report.model_breakdown.items(),
                    key=lambda x: x[1]["cost_usd"],
                    reverse=True,
                ):
                    print(
                        f"    {model:30s} ${mdata['cost_usd']:10.6f}  ({mdata['entries']} entries)"
                    )
        print(f"{'=' * 70}\n")


def cmd_doctor(args: Any) -> int:
    """Run capability status and connection diagnostics."""
    from prismatic.cli.doctor import run as _doctor_run

    return _doctor_run(args)


def main() -> None:
    """Entry point: parse CLI arguments and start the dispatcher.

    Supports:
        ``serve``                Start the dispatcher event loop.
        ``init``                 Initialize default configuration files.
        ``optimize-workspace``   Create first-run context guards.
        ``skills``               Skill marketplace subcommands.
        ``--help``               Show usage.

    Legacy Support (for backward compatibility):
        ``--once``, ``--interval``, ``--setup-pipelines`` work as before.
    """
    import argparse

    # ── Legacy support: Rewrite sys.argv ─────────────────────────
    # If the first argument is a legacy flag, insert 'serve' before it.
    legacy_flags = {"--once", "--interval", "--setup-pipelines"}
    if len(sys.argv) > 1 and sys.argv[1] in legacy_flags:
        sys.argv.insert(1, "serve")

    parser = argparse.ArgumentParser(
        description="Prismatic Engine — agent orchestration dispatcher",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # ── Serve Subcommand ──────────────────────────────────────
    serve_parser = subparsers.add_parser(
        "serve", help="Start the dispatcher event loop"
    )
    serve_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single dispatch cycle and exit",
    )
    serve_parser.add_argument(
        "--interval",
        type=int,
        default=POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})",
    )
    serve_parser.add_argument(
        "--setup-pipelines",
        action="store_true",
        help="Run pipeline setup on all matching issues, then exit",
    )

    # ── Gateway/Visual Verification Subcommands ───────────────
    gateway_parser = subparsers.add_parser(
        "gateway", help="Run Prismatic gateway server"
    )
    gateway_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    gateway_parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PRISMATIC_PORT", "9000")),
        help="Bind port (default: PRISMATIC_PORT or 9000)",
    )
    gateway_parser.add_argument("--log-level", default="info")
    gateway_parser.add_argument("--reload", action="store_true")
    gateway_parser.add_argument(
        "--grpc", action="store_true", help="Enable gRPC bridge when available"
    )
    gateway_parser.add_argument(
        "--grpc-port", type=int, default=9001, help="gRPC port (default: 9001)"
    )

    visual_parser = subparsers.add_parser(
        "visual-verify", help="Run visual verification"
    )
    visual_parser.add_argument("args", nargs=argparse.REMAINDER)

    # ── Init Subcommand ───────────────────────────────────────
    init_parser = subparsers.add_parser(
        "init", help="Initialize default configuration files"
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration files",
    )

    # ── Optimize Workspace Subcommand ─────────────────────────
    optimize_parser = subparsers.add_parser(
        "optimize-workspace",
        help="Create first-run ignore files and disable high-overhead plugins",
    )
    optimize_parser.add_argument(
        "workspace",
        nargs="?",
        default=os.environ.get("PRISMATIC_HOME", os.getcwd()),
        help="Workspace root to optimize (default: PRISMATIC_HOME or current directory)",
    )
    optimize_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON",
    )

    # ── Billing-Report Subcommand (Phase 4.4) ─────────────────
    billing_parser = subparsers.add_parser(
        "billing-report", help="Generate client cost attribution report"
    )
    billing_parser.add_argument(
        "--client", type=str, default=None, help="Filter by client ID"
    )
    billing_parser.add_argument(
        "--project", type=str, default=None, help="Filter by project ID"
    )
    billing_parser.add_argument(
        "--format",
        type=str,
        default="table",
        choices=["table", "json", "csv"],
        help="Output format (default: table)",
    )
    billing_parser.add_argument(
        "--projection", action="store_true", help="Show rolling 7-day cost projection"
    )
    billing_parser.add_argument(
        "--set-attribution",
        nargs=3,
        metavar=("ISSUE_ID", "CLIENT_ID", "PROJECT_ID"),
        help="Map an issue to client/project for billing",
    )

    # ── Skills Subcommand ─────────────────────────────────────
    subparsers.add_parser(
        "skills", help="Skill marketplace subcommands (run 'skills --help' for details)"
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="Verify system health and providers"
    )
    doctor_parser.add_argument("--provider", default=None)

    # ── Help / No Command ─────────────────────────────────────
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    # Handle 'skills' early to delegate to skills.py
    if sys.argv[1] == "skills":
        from .skills import cli_skills

        sys.exit(cli_skills(sys.argv[2:]))

    args = parser.parse_args()

    if args.command == "init":
        init_config(force=args.force)
    elif args.command == "gateway":
        import uvicorn

        print(f"Starting gateway on {args.host}:{args.port}")
        uvicorn.run(
            "prismatic.gateway.server:app",
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            reload=args.reload,
        )
    elif args.command == "visual-verify":
        from prismatic.cli.visual_verify import main as visual_main

        sys.exit(visual_main(args.args))
    elif args.command == "optimize-workspace":
        from .workspace_optimizer import main as optimize_main

        sys.exit(optimize_main([args.workspace] + (["--json"] if args.json else [])))
    elif args.command == "billing-report":
        cmd_billing_report(args)
    elif args.command == "doctor":
        sys.exit(cmd_doctor(args))
    elif args.command == "serve":
        if args.setup_pipelines:
            issues = setup_pipeline_issues()
            print(f"Set up {len(issues)} pipeline issues")
            return
        main_loop(interval=args.interval, once=args.once)
    else:
        # Default fallback
        if args.command is None:
            print("Please specify a command: serve, init, or skills.")
            parser.print_help()


# ═══════════════════════════════════════════════════════════════
# Hook for ``python -m prismatic.dispatcher``
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    main()
