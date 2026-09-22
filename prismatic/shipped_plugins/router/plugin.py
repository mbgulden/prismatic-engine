"""prismatic-router — policy-based ingress classification capability plugin.

The plugin answers one question, purely: *"which capability should handle
this request class?"* Operator-configured rules map a request class onto a
required capability plus advisory policy notes. Classification is a pure
function of ``(rules, input)``: deterministic, repeatable, zero side
effects.

The anti-choreography line: **it classifies, never dispatches.** There is
no dispatch / execute / enqueue path anywhere in this plugin — no method,
no route, no background thread. Enforcement of a routing decision stays
with the kernel gate / the harness; the plugin's answer is advisory.

The swarmrouter primitive is consulted for one advisory annotation only:
whether the matched capability is a known capability in the primitive's
taxonomy (``swarmrouter.routing.capability_known``). It never influences
the classification result itself.

The plugin registers no agent tools (``register_tools()`` returns ``[]``)
and exposes its rule table and advisory classification only through
plugin-registered API routes — it never touches
``prismatic/gateway/server.py``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prismatic.interface.plugin import (
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)

# NOTE: swarmrouter is intentionally NOT imported at module top level. It
# ships in the ``primitives`` extra, not the base install, and the
# PluginLoader imports every plugin module at scan time — a top-level
# ``import swarmrouter`` here would crash base installs. Every use site
# calls _swarmrouter_or_raise() first; the import is cached after the first
# successful call.
#
# Expected primitive seam (aspirational: swarmrouter is not on PyPI yet):
#   swarmrouter.routing.capability_known(name: str) -> bool
# The plugin tolerates the attribute being absent (annotation becomes None);
# only a missing/broken package raises.

_swarmrouter: Optional[Any] = None


def _swarmrouter_or_raise() -> Any:
    """Import swarmrouter lazily; raise a clear error when the extra is missing."""
    global _swarmrouter
    if _swarmrouter is None:
        try:
            from swarmrouter import routing as swarmrouter_routing
        except ImportError as exc:
            raise RuntimeError(
                "prismatic-router requires the 'swarmrouter' package, which ships in "
                "the 'primitives' extra: pip install 'prismatic-engine[primitives]'"
            ) from exc
        _swarmrouter = swarmrouter_routing
    return _swarmrouter


PLUGIN_NAME = "prismatic-router"
PLUGIN_VERSION = "0.1.0"

DECISION_MATCHED = "matched"
DECISION_DEFAULT = "default"
DECISION_NO_MATCH = "no-match"


def plugin_state_dir() -> Path:
    """State root for this plugin.

    Matches the lifecycle contract: ``$PRISMATIC_HOME/plugin-state/<name>/``.
    """
    home = Path(os.environ.get("PRISMATIC_HOME") or Path.home())
    return home / "plugin-state" / PLUGIN_NAME


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_rule(raw: Any) -> Dict[str, Any]:
    """Validate one routing rule; return the canonical rule dict.

    Raises PluginValidationError on any violation. Mirrors the manifest
    ``config_schema`` (the loader validates at attach; this is the defensive
    re-check inside on_init / on_resume).
    """
    if not isinstance(raw, dict):
        raise PluginValidationError(f"rule must be an object, got {type(raw).__name__}")

    request_class = raw.get("request_class")
    if not isinstance(request_class, str) or not request_class.strip():
        raise PluginValidationError("rule.request_class must be a non-empty string")

    capability = raw.get("capability")
    if not isinstance(capability, str) or not capability.strip():
        raise PluginValidationError(
            f"rule {request_class!r}: capability must be a non-empty string"
        )

    policy_notes = raw.get("policy_notes")
    if policy_notes is not None and not isinstance(policy_notes, str):
        raise PluginValidationError(
            f"rule {request_class!r}: policy_notes must be a string when set"
        )

    priority = raw.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        raise PluginValidationError(
            f"rule {request_class!r}: priority must be a non-negative integer"
        )

    return {
        "request_class": request_class,
        "capability": capability,
        "policy_notes": policy_notes,
        "priority": priority,
    }


def classify_request(
    rules: List[Dict[str, Any]],
    request_class: str,
    default_capability: Optional[str] = None,
) -> Dict[str, Any]:
    """Pure classification: (rules, input) -> advisory result.

    No side effects: no I/O, no mutation of ``rules`` or ``request_class``,
    no primitive import. Deterministic — the same inputs always produce the
    same output. Rules are evaluated highest-priority-first; ties break by
    config order; the first rule whose ``request_class`` exactly matches the
    input wins.
    """
    ordered = sorted(enumerate(rules), key=lambda t: (-t[1]["priority"], t[0]))
    matched = next(
        (rule for _, rule in ordered if rule["request_class"] == request_class),
        None,
    )

    if matched is not None:
        decision = DECISION_MATCHED
        capability = matched["capability"]
        policy_notes = matched.get("policy_notes")
        matched_rule: Optional[Dict[str, Any]] = {
            "request_class": matched["request_class"],
            "capability": matched["capability"],
            "policy_notes": matched.get("policy_notes"),
            "priority": matched["priority"],
        }
    elif default_capability:
        decision = DECISION_DEFAULT
        capability = default_capability
        policy_notes = None
        matched_rule = None
    else:
        decision = DECISION_NO_MATCH
        capability = None
        policy_notes = None
        matched_rule = None

    return {
        "advisory": True,
        "request_class": request_class,
        "decision": decision,
        "matched_rule": matched_rule,
        "capability": capability,
        "policy_notes": policy_notes,
    }


class RouterPlugin(PrismaticPlugin):
    """Router capability plugin — advisory ingress classification.

    It classifies, never dispatches: there is deliberately no dispatch,
    execute, or enqueue method or route anywhere on this class.
    """

    def __init__(self) -> None:
        # Canonical rule dicts (see validate_rule). Never mutated in place
        # after load: classify() only reads them, so route handlers are
        # safe to call from any thread.
        self._rules: List[Dict[str, Any]] = []
        self._default_capability: Optional[str] = None
        self._state_dir = plugin_state_dir()
        # True when on_init received an explicit "rules" key (even an empty
        # list). Distinguishes "operator configured zero rules" from "no
        # config provided", so on_resume() knows when it may fall back to
        # the suspend snapshot for rule definitions.
        self._rules_from_config = False

    # ── lifecycle: init / suspend / resume ──────────────────────────────

    def on_init(self, context: PluginContext) -> None:
        """Validate config rules and store the rule table.

        The generic PluginLoader passes the whole core config as
        ``context.config`` with this plugin's validated config nested at
        ``context.config["plugin_configs"]["prismatic-router"]``; standalone
        use may pass the plugin config directly. Support both.
        """
        core_config = context.config or {}
        config = core_config.get("plugin_configs", {}).get(PLUGIN_NAME, core_config)
        rules_cfg = config.get("rules", [])
        if not isinstance(rules_cfg, list):
            raise PluginValidationError("config.rules must be an array")
        self._rules_from_config = "rules" in config

        default_capability = config.get("default_capability")
        if default_capability is not None and (
            not isinstance(default_capability, str) or not default_capability.strip()
        ):
            raise PluginValidationError(
                "config.default_capability must be a non-empty string when set"
            )

        rules = [validate_rule(raw) for raw in rules_cfg]

        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._rules = rules
        self._default_capability = default_capability

    def on_suspend(self) -> Dict[str, Any]:
        """Return the JSON-serializable rule-table snapshot. No secrets."""
        snapshot = {
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "saved_at": _utcnow_iso(),
            "rules": [dict(rule) for rule in self._rules],
            "default_capability": self._default_capability,
        }
        return json.loads(json.dumps(snapshot, default=str))

    def on_resume(self, state: Dict[str, Any]) -> None:
        """Restore the rule table from a suspend snapshot.

        The operator config is authoritative — on_init already loaded it
        and the loader re-supplies the recorded config on enable-after-
        unload. The snapshot's rules are only a fallback: they are restored
        when this instance has no rules AND on_init did not receive an
        explicit "rules" key (so an intentionally-emptied config stays
        empty instead of resurrecting old definitions).
        """
        rules_cfg = state.get("rules") or []
        if rules_cfg and not self._rules and not self._rules_from_config:
            self._rules = [validate_rule(raw) for raw in rules_cfg]
            default_capability = state.get("default_capability")
            if default_capability is not None:
                if (
                    not isinstance(default_capability, str)
                    or not default_capability.strip()
                ):
                    raise PluginValidationError(
                        "state.default_capability must be a non-empty string when set"
                    )
                self._default_capability = default_capability

    # ── classification ──────────────────────────────────────────────────

    def classify(self, request_class: str) -> Dict[str, Any]:
        """Advisory classification of one request class. No side effects.

        Raises RuntimeError when the swarmrouter primitive is absent: the
        classification *service* consults the primitive's capability
        taxonomy (``swarmrouter.routing.capability_known``) for an advisory
        annotation. The underlying mapping stays a pure function of
        (rules, input) — see :func:`classify_request`.
        """
        _swarmrouter_or_raise()  # fail fast with a clear message when absent
        result = classify_request(self._rules, request_class, self._default_capability)
        routing = _swarmrouter_or_raise()
        check = getattr(routing, "capability_known", None)
        capability = result["capability"]
        result["capability_known"] = (
            bool(check(capability)) if (callable(check) and capability) else None
        )
        result["primitive"] = "swarmrouter"
        return result

    # ── plugin-registered API surface (no core edits) ───────────────────

    def rules_table(self) -> Dict[str, Any]:
        """Payload for GET /api/router/rules."""
        return {
            "rules": [dict(rule) for rule in self._rules],
            "default_capability": self._default_capability,
        }

    def classify_endpoint(self, payload: Any) -> Dict[str, Any]:
        """Handler for POST /api/router/classify.

        Advisory dry-run classification. Never raises for bad input or a
        missing primitive: it returns an error payload (never a 500 with a
        traceback) so the route stays a pure advisory read.
        """
        if not isinstance(payload, dict):
            return {
                "error": "invalid payload",
                "detail": "request body must be a JSON object",
            }
        request_class = payload.get("request_class")
        if not isinstance(request_class, str) or not request_class.strip():
            return {
                "error": "invalid payload",
                "detail": "payload.request_class must be a non-empty string",
            }
        try:
            return self.classify(request_class)
        except RuntimeError as exc:
            return {"error": "primitive not installed", "detail": str(exc)}

    def register_api_routes(self) -> List[Dict[str, Any]]:
        """Route descriptors the PE Gateway exposes for this plugin."""
        return [
            {
                "method": "GET",
                "path": "/api/router/rules",
                "description": "Current rule table: request class → capability (+ policy notes, priority).",
                "handler": "router.plugin:RouterPlugin.rules_table",
            },
            {
                "method": "POST",
                "path": "/api/router/classify",
                "description": "Advisory dry-run classification: input → matched rule + capability. No side effects.",
                "handler": "router.plugin:RouterPlugin.classify_endpoint",
            },
        ]

    def register_tools(self) -> List[Dict[str, Any]]:
        """Classification is infrastructure, not an agent tool."""
        return []

    def capability_contract(self) -> Dict[str, Any]:
        """Machine-readable description of the router capability."""
        return {
            "capability": "policy-based-ingress-classification",
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": (
                "Maps an ingress request class onto the operator-configured "
                "capability that should handle it, with advisory policy "
                "notes. Classification is a pure function of (rules, input) "
                "— advisory dry-run only."
            ),
            "routes": self.register_api_routes(),
            "state_dir": str(self._state_dir),
            "tools": [],
            "notes": [
                "Disabled by default (auto_enable: false); operator enables explicitly.",
                "It classifies, never dispatches: no dispatch, execute, or enqueue path exists.",
                "Enforcement of routing decisions stays with the kernel gate / the harness.",
                "swarmrouter.routing.capability_known annotates results; it never changes the mapping.",
            ],
        }
