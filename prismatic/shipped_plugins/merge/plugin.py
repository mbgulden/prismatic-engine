"""prismatic-merge — merge-strategy registry capability plugin.

A guest-callable registry of merge strategies. Guests (harnesses, agents,
operators) invoke strategies through the plugin's API routes; the plugin
itself never auto-merges anything, never holds kernel state, and is never
driven by the kernel.

Scope guard (docs/infrastructure-capabilities.md, non-negotiable in review):
"merge stays a guest-callable registry. Merge strategies are functions
guests invoke; merge is never driven by the kernel and never holds
kernel state."

Each strategy is a pure function implemented by the ``swarmmerge`` primitive
and named in the operator config::

    strategies:
      - name: last-writer-wins
        description: Later documents overwrite earlier keys.
        handler: strategies.last_writer_wins   # dotted path inside swarmmerge

``POST /api/merge/apply`` resolves the handler lazily, calls it with the
guest-supplied inputs, and returns the merged result. Apply is stateless:
the same strategy + inputs always produce the same output, and the plugin
keeps no kernel state — only an append-only audit log of apply calls under
its own state dir (``$PRISMATIC_HOME/plugin-state/prismatic-merge/``).

The plugin registers no agent tools (``register_tools()`` returns ``[]``)
and never touches ``prismatic/gateway/server.py`` or any kernel
transaction/lock/ledger path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prismatic.interface.plugin import (
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)

# NOTE: swarmmerge is intentionally NOT imported at module top level. It ships
# in the ``primitives`` extra, not the base install, and the PluginLoader
# imports every plugin module at scan time — a top-level ``import swarmmerge``
# here would crash base installs. Every use site calls _swarmmerge_or_raise()
# first; the import is cached after the first successful call.

_swarmmerge: Optional[Any] = None


def _swarmmerge_or_raise() -> Any:
    """Import swarmmerge lazily; raise a clear error when the extra is missing."""
    global _swarmmerge
    if _swarmmerge is None:
        try:
            import swarmmerge
        except ImportError as exc:
            raise RuntimeError(
                "prismatic-merge requires the 'swarmmerge' package, which ships in "
                "the 'primitives' extra: pip install 'prismatic-engine[primitives]'"
            ) from exc
        _swarmmerge = swarmmerge
    return _swarmmerge


PLUGIN_NAME = "prismatic-merge"
PLUGIN_VERSION = "0.1.0"

AUDIT_TAIL_MAX = 200
STRATEGY_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class MergeRequestError(ValueError):
    """Raised for bad merge requests: unknown strategy, malformed inputs,
    unresolvable handler, or a strategy that returned an unusable result."""


def plugin_state_dir() -> Path:
    """State root for this plugin.

    Matches the lifecycle contract: ``$PRISMATIC_HOME/plugin-state/<name>/``.
    The only thing the plugin ever writes is its own audit log here —
    never kernel state, never anywhere else.
    """
    home = Path(os.environ.get("PRISMATIC_HOME") or Path.home())
    return home / "plugin-state" / PLUGIN_NAME


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    """Bounded fingerprint of a JSON value for the audit log."""
    try:
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):
        canonical = repr(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_strategy(raw: Any) -> Dict[str, Any]:
    """Validate one strategy definition; return the canonical strategy dict.

    Raises PluginValidationError on any violation. Mirrors the manifest
    ``config_schema`` (the loader validates at attach; this is the defensive
    re-check inside on_init / on_resume).
    """
    if not isinstance(raw, dict):
        raise PluginValidationError(
            f"strategy must be an object, got {type(raw).__name__}"
        )

    name = raw.get("name")
    if not isinstance(name, str) or not STRATEGY_NAME_RE.fullmatch(name):
        raise PluginValidationError(
            "strategy.name must be a lowercase slug matching "
            f"{STRATEGY_NAME_RE.pattern!r}, got {name!r}"
        )

    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise PluginValidationError(
            f"strategy {name!r}: description must be a non-empty string"
        )

    handler = raw.get("handler")
    if not isinstance(handler, str) or not handler.strip():
        raise PluginValidationError(
            f"strategy {name!r}: handler must be a non-empty dotted path "
            "inside the swarmmerge package (e.g. 'strategies.last_writer_wins')"
        )

    return {
        "name": name,
        "description": description.strip(),
        "handler": handler.strip(),
    }


class MergePlugin(PrismaticPlugin):
    """Merge capability plugin — a guest-callable registry of merge strategies."""

    def __init__(self) -> None:
        self._strategies: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._audit_ring: List[Dict[str, Any]] = []
        self._state_dir = plugin_state_dir()
        # True when on_init received an explicit "strategies" key (even an
        # empty list). Distinguishes "operator configured zero strategies"
        # from "no config provided", so on_resume() knows when it may fall
        # back to the suspend snapshot for strategy definitions.
        self._strategies_from_config = False

    # ── lifecycle: init / suspend / resume ──────────────────────────────

    def on_init(self, context: PluginContext) -> None:
        """Validate the configured strategy registry.

        The generic PluginLoader passes the whole core config as
        ``context.config`` with this plugin's validated config nested at
        ``context.config["plugin_configs"]["prismatic-merge"]``; standalone
        use may pass the plugin config directly. Support both.

        Handler paths are NOT resolved here — swarmmerge may not be installed
        yet. Resolution happens lazily at apply time, so loader scans and
        attaches never depend on the primitive.
        """
        core_config = context.config or {}
        config = core_config.get("plugin_configs", {}).get(PLUGIN_NAME, core_config)
        strategies_cfg = config.get("strategies", [])
        if not isinstance(strategies_cfg, list):
            raise PluginValidationError("config.strategies must be an array")
        self._strategies_from_config = "strategies" in config

        strategies: Dict[str, Dict[str, Any]] = {}
        for raw in strategies_cfg:
            strategy = validate_strategy(raw)
            if strategy["name"] in strategies:
                raise PluginValidationError(
                    f"duplicate strategy name: {strategy['name']!r}"
                )
            strategies[strategy["name"]] = strategy

        self._state_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._strategies = strategies

    def on_suspend(self) -> Dict[str, Any]:
        """Return the JSON-serializable strategy registry echo.

        Apply is stateless, so there is nothing else to persist: the suspend
        snapshot is just the registry (names, descriptions, handler paths) —
        no inputs, no results, no secrets.
        """
        with self._lock:
            snapshot = {
                "plugin": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "saved_at": _iso(_utcnow()),
                "strategies": list(self._strategies.values()),
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
        """Restore the strategy registry from a suspend snapshot.

        Config is authoritative — on_init already loaded it. The snapshot's
        strategies are only a fallback: restored when this instance has no
        strategies AND on_init did not receive an explicit "strategies" key
        (so an intentionally-emptied config stays empty instead of
        resurrecting old definitions).
        """
        with self._lock:
            strategies_cfg = state.get("strategies") or []
            if (
                strategies_cfg
                and not self._strategies
                and not self._strategies_from_config
            ):
                strategies: Dict[str, Dict[str, Any]] = {}
                for raw in strategies_cfg:
                    strategy = validate_strategy(raw)
                    strategies[strategy["name"]] = strategy
                self._strategies = strategies

    # ── registry reads ──────────────────────────────────────────────────

    def list_strategies(self) -> List[Dict[str, Any]]:
        """Payload for GET /api/merge/strategies."""
        with self._lock:
            return [
                {
                    "name": s["name"],
                    "description": s["description"],
                    "handler": s["handler"],
                }
                for s in self._strategies.values()
            ]

    def recent_audit_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """In-memory tail of apply audit events (bounded ring)."""
        with self._lock:
            return list(self._audit_ring[-max(1, limit) :])

    # ── apply ───────────────────────────────────────────────────────────

    def _resolve_handler(self, handler_path: str) -> Any:
        """Resolve a dotted handler path inside the swarmmerge namespace.

        The path may only walk public attributes of the swarmmerge package
        (no private/dunder segments), so a misconfigured handler can never
        reach outside the primitive.
        """
        prim = _swarmmerge_or_raise()
        obj: Any = prim
        for seg in handler_path.split("."):
            if not seg.isidentifier() or seg.startswith("_"):
                raise MergeRequestError(
                    f"strategy handler {handler_path!r}: invalid path segment "
                    f"{seg!r} — handlers resolve inside the swarmmerge package "
                    "namespace only"
                )
            nxt = getattr(obj, seg, None)
            if nxt is None:
                raise MergeRequestError(
                    f"strategy handler {handler_path!r} not found in swarmmerge"
                )
            obj = nxt
        if not callable(obj):
            raise MergeRequestError(
                f"strategy handler {handler_path!r} is not callable"
            )
        return obj

    def _record_audit(
        self,
        event: str,
        strategy: Any,
        inputs: Any,
        result: Any,
        error: str = "",
    ) -> Dict[str, Any]:
        """Append one audit event per apply call.

        Observability only — the audit log never feeds back into merge
        behavior. Written to the plugin's own state dir, nowhere else.
        """
        entry: Dict[str, Any] = {
            "event": event,
            "plugin": PLUGIN_NAME,
            "strategy": strategy,
            "inputs_digest": _digest(inputs),
            "result_digest": _digest(result) if result is not None else None,
            "applied_at": _iso(_utcnow()),
            "ok": event == "merge_applied",
        }
        if error:
            entry["error"] = error
        try:
            with (self._state_dir / "audit.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass
        with self._lock:
            self._audit_ring.append(entry)
            self._audit_ring = self._audit_ring[-AUDIT_TAIL_MAX:]
        return entry

    def apply_merge(self, strategy: str, inputs: Any) -> Dict[str, Any]:
        """Apply a named merge strategy to guest-supplied inputs.

        Payload for POST /api/merge/apply. Pure function of its arguments:
        the same strategy + inputs always produce the same output, and no
        kernel state is read or written. Raises MergeRequestError for an
        unknown strategy or malformed inputs, RuntimeError when the
        swarmmerge primitive is not installed.
        """
        if not isinstance(strategy, str) or not strategy:
            raise MergeRequestError("strategy must be a non-empty string")
        with self._lock:
            record = self._strategies.get(strategy)
        if record is None:
            known = sorted(self._strategies)
            self._record_audit(
                "merge_failed",
                strategy,
                inputs,
                None,
                error=f"unknown strategy {strategy!r}; known: {known}",
            )
            raise MergeRequestError(
                f"unknown merge strategy {strategy!r}; known strategies: {known}"
            )
        if not isinstance(inputs, dict):
            self._record_audit(
                "merge_failed",
                strategy,
                inputs,
                None,
                error="inputs must be a JSON object",
            )
            raise MergeRequestError("inputs must be a JSON object (dict)")
        try:
            json.dumps(inputs)
        except (TypeError, ValueError) as exc:
            self._record_audit(
                "merge_failed",
                strategy,
                inputs,
                None,
                error=f"inputs not JSON-serializable: {exc}",
            )
            raise MergeRequestError(f"inputs must be JSON-serializable: {exc}") from exc

        try:
            handler = self._resolve_handler(record["handler"])
            result = handler(inputs)
        except Exception as exc:
            self._record_audit(
                "merge_failed",
                strategy,
                inputs,
                None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        try:
            json.dumps(result)
        except (TypeError, ValueError) as exc:
            self._record_audit(
                "merge_failed",
                strategy,
                inputs,
                None,
                error=f"strategy returned non-JSON-serializable result: {exc}",
            )
            raise MergeRequestError(
                f"strategy {strategy!r} returned a non-JSON-serializable result"
            ) from exc

        applied_at = _iso(_utcnow())
        self._record_audit("merge_applied", strategy, inputs, result)
        return {"strategy": strategy, "result": result, "applied_at": applied_at}

    # ── plugin-registered API surface (no core edits) ───────────────────

    def register_api_routes(self) -> List[Dict[str, Any]]:
        """Route descriptors the PE Gateway exposes for this plugin."""
        return [
            {
                "method": "GET",
                "path": "/api/merge/strategies",
                "description": (
                    "List registered merge strategies with names and descriptions."
                ),
                "handler": "merge.plugin:MergePlugin.list_strategies",
            },
            {
                "method": "POST",
                "path": "/api/merge/apply",
                "description": (
                    "Apply a named merge strategy to guest-supplied inputs; "
                    "returns the merged result. Stateless: the result is a "
                    "pure function of the strategy and inputs."
                ),
                "handler": "merge.plugin:MergePlugin.apply_merge",
            },
        ]

    def register_tools(self) -> List[Dict[str, Any]]:
        """Merging is infrastructure, not an agent tool."""
        return []

    def capability_contract(self) -> Dict[str, Any]:
        """Machine-readable description of the merge capability."""
        return {
            "capability": "merge-strategy-registry",
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": (
                "Guest-callable registry of merge strategies. Guests invoke a "
                "named strategy with inputs; the plugin applies it as a pure "
                "function and returns the merged result. Never auto-merges, "
                "never holds kernel state, never driven by the kernel."
            ),
            "strategies": self.list_strategies(),
            "routes": self.register_api_routes(),
            "state_dir": str(self._state_dir),
            "tools": [],
            "notes": [
                "Disabled by default (auto_enable: false); operator enables explicitly.",
                "Strategy implementations live in the swarmmerge primitive; "
                "handler paths resolve inside its package namespace only.",
                "Every apply call appends one audit event to the plugin's own "
                "audit.jsonl; the audit log never feeds back into merge behavior.",
            ],
        }
