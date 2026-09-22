"""prismatic-mesh — mesh capability plugin for the Prismatic Engine.

Guest addressing + presence as infrastructure:

* ``POST /api/mesh/announce`` — a guest registers itself or heartbeats
  its presence (``peer_id``, ``address``, optional ``trust_tier``).
* ``GET /api/mesh/peers`` — the peer table (id, address, last-seen,
  trust tier, staleness flag).
* ``DELETE /api/mesh/peers/{id}`` — deregister a guest.

The peer table persists across disable/enable via the generic lifecycle
(``on_suspend`` / ``on_resume``), written to
``$PRISMATIC_HOME/plugin-state/prismatic-mesh/``. Presence changes emit
audit events (``peer_registered``, ``peer_heartbeat``,
``peer_deregistered``).

Event-driven: heartbeats and announcements are the event source. The
plugin runs NO polling loop and no background threads.

Boundary (docs/infrastructure-capabilities.md): addressing and presence
are infrastructure a harness can call. This plugin never routes
workloads, never decides placement, never sequences agent steps, and
never choreographs anything — it records *who is present*, never *what
anyone should do next*. It registers no agent tools
(``register_tools()`` returns ``[]``) and never touches
``prismatic/gateway/server.py``.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prismatic.interface.plugin import (
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)

# NOTE: swarmmesh is intentionally NOT imported at module top level. It
# ships in the ``primitives`` extra, not the base install, and the
# PluginLoader imports every plugin module at scan time — a top-level
# ``import swarmmesh`` here would crash base installs. The only use site
# (guest address normalization on announce) calls
# _swarmmesh_or_raise() first; the import is cached after the first
# successful call.

_swarmmesh_normalize: Optional[Any] = None


def _swarmmesh_or_raise() -> Any:
    """Import swarmmesh's address normalizer lazily.

    Raises a clear RuntimeError when the extra is missing. Loader scans
    never call this; it is only reached at announce time (a use-time
    dependency, not a load-time one).
    """
    global _swarmmesh_normalize
    if _swarmmesh_normalize is None:
        try:
            from swarmmesh.addressing import normalize_address
        except ImportError as exc:
            raise RuntimeError(
                "prismatic-mesh requires the 'swarmmesh' package, which ships in "
                "the 'primitives' extra: pip install 'prismatic-engine[primitives]'"
            ) from exc
        _swarmmesh_normalize = normalize_address
    return _swarmmesh_normalize


PLUGIN_NAME = "prismatic-mesh"
PLUGIN_VERSION = "0.1.0"

TRUST_TIERS = ("untrusted", "trusted")
DEFAULT_MAX_PEERS = 1000
DEFAULT_HEARTBEAT_TTL_SEC = 0
PRESENCE_EVENTS_MAX = 500
PEER_ID_MAX_LEN = 256
ADDRESS_MAX_LEN = 1024


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


def _validate_trust_tier(tier: Any, *, field: str = "trust_tier") -> str:
    if not isinstance(tier, str) or tier not in TRUST_TIERS:
        raise PluginValidationError(
            f"{field} must be one of {TRUST_TIERS}, got {tier!r}"
        )
    return tier


def _validate_peer_id(peer_id: Any) -> str:
    if not isinstance(peer_id, str) or not peer_id.strip():
        raise PluginValidationError("peer_id must be a non-empty string")
    peer_id = peer_id.strip()
    if len(peer_id) > PEER_ID_MAX_LEN:
        raise PluginValidationError(
            f"peer_id must be at most {PEER_ID_MAX_LEN} characters"
        )
    return peer_id


class MeshPlugin(PrismaticPlugin):
    """Mesh capability plugin — guest addressing + presence.

    The plugin owns the peer table. ``swarmmesh`` (the primitive) owns
    the *address syntax*: announce-time addresses are normalized through
    ``swarmmesh.addressing.normalize_address`` so "what counts as a valid
    guest address" stays with the primitive, not with this plugin.
    """

    def __init__(self) -> None:
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._presence_events: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._max_peers = DEFAULT_MAX_PEERS
        self._heartbeat_ttl_sec = DEFAULT_HEARTBEAT_TTL_SEC
        self._default_trust_tier = "untrusted"
        self._state_dir = plugin_state_dir()
        self._telemetry: Optional[Any] = None

    # ── lifecycle: init / suspend / resume ──────────────────────────────

    def on_init(self, context: PluginContext) -> None:
        """Validate config and (re)build the peer table.

        The generic PluginLoader passes the whole core config as
        ``context.config`` with this plugin's validated config nested at
        ``context.config["plugin_configs"]["prismatic-mesh"]``; standalone
        use may pass the plugin config directly. Support both.

        The primitive is deliberately NOT imported here: attach/enable on
        a base install must succeed; the missing primitive only surfaces
        as a clear RuntimeError at announce time.
        """
        core_config = context.config or {}
        config = core_config.get("plugin_configs", {}).get(PLUGIN_NAME, core_config)
        if not isinstance(config, dict):
            raise PluginValidationError("plugin config must be an object")

        max_peers = config.get("max_peers", DEFAULT_MAX_PEERS)
        if (
            not isinstance(max_peers, int)
            or isinstance(max_peers, bool)
            or max_peers < 1
        ):
            raise PluginValidationError("config.max_peers must be an integer >= 1")
        ttl = config.get("heartbeat_ttl_sec", DEFAULT_HEARTBEAT_TTL_SEC)
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 0:
            raise PluginValidationError(
                "config.heartbeat_ttl_sec must be an integer >= 0"
            )
        default_tier = _validate_trust_tier(
            config.get("default_trust_tier", "untrusted"),
            field="config.default_trust_tier",
        )

        self._state_dir = plugin_state_dir()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._telemetry = getattr(context, "telemetry_client", None)

        with self._lock:
            self._max_peers = max_peers
            self._heartbeat_ttl_sec = ttl
            self._default_trust_tier = default_tier
            self._peers = {}
            self._restore_peers_from_state_file_locked()

    def _restore_peers_from_state_file_locked(self) -> None:
        """Belt-and-braces restore: re-read the loader's state.json.

        The loader persists the suspend snapshot at the contract path;
        this restores the peer table even if the plugin was re-attached
        without a suspend snapshot flowing through on_resume().
        """
        state_path = self._state_dir / "state.json"
        if not state_path.exists():
            return
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(saved, dict):
            return
        # Two shapes: the loader wraps suspend output as
        # {"version": 1, "saved_at": ..., "state": {...}}; the plugin's
        # own belt-and-braces write is the raw snapshot {"peers": ...}.
        wrapped = saved.get("state")
        payload = wrapped if isinstance(wrapped, dict) else saved
        self._load_peer_snapshot(payload.get("peers"))
        events = payload.get("presence_events_tail")
        if isinstance(events, list):
            self._presence_events = [e for e in events if isinstance(e, dict)][
                -PRESENCE_EVENTS_MAX:
            ]

    def _load_peer_snapshot(self, peers: Any) -> None:
        if not isinstance(peers, list):
            return
        restored: Dict[str, Dict[str, Any]] = {}
        for raw in peers:
            if not isinstance(raw, dict):
                continue
            peer_id = raw.get("peer_id")
            if not isinstance(peer_id, str) or not peer_id:
                continue
            restored[peer_id] = {
                "peer_id": peer_id,
                "address": str(raw.get("address", "")),
                "trust_tier": (
                    raw.get("trust_tier")
                    if raw.get("trust_tier") in TRUST_TIERS
                    else "untrusted"
                ),
                "first_seen": str(raw.get("first_seen", "")),
                "last_seen": str(raw.get("last_seen", "")),
                "announce_count": int(raw.get("announce_count", 0) or 0),
            }
        self._peers = restored

    def on_suspend(self) -> Dict[str, Any]:
        """Return the JSON-serializable peer-table snapshot. No secrets."""
        with self._lock:
            snapshot = {
                "plugin": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "saved_at": _iso(_utcnow()),
                "peers": list(self._peers.values()),
                "presence_events_tail": list(
                    self._presence_events[-PRESENCE_EVENTS_MAX:]
                ),
            }
        # Belt-and-braces: persist alongside the loader's own state.json write.
        try:
            (self._state_dir / "state.json").write_text(
                json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
            )
        except OSError:
            pass
        # Guarantee JSON-serializability for the loader's own persistence.
        return json.loads(json.dumps(snapshot, default=str))

    def on_resume(self, state: Dict[str, Any]) -> None:
        """Restore the peer table from a suspend snapshot."""
        if not isinstance(state, dict):
            return
        with self._lock:
            peers = state.get("peers")
            if peers:
                self._load_peer_snapshot(peers)
            events = state.get("presence_events_tail")
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        self._presence_events.append(event)
                self._presence_events = self._presence_events[-PRESENCE_EVENTS_MAX:]

    # ── presence events (audit) ─────────────────────────────────────────

    def _emit_presence_event(
        self, kind: str, peer_id: str, detail: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Record a presence-change audit event.

        Events are kept in a bounded in-memory log (suspend/resume
        persists the tail) and forwarded best-effort to the context's
        telemetry client when one is present. A telemetry failure never
        breaks the announce/deregister path.
        """
        event: Dict[str, Any] = {
            "event": kind,
            "plugin": PLUGIN_NAME,
            "peer_id": peer_id,
            "at": _iso(_utcnow()),
        }
        if detail:
            event.update(detail)
        with self._lock:
            self._presence_events.append(event)
            self._presence_events = self._presence_events[-PRESENCE_EVENTS_MAX:]
        telemetry = self._telemetry
        if telemetry is not None:
            for method_name in (
                "record_event",
                "record_plugin_audit_event",
                "emit_audit_event",
                "emit",
            ):
                method = getattr(telemetry, method_name, None)
                if callable(method):
                    try:
                        method(event)
                    except Exception:
                        pass
                    break
        return event

    def presence_events(self) -> List[Dict[str, Any]]:
        """In-memory presence audit log (bounded)."""
        with self._lock:
            return list(self._presence_events)

    # ── peer table ──────────────────────────────────────────────────────

    def _is_stale(self, record: Dict[str, Any], now: datetime) -> bool:
        ttl = self._heartbeat_ttl_sec
        if ttl <= 0:
            return False
        try:
            last_seen = datetime.fromisoformat(str(record.get("last_seen", "")))
        except ValueError:
            return True
        return (now - last_seen).total_seconds() > ttl

    def _public_record(self, record: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        return {
            "peer_id": record["peer_id"],
            "address": record["address"],
            "trust_tier": record["trust_tier"],
            "first_seen": record["first_seen"],
            "last_seen": record["last_seen"],
            "announce_count": record["announce_count"],
            "stale": self._is_stale(record, now),
        }

    def announce_peer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new guest or heartbeat an existing one.

        ``payload``: ``{"peer_id": str, "address": str,
        "trust_tier": "untrusted" | "trusted" (optional)}``.

        Returns ``{"peer": <record>, "registered": bool}``. Emits
        ``peer_registered`` for new guests, ``peer_heartbeat`` for
        re-announces. Raises PluginValidationError on bad payload;
        RuntimeError when the swarmmesh primitive is not installed.
        """
        if not isinstance(payload, dict):
            raise PluginValidationError("announce payload must be an object")
        peer_id = _validate_peer_id(payload.get("peer_id"))
        address = payload.get("address")
        if not isinstance(address, str) or not address.strip():
            raise PluginValidationError("address must be a non-empty string")
        address = address.strip()
        if len(address) > ADDRESS_MAX_LEN:
            raise PluginValidationError(
                f"address must be at most {ADDRESS_MAX_LEN} characters"
            )
        # Address syntax belongs to the primitive; this is the use-time
        # dependency — clear RuntimeError when the extra is missing.
        normalize_address = _swarmmesh_or_raise()
        try:
            address = normalize_address(address)
        except (ValueError, TypeError) as exc:
            raise PluginValidationError(f"invalid guest address: {exc}") from exc
        trust_tier = _validate_trust_tier(
            payload.get("trust_tier", self._default_trust_tier)
        )

        now = _utcnow()
        with self._lock:
            existing = self._peers.get(peer_id)
            if existing is None and len(self._peers) >= self._max_peers:
                raise PluginValidationError(
                    f"peer table full (max_peers={self._max_peers}): "
                    f"cannot register new peer {peer_id!r}"
                )
            if existing is None:
                record = {
                    "peer_id": peer_id,
                    "address": address,
                    "trust_tier": trust_tier,
                    "first_seen": _iso(now),
                    "last_seen": _iso(now),
                    "announce_count": 1,
                }
                self._peers[peer_id] = record
                registered = True
            else:
                existing["address"] = address
                existing["trust_tier"] = trust_tier
                existing["last_seen"] = _iso(now)
                existing["announce_count"] = int(existing.get("announce_count", 0)) + 1
                record = existing
                registered = False
            public = self._public_record(record, now)

        self._emit_presence_event(
            "peer_registered" if registered else "peer_heartbeat",
            peer_id,
            {"address": address, "trust_tier": trust_tier},
        )
        return {"peer": public, "registered": registered}

    def peers_status(self) -> List[Dict[str, Any]]:
        """Payload for GET /api/mesh/peers."""
        now = _utcnow()
        with self._lock:
            records = [self._public_record(r, now) for r in self._peers.values()]
        return sorted(records, key=lambda r: r["peer_id"])

    def deregister_peer(self, peer_id: str) -> Dict[str, Any]:
        """Remove a guest from the peer table.

        Returns ``{"peer_id": ..., "removed": bool}`` and emits
        ``peer_deregistered`` when a record was actually removed.
        """
        peer_id = _validate_peer_id(peer_id)
        with self._lock:
            removed = self._peers.pop(peer_id, None) is not None
        if removed:
            self._emit_presence_event("peer_deregistered", peer_id)
        return {"peer_id": peer_id, "removed": removed}

    # ── plugin-registered API surface (no core edits) ───────────────────

    def register_api_routes(self) -> List[Dict[str, Any]]:
        """Route descriptors the PE Gateway exposes for this plugin."""
        return [
            {
                "method": "GET",
                "path": "/api/mesh/peers",
                "description": (
                    "Peer table: peer_id, address, trust tier, first/last "
                    "seen, announce count, and staleness flag."
                ),
                "handler": "mesh.plugin:MeshPlugin.peers_status",
            },
            {
                "method": "POST",
                "path": "/api/mesh/announce",
                "description": (
                    "Register a new guest or heartbeat an existing one. "
                    "Payload: {peer_id, address, trust_tier?}."
                ),
                "handler": "mesh.plugin:MeshPlugin.announce_peer",
            },
            {
                "method": "DELETE",
                "path": "/api/mesh/peers/{id}",
                "description": "Deregister a guest from the peer table.",
                "handler": "mesh.plugin:MeshPlugin.deregister_peer",
            },
        ]

    def register_tools(self) -> List[Dict[str, Any]]:
        """Addressing + presence is infrastructure, not an agent tool."""
        return []

    def capability_contract(self) -> Dict[str, Any]:
        """Machine-readable description of the mesh capability."""
        return {
            "capability": "mesh-presence",
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": (
                "Guest addressing + presence: register/heartbeat guests, "
                "list the peer table (id, address, last-seen, trust tier), "
                "and deregister guests. Peer table persists across "
                "disable/enable; presence changes emit audit events."
            ),
            "routes": self.register_api_routes(),
            "state_dir": str(self._state_dir),
            "tools": [],
            "notes": [
                "Disabled by default (auto_enable: false); operator enables explicitly.",
                "Event-driven: heartbeats/announcements are the event source; no polling loop.",
                "Boundary: never routes workloads, never decides placement, never sequences agent steps. It records who is present — never what anyone should do next.",
                "Trust tiers are stated plainly: 'untrusted' makes no claims about the guest; 'trusted' means the operator attested the guest out of band.",
            ],
        }
