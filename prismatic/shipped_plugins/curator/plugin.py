"""prismatic-curator — advisory-only capability recommendation plugin.

Suggests which registered capability fits a request. It reads the plugin
catalog (the loader's registered capability contracts, supplied as a
read-only snapshot) and returns ranked suggestions with reasons.

Scope guard (non-negotiable): this plugin is ADVISORY ONLY. It recommends;
it never selects, attaches, enables, or rewires anything. There are no
write routes — the route table is GET-only — and no code path in this
module mutates plugin or loader state. The moment it acted on its own
recommendations it would cross into harness territory, so it does not.

Read path: catalog snapshot in -> ranked suggestions out. Suggestions are
stateless; the only persisted state is a minimal config echo from
on_suspend().
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from prismatic.interface.plugin import (
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)

# NOTE: swarmcurator is intentionally NOT imported at module top level. It
# ships in the ``primitives`` extra, not the base install, and the
# PluginLoader imports every plugin module at scan time — a top-level
# ``import swarmcurator`` here would crash base installs. Every use site
# calls _swarmcurator_or_raise() first; the import is cached after the
# first successful call.
#
# The primitive is used for one narrow job: deterministic suggestion
# fingerprinting (swarmcurator.models.compute_fingerprint), so callers can
# cache or dedupe suggestion sets. Ranking itself is a small transparent
# scorer below — an advisory plugin's ranking must stay auditable, not
# hidden inside a dependency.

_swarmlib: Optional[tuple] = None


def _swarmcurator_or_raise() -> tuple:
    """Import swarmcurator lazily; raise a clear error when the extra is missing."""
    global _swarmlib
    if _swarmlib is None:
        try:
            from swarmcurator.models import compute_fingerprint
        except ImportError as exc:
            raise RuntimeError(
                "prismatic-curator requires the 'swarmcurator' package, which ships in "
                "the 'primitives' extra: pip install 'prismatic-engine[primitives]'"
            ) from exc
        _swarmlib = (compute_fingerprint,)
    return _swarmlib


PLUGIN_NAME = "prismatic-curator"
PLUGIN_VERSION = "0.1.0"

DEFAULT_MAX_SUGGESTIONS = 5
MAX_SUGGESTIONS_LIMIT = 25

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Field weights for the transparent term-overlap scorer.
_WEIGHT_NAME = 3.0
_WEIGHT_DESCRIPTION = 2.0
_WEIGHT_TAGS = 1.0


def _tokens(text: Any) -> List[str]:
    """Lowercase alphanumeric tokens from free text."""
    if text is None:
        return []
    return _TOKEN_RE.findall(str(text).lower())


def _validate_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Defensively validate operator config; raise PluginValidationError."""
    cfg = dict(config or {})
    max_suggestions = cfg.get("max_suggestions", DEFAULT_MAX_SUGGESTIONS)
    if isinstance(max_suggestions, bool) or not isinstance(max_suggestions, int):
        raise PluginValidationError(
            f"'max_suggestions' must be an integer, got {max_suggestions!r}."
        )
    if not 1 <= max_suggestions <= MAX_SUGGESTIONS_LIMIT:
        raise PluginValidationError(
            f"'max_suggestions' must be between 1 and {MAX_SUGGESTIONS_LIMIT}, "
            f"got {max_suggestions}."
        )
    return {"max_suggestions": max_suggestions}


def _score_entry(
    query_tokens: List[str], name: str, contract: Dict[str, Any]
) -> Tuple[float, List[str]]:
    """Score one catalog entry against query tokens.

    Transparent term overlap: each query token contributes at most once,
    at the highest-weight field it matches (name > description > tags /
    keywords). Returns (score, reasons).
    """
    name_tokens = set(_tokens(name))
    description_tokens = set(_tokens(contract.get("description", "")))
    tag_tokens = set(_tokens(contract.get("tags", [])))
    tag_tokens.update(_tokens(contract.get("keywords", [])))

    score = 0.0
    reasons: List[str] = []
    for token in query_tokens:
        if token in name_tokens:
            score += _WEIGHT_NAME
            reasons.append(f"name matches '{token}'")
        elif token in description_tokens:
            score += _WEIGHT_DESCRIPTION
            reasons.append(f"description matches '{token}'")
        elif token in tag_tokens:
            score += _WEIGHT_TAGS
            reasons.append(f"tags match '{token}'")
    return score, reasons


