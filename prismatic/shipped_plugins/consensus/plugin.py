"""prismatic-consensus -- quorum capability plugin for the Prismatic Engine.

Governance quorum primitive (e.g. 2-of-3 approvals) that the gate
evaluator's escalation path can CONSULT.

What it does:

* POST /api/consensus/propose -- open a quorum question: subject,
  required approvals, expiry.
* POST /api/consensus/approve -- record an approval with caller
  identity. The proposer cannot approve their own proposal; each caller
  identity may approve a proposal at most once.
* GET /api/consensus/status/{id} -- quorum state (pending/met/failed)
  with expiry evaluated at query time (event-driven on read; there is no
  background sweeper).

The plugin holds read-only gate-config knowledge: the quorum_policy
config maps gate decision names to the approvals they require, so the
plugin knows what needs quorum. It reads this config; it never writes
gate state.

What it does NOT do (scope guard -- the arrow is fixed):

* It never approves or denies anything itself; it only records approvals.
* It never writes gate decisions and never calls into the gate.
  The kernel reads the plugin; the plugin never drives the kernel.

Registers no agent tools (register_tools() returns []) and never
touches prismatic/gateway/server.py or any other core file.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prismatic.interface.plugin import (
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)

# NOTE: swarmconsensus is intentionally NOT imported at module top level. It
# ships in the primitives extra, not the base install, and the
# PluginLoader imports every plugin module at scan time -- a top-level
# import swarmconsensus here would crash base installs. Every use site
# calls _swarmconsensus_or_raise() first; the import is cached after the
# first successful call.
#
# Contract with the primitive (swarmconsensus.quorum)::
#
#     evaluate_quorum(*, required_approvals: int, approvals: int,
#                     expires_at: str, now: str) -> str
#
# Returns "met" when approvals >= required_approvals, "failed" when
# now > expires_at, otherwise "pending". Timestamps are UTC ISO-8601
# strings. The plugin owns proposal storage, caller-identity rules, and
# the API; the primitive owns the quorum policy math.

_swarmconsensus_quorum: Optional[Any] = None


def _swarmconsensus_or_raise() -> Any:
    """Import the swarmconsensus quorum primitive lazily; raise a clear error when missing."""
    global _swarmconsensus_quorum
    if _swarmconsensus_quorum is None:
        try:
            from swarmconsensus import quorum as _quorum
        except ImportError as exc:
            raise RuntimeError(
                "prismatic-consensus requires the swarmconsensus package, "
                "which is not installed. Quorum state cannot be evaluated "
                "without it."
            ) from exc
        _swarmconsensus_quorum = _quorum
    return _swarmconsensus_quorum


def _reset_swarmconsensus_cache() -> None:
    """Test hook: drop the cached primitive so sys.modules stubs take effect."""
    global _swarmconsensus_quorum
    _swarmconsensus_quorum = None


PLUGIN_NAME = "prismatic-consensus"
PLUGIN_VERSION = "0.1.0"

DEFAULT_REQUIRED_APPROVALS = 2
MAX_REQUIRED_APPROVALS = 9
DEFAULT_EXPIRY_SEC = 86400  # 1 day
MAX_EXPIRY_SEC = 30 * 86400  # 30 days

STATUS_PENDING = "pending"
STATUS_MET = "met"
STATUS_FAILED = "failed"
STATUSES = (STATUS_PENDING, STATUS_MET, STATUS_FAILED)


def plugin_state_dir() -> Path:
    """State root for this plugin.

    Matches the lifecycle contract: /plugin-state/<name>/.
    """
    home = Path(os.environ.get("PRISMATIC_HOME") or Path.home())
    return home / "plugin-state" / PLUGIN_NAME


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_expiry(raw: str) -> datetime:
    """Parse an ISO-8601 expiry; naive timestamps are treated as UTC."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _error(code: str, detail: str) -> Dict[str, Any]:
    return {"ok": False, "error": code, "detail": detail}