class CuratorPlugin(PrismaticPlugin):
    """Advisory-only capability recommender.

    Lifecycle: on_init validates config; refresh_catalog() takes a
    read-only snapshot of the loader's registered capability contracts;
    suggest()/catalog() serve the GET routes; on_suspend() returns a
    minimal JSON-serializable config echo (no secrets — suggestions are
    stateless).
    """

    def __init__(self) -> None:
        self._max_suggestions = DEFAULT_MAX_SUGGESTIONS
        self._catalog: Dict[str, Dict[str, Any]] = {}

    # ── lifecycle ─────────────────────────────────────────────────────

    def on_init(self, context: PluginContext) -> None:
        """Validate operator config. Raises PluginValidationError."""
        raw = (context.config or {}).get("plugin_configs", {}).get(PLUGIN_NAME, {})
        # Also accept a flat config (tests and direct construction).
        if not raw and isinstance(context.config, dict):
            maybe = {k: v for k, v in context.config.items() if k == "max_suggestions"}
            raw = maybe or raw
        self._max_suggestions = _validate_config(raw)["max_suggestions"]

    def register_tools(self) -> List[Dict[str, Any]]:
        """Recommendations are infrastructure, not agent tools."""
        return []

    def capability_contract(self) -> Dict[str, Any]:
        """Machine-readable contract; declares the advisory-only nature."""
        return {
            "description": (
                "Advisory-only capability recommendations: ranked suggestions "
                "with reasons, read from the plugin catalog. Never selects, "
                "attaches, enables, or rewires capabilities."
            ),
            "tags": ["infrastructure", "optional", "advisory"],
            "routes": ["/api/curator/suggest", "/api/curator/catalog"],
            "mutates_state": False,
            "write_routes": [],
        }

    # ── catalog (read-only snapshot of the loader's capability contracts) ──

    def refresh_catalog(
        self, contracts: Optional[Dict[str, Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """Replace the local catalog snapshot with a deep copy of the given
        capability contracts (the loader's ``registered_capability_contracts``).

        Read-only by construction: the caller's dicts are never mutated —
        the plugin keeps its own copy. The harness calls this before
        serving routes so suggestions track the currently registered set.
        """
        snapshot = copy.deepcopy(dict(contracts or {}))
        self._catalog = {
            str(name): contract
            for name, contract in snapshot.items()
            if isinstance(contract, dict)
        }
        return {"capabilities": len(self._catalog)}

    def catalog(self) -> Dict[str, Any]:
        """Payload for GET /api/curator/catalog: the snapshot being read."""
        entries = [
            {"name": name, "contract": copy.deepcopy(self._catalog[name])}
            for name in sorted(self._catalog)
        ]
        return {
            "capabilities": entries,
            "count": len(entries),
            "advisory_only": True,
        }

    # ── advisory suggestions (pure read path) ─────────────────────────

    def suggest(self, q: str = "", limit: Optional[int] = None) -> Dict[str, Any]:
        """Payload for GET /api/curator/suggest.

        Catalog in -> ranked suggestions out. Never mutates plugin or
        loader state; returns fresh objects on every call.
        """
        (compute_fingerprint,) = _swarmcurator_or_raise()
        query = (q or "").strip()
        if not query or not _tokens(query):
            return {
                "query": query,
                "suggestions": [],
                "catalog_size": len(self._catalog),
                "advisory_only": True,
            }
        query_tokens = _tokens(query)
        max_n = self._max_suggestions
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            max_n = min(limit, MAX_SUGGESTIONS_LIMIT)

        scored: List[Tuple[float, str, List[str]]] = []
        for name in sorted(self._catalog):
            score, reasons = _score_entry(query_tokens, name, self._catalog[name])
            if score > 0:
                scored.append((score, name, reasons))
        # Deterministic order: score desc, then name asc.
        scored.sort(key=lambda item: (-item[0], item[1]))

        suggestions = [
            {
                "capability": name,
                "score": round(score, 3),
                "reasons": list(reasons),
                "fingerprint": compute_fingerprint(PLUGIN_NAME, name, query),
                "advisory": True,
            }
            for score, name, reasons in scored[:max_n]
        ]
        return {
            "query": query,
            "suggestions": suggestions,
            "catalog_size": len(self._catalog),
            "advisory_only": True,
        }

    # ── plugin-registered API surface (no core edits; GET only) ────────

    def register_api_routes(self) -> List[Dict[str, Any]]:
        """Route descriptors the PE Gateway exposes for this plugin.

        GET-only by design: an advisory plugin must not expose any route
        that could change state.
        """
        return [
            {
                "method": "GET",
                "path": "/api/curator/suggest",
                "description": (
                    "Ranked advisory capability suggestions for ?q=<query>, "
                    "each with score, reasons, and a stable fingerprint. "
                    "Read-only: never selects or attaches a capability."
                ),
                "handler": "curator.plugin:CuratorPlugin.suggest",
            },
            {
                "method": "GET",
                "path": "/api/curator/catalog",
                "description": (
                    "The capability catalog snapshot the suggester reads from "
                    "(names plus their registered capability contracts)."
                ),
                "handler": "curator.plugin:CuratorPlugin.catalog",
            },
        ]

    # ── suspend / resume ──────────────────────────────────────────────

    def on_suspend(self) -> Dict[str, Any]:
        """Return JSON-serializable state: a minimal config echo.

        Suggestions are stateless; the catalog snapshot is re-supplied by
        the harness via refresh_catalog(), so it is not persisted — only
        the capability names are recorded for operator visibility. No
        secrets are ever stored here.
        """
        snapshot = {
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "config": {"max_suggestions": self._max_suggestions},
            "catalog_names": sorted(self._catalog),
            "advisory_only": True,
        }
        return json.loads(json.dumps(snapshot))

    def on_resume(self, state: Dict[str, Any]) -> None:
        """Restore the config echo from a suspend snapshot."""
        try:
            max_suggestions = (state or {}).get("config", {}).get("max_suggestions")
            if isinstance(max_suggestions, int) and not isinstance(
                max_suggestions, bool
            ):
                if 1 <= max_suggestions <= MAX_SUGGESTIONS_LIMIT:
                    self._max_suggestions = max_suggestions
        except (AttributeError, TypeError):
            pass