class ConsensusPlugin(PrismaticPlugin):
    """Quorum capability plugin -- governance approvals as infrastructure."""

    def __init__(self) -> None:
        self._proposals: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._state_dir = plugin_state_dir()
        self._default_required = DEFAULT_REQUIRED_APPROVALS
        self._max_required = MAX_REQUIRED_APPROVALS
        self._default_expiry_sec = DEFAULT_EXPIRY_SEC
        self._max_expiry_sec = MAX_EXPIRY_SEC
        # Read-only gate-config knowledge: decision name -> required approvals.
        # Read by propose(); never written by the plugin.
        self._quorum_policy: Dict[str, Dict[str, Any]] = {}

    # -- lifecycle: init / suspend / resume ------------------------------

    def on_init(self, context: PluginContext) -> None:
        """Validate config and prepare the proposal store.

        The generic PluginLoader passes the whole core config as
        context.config with this plugin's validated config nested at
        context.config["plugin_configs"]["prismatic-consensus"];
        standalone use may pass the plugin config directly. Support both.
        """
        core_config = context.config or {}
        config = core_config.get("plugin_configs", {}).get(PLUGIN_NAME, core_config)

        self._default_required = self._validated_int(
            config, "default_required_approvals", DEFAULT_REQUIRED_APPROVALS
        )
        self._max_required = self._validated_int(
            config, "max_required_approvals", MAX_REQUIRED_APPROVALS
        )
        if self._default_required > self._max_required:
            raise PluginValidationError(
                "config.default_required_approvals "
                f"({self._default_required}) exceeds config.max_required_approvals "
                f"({self._max_required})"
            )
        self._default_expiry_sec = self._validated_int(
            config, "default_expiry_sec", DEFAULT_EXPIRY_SEC
        )
        self._max_expiry_sec = self._validated_int(
            config, "max_expiry_sec", MAX_EXPIRY_SEC
        )
        if self._default_expiry_sec > self._max_expiry_sec:
            raise PluginValidationError(
                "config.default_expiry_sec "
                f"({self._default_expiry_sec}) exceeds config.max_expiry_sec "
                f"({self._max_expiry_sec})"
            )

        policy = config.get("quorum_policy", {}) or {}
        if not isinstance(policy, dict):
            raise PluginValidationError("config.quorum_policy must be an object")
        validated: Dict[str, Dict[str, Any]] = {}
        for decision, entry in policy.items():
            if not isinstance(decision, str) or not decision.strip():
                raise PluginValidationError(
                    "config.quorum_policy keys must be non-empty strings"
                )
            if not isinstance(entry, dict):
                raise PluginValidationError(
                    f"config.quorum_policy[{decision!r}] must be an object"
                )
            required = entry.get("required_approvals")
            if (
                isinstance(required, bool)
                or not isinstance(required, int)
                or not 1 <= required <= self._max_required
            ):
                raise PluginValidationError(
                    f"config.quorum_policy[{decision!r}].required_approvals must be "
                    f"an integer in [1, {self._max_required}]"
                )
            validated[decision] = {
                "required_approvals": required,
                "description": str(entry.get("description", "")),
            }
        self._quorum_policy = validated

        self._state_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validated_int(config: Dict[str, Any], key: str, default: int) -> int:
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PluginValidationError(
                f"config.{key} must be an integer >= 1, got {value!r}"
            )
        return value

    def on_suspend(self) -> Dict[str, Any]:
        """Return the JSON-serializable quorum store snapshot.

        Approval records carry caller identities and timestamps only --
        never secrets.
        """
        with self._lock:
            snapshot = {
                "plugin": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "saved_at": _iso(_utcnow()),
                "proposals": list(self._proposals.values()),
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
        """Restore open quorums from a suspend snapshot.

        Proposals are runtime state (not operator config), so the snapshot
        is always merged: records already present keep their live copy.
        """
        proposals = state.get("proposals") or []
        with self._lock:
            for record in proposals:
                if not isinstance(record, dict):
                    continue
                pid = record.get("id")
                if not isinstance(pid, str) or pid in self._proposals:
                    continue
                # Defensive: keep only well-formed records.
                if not isinstance(record.get("approvals"), list):
                    continue
                required = record.get("required_approvals")
                if isinstance(required, bool) or not isinstance(required, int):
                    continue
                if not isinstance(record.get("expires_at"), str):
                    continue
                self._proposals[pid] = record

    # -- quorum evaluation (delegated to the primitive) -------------------

    def _evaluate(self, proposal: Dict[str, Any], now: datetime) -> str:
        """Compute pending/met/failed for one proposal via the primitive.

        Raises RuntimeError when the swarmconsensus primitive is missing --
        the caller (route handler) converts that into a structured
        "primitive_not_installed" response instead of a traceback.
        """
        quorum = _swarmconsensus_or_raise()
        status = quorum.evaluate_quorum(
            required_approvals=proposal["required_approvals"],
            approvals=len(proposal["approvals"]),
            expires_at=proposal["expires_at"],
            now=_iso(now),
        )
        if status not in STATUSES:
            raise RuntimeError(
                f"swarmconsensus.quorum.evaluate_quorum returned unexpected "
                f"status {status!r}; expected one of {STATUSES}"
            )
        return status

    # -- plugin-registered API surface (no core edits) --------------------

    def propose_quorum(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Payload for POST /api/consensus/propose -- open a quorum question."""
        if not isinstance(payload, dict):
            return _error("invalid_request", "request body must be an object")

        subject = payload.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return _error("invalid_request", "subject must be a non-empty string")
        proposer = payload.get("proposer")
        if not isinstance(proposer, str) or not proposer.strip():
            return _error("invalid_request", "proposer must be a non-empty string")

        decision = payload.get("decision")
        required = payload.get("required_approvals")
        if decision is not None:
            if not isinstance(decision, str) or not decision.strip():
                return _error("invalid_request", "decision must be a non-empty string")
            entry = self._quorum_policy.get(decision)
            if entry is None:
                return _error(
                    "unknown_decision",
                    f"decision {decision!r} is not in the configured quorum_policy",
                )
            if required is not None and required != entry["required_approvals"]:
                return _error(
                    "conflicting_required_approvals",
                    f"required_approvals={required!r} conflicts with "
                    f"quorum_policy[{decision!r}].required_approvals="
                    f"{entry['required_approvals']}",
                )
            required = entry["required_approvals"]
        if required is None:
            required = self._default_required
        if isinstance(required, bool) or not isinstance(required, int) or required < 1:
            return _error(
                "invalid_request", "required_approvals must be a positive integer"
            )
        if required > self._max_required:
            return _error(
                "invalid_request",
                f"required_approvals={required} exceeds max_required_approvals="
                f"{self._max_required}",
            )

        now = _utcnow()
        expires_at = payload.get("expires_at")
        expires_in_sec = payload.get("expires_in_sec")
        if expires_at is not None and expires_in_sec is not None:
            return _error(
                "invalid_request",
                "set exactly one of expires_at / expires_in_sec",
            )
        if expires_in_sec is not None:
            if (
                isinstance(expires_in_sec, bool)
                or not isinstance(expires_in_sec, int)
                or expires_in_sec < 1
            ):
                return _error(
                    "invalid_request", "expires_in_sec must be a positive integer"
                )
            if expires_in_sec > self._max_expiry_sec:
                return _error(
                    "invalid_request",
                    f"expires_in_sec={expires_in_sec} exceeds max_expiry_sec="
                    f"{self._max_expiry_sec}",
                )
            expiry = now + timedelta(seconds=expires_in_sec)
        elif expires_at is not None:
            if not isinstance(expires_at, str):
                return _error(
                    "invalid_request", "expires_at must be an ISO-8601 string"
                )
            try:
                expiry = _parse_expiry(expires_at)
            except ValueError:
                return _error(
                    "invalid_request",
                    f"expires_at={expires_at!r} is not a valid ISO-8601 timestamp",
                )
            if expiry <= now:
                return _error("invalid_request", "expires_at must be in the future")
        else:
            expiry = now + timedelta(seconds=self._default_expiry_sec)

        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            return _error("invalid_request", "metadata must be an object")

        # The quorum primitive is required to open a quorum: fail loudly at
        # open time rather than silently at status time.
        try:
            _swarmconsensus_or_raise()
        except RuntimeError as exc:
            return _error("primitive_not_installed", str(exc))

        pid = uuid.uuid4().hex[:16]
        proposal = {
            "id": pid,
            "subject": subject.strip(),
            "proposer": proposer.strip(),
            "decision": decision,
            "required_approvals": required,
            "approvals": [],
            "created_at": _iso(now),
            "expires_at": _iso(expiry),
            "metadata": metadata,
        }
        with self._lock:
            self._proposals[pid] = proposal
        return {
            "ok": True,
            "id": pid,
            "status": STATUS_PENDING,
            "required_approvals": required,
            "expires_at": _iso(expiry),
        }

    def approve_quorum(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Payload for POST /api/consensus/approve -- record one approval.

        Identity rules: the proposer cannot approve their own proposal, and
        each caller identity may approve a given proposal at most once.
        Recording an approval is not a decision -- the plugin never
        approves or denies anything itself.
        """
        if not isinstance(payload, dict):
            return _error("invalid_request", "request body must be an object")
        pid = payload.get("id")
        if not isinstance(pid, str) or not pid:
            return _error("invalid_request", "id must be a non-empty string")
        approver = payload.get("approver")
        if not isinstance(approver, str) or not approver.strip():
            return _error("invalid_request", "approver must be a non-empty string")
        approver = approver.strip()

        with self._lock:
            proposal = self._proposals.get(pid)
        if proposal is None:
            return _error("unknown_proposal", f"no proposal with id {pid!r}")

        if approver == proposal["proposer"]:
            return _error(
                "self_approval",
                "the proposer cannot approve their own proposal",
            )
        if any(a.get("approver") == approver for a in proposal["approvals"]):
            return _error(
                "duplicate_approval",
                f"{approver!r} has already approved proposal {pid!r}",
            )

        now = _utcnow()
        try:
            status = self._evaluate(proposal, now)
        except RuntimeError as exc:
            return _error("primitive_not_installed", str(exc))
        if status != STATUS_PENDING:
            return _error(
                "proposal_not_pending",
                f"proposal {pid!r} is {status}; approvals are closed",
            )

        with self._lock:
            proposal["approvals"].append({"approver": approver, "at": _iso(now)})
            approvals = len(proposal["approvals"])
        try:
            status = self._evaluate(proposal, now)
        except RuntimeError as exc:
            return _error("primitive_not_installed", str(exc))
        return {
            "ok": True,
            "id": pid,
            "status": status,
            "approvals": approvals,
            "required_approvals": proposal["required_approvals"],
        }

    def quorum_status(self, proposal_id: str) -> Dict[str, Any]:
        """Payload for GET /api/consensus/status/{id}.

        Quorum state is computed at query time (event-driven on read): the
        primitive evaluates required-approvals against current approvals and
        the expiry timestamp. There is no background sweeper.
        """
        if not isinstance(proposal_id, str) or not proposal_id:
            return _error("invalid_request", "proposal id must be a non-empty string")
        with self._lock:
            proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return _error("unknown_proposal", f"no proposal with id {proposal_id!r}")
        try:
            status = self._evaluate(proposal, _utcnow())
        except RuntimeError as exc:
            return _error("primitive_not_installed", str(exc))
        return {
            "ok": True,
            "id": proposal["id"],
            "status": status,
            "subject": proposal["subject"],
            "proposer": proposal["proposer"],
            "decision": proposal["decision"],
            "required_approvals": proposal["required_approvals"],
            "approvals": list(proposal["approvals"]),
            "created_at": proposal["created_at"],
            "expires_at": proposal["expires_at"],
        }

    def register_api_routes(self) -> List[Dict[str, Any]]:
        """Route descriptors the PE Gateway exposes for this plugin."""
        return [
            {
                "method": "POST",
                "path": "/api/consensus/propose",
                "description": "Open a quorum question: subject, required approvals, expiry.",
                "handler": "consensus.plugin:ConsensusPlugin.propose_quorum",
            },
            {
                "method": "POST",
                "path": "/api/consensus/approve",
                "description": (
                    "Record an approval for a proposal with caller identity; "
                    "the proposer cannot approve their own proposal."
                ),
                "handler": "consensus.plugin:ConsensusPlugin.approve_quorum",
            },
            {
                "method": "GET",
                "path": "/api/consensus/status/{id}",
                "description": "Quorum state (pending/met/failed) with expiry evaluated at query time.",
                "handler": "consensus.plugin:ConsensusPlugin.quorum_status",
            },
        ]

    def register_tools(self) -> List[Dict[str, Any]]:
        """Quorum governance is infrastructure, not an agent tool."""
        return []

    def capability_contract(self) -> Dict[str, Any]:
        """Machine-readable description of the consensus capability."""
        return {
            "capability": "quorum-governance",
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": (
                "Governance quorum primitive (e.g. 2-of-3 approvals) that the "
                "gate evaluator's escalation path can CONSULT. Opens quorum "
                "questions, records approvals with caller identity (no "
                "self-approval, no duplicate approval), and reports quorum "
                "state with expiry evaluated at query time."
            ),
            "routes": self.register_api_routes(),
            "state_dir": str(self._state_dir),
            "tools": [],
            "notes": [
                "Disabled by default (auto_enable: false); operator enables explicitly.",
                "The plugin never approves or denies anything itself; it only records approvals.",
                "The plugin never writes gate decisions and never calls into the gate -- "
                "the kernel reads the plugin; the plugin never drives the kernel.",
                "quorum_policy config is read-only gate-config knowledge (decision name -> "
                "required approvals); the plugin reads it, never writes it.",
                "Expiry is evaluated at query time via swarmconsensus.quorum.evaluate_quorum; "
                "no background sweeper.",
            ],
        }
