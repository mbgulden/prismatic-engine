"""HelloWorldPlugin — canonical reference plugin implementation.

Demonstrates ALL FOUR Sprint 1 contribution patterns on the
:class:`ReviewerRegistry`:

  1. ``register_secret_pattern`` — extra (regex, kind, severity) tuples that
     augment the built-in secret-detection set.
  2. ``register_check``          — callables that take a unified diff and
     return a list of QualityFinding-like objects.
  3. ``register_impact_rule``    — callables that override
     ``PipelineOrchestrator``'s impact classification for a specific PR.
  4. ``register_action_rule``    — callables that override
     ``PipelineOrchestrator``'s next-action decision (separate channel
     from impact rules — Gap 11).

Usage
-----
This plugin is auto-discovered by ``PluginLoader.scan_and_load_plugins`` via
its ``plugin-manifest.yaml``. The host dispatcher constructs a
``PluginContext`` and passes it to ``on_init``; this plugin then registers
all four patterns on whichever registry is exposed via the context.

The defensive ``getattr(context, "review_registry", None)`` lookup lets the
plugin run against either:

  * the production :class:`prismatic.core.dispatcher.DispatcherContext`
    (which exposes ``review_registry``), or
  * a bare :class:`prismatic.interface.plugin.PluginContext` from unit tests
    (which does not — see ``plugins/prismatic_hello_world/tests/test_hello_world.py``).

If no registry is available, ``on_init`` is a silent no-op so the plugin can
still load without crashing. This is intentional: the PluginLoader wraps
``on_init`` in try-catch isolation and a missing registry is recoverable
(plugins register zero patterns in that environment).
"""

from __future__ import annotations

from typing import Any, Dict, List

from prismatic.interface.plugin import (
    AgentContract,
    PluginContext,
    PrismaticPlugin,
)


# ─────────────────────────────────────────────────────────────────────
# Pattern functions
# ─────────────────────────────────────────────────────────────────────


def no_hello_comments(diff: str) -> list:
    """Canonical quality-check callable.

    Flags any line in the unified diff whose added content is a Python
    comment that contains the word ``hello`` (case-insensitive). This is
    a deliberately simple example so plugin authors can copy the shape.

    Returns a list of dicts (not QualityFinding dataclasses) so the plugin
    stays decoupled from the reviewer's internal types — the registry
    accepts any iterable of finding-like objects.
    """
    findings: list[dict[str, Any]] = []
    for line in diff.splitlines():
        # Only inspect added lines (unified-diff prefix '+', not '+++').
        if not line.startswith("+") or line.startswith("+++"):
            continue
        stripped = line[1:].lstrip()
        # Python comments start with '#'. Strip it before scanning.
        if not stripped.startswith("#"):
            continue
        body = stripped.lstrip("#").strip().lower()
        if "hello" in body:
            findings.append(
                {
                    "path": "<diff>",
                    "line": 0,
                    "severity": "warning",
                    "message": "hello-world reference check: avoid '# hello' comments",
                }
            )
    return findings


def escalate_when_hello(result: Any, current: str) -> str | None:
    """Impact-override rule.

    Treat any PR whose ``summary`` mentions ``hello`` as ``minor`` instead of
    the orchestrator's default classification. Returns ``None`` to defer to
    later rules / the built-in classifier when the summary doesn't match.
    """
    summary = getattr(result, "summary", "") or ""
    if "hello" in summary.lower():
        return "minor"
    return None


def force_rework_when_hello_world(result: Any, current: str) -> str | None:
    """Action-override rule.

    Force a ``rework`` action for any PR whose ``summary`` mentions the
    literal ``hello_world`` token. This exercises the action-rule channel
    independently of impact rules (Gap 11 — channels are separate).
    """
    summary = getattr(result, "summary", "") or ""
    if "hello_world" in summary.lower():
        return "rework"
    return None


# ─────────────────────────────────────────────────────────────────────
# Plugin class
# ─────────────────────────────────────────────────────────────────────


class HelloWorldPlugin(PrismaticPlugin):
    """Canonical reference plugin — registers one of each Sprint 1 pattern.

    Copy this class to start a new plugin. The minimum required surface is
    ``on_init`` (abstract) and ``register_tools`` (abstract); the other
    hooks have default no-op implementations in the base class.
    """

    # Display name (mirrors manifest `name` for logging)
    NAME = "prismatic-hello-world"

    def on_init(self, context: PluginContext) -> None:
        """Register all four contribution patterns on the review registry.

        The registry lookup is defensive: ``PluginContext`` doesn't declare
        a ``review_registry`` attribute, but the production dispatcher's
        richer context object does. Using ``getattr(..., None)`` lets the
        plugin load cleanly under bare ``PluginContext`` (e.g. in unit
        tests) without raising ``AttributeError``.

        If no registry is exposed, this hook is a silent no-op — the
        plugin still loads, registers zero patterns, and the host can
        continue normally. This is the intended fallback for environments
        that don't support plugin-contributed review patterns.
        """
        registry = getattr(context, "review_registry", None)
        if registry is None:
            return

        # 1. Custom secret pattern: a fictitious "hello_world_token" format.
        #    Format: "hello_" + 16 lowercase alphanumerics.
        registry.register_secret_pattern(
            regex=r"hello_[a-z0-9]{16}",
            kind="hello_world_token",
            severity="warning",
        )

        # 2. Custom quality check (registered under a stable name so
        #    later plugins can override it via the registry's dedup logic).
        registry.register_check(no_hello_comments, name="hello.no_comments")

        # 3. Impact-override rule (separate channel from action rules —
        #    Gap 11 ensures a rule returning an invalid IMPACT value is
        #    ignored rather than leaking into the action channel).
        registry.register_impact_rule(escalate_when_hello)

        # 4. Action-override rule (its own pool; runs after
        #    ``decide_next_action`` in ``PipelineOrchestrator.process``).
        registry.register_action_rule(force_rework_when_hello_world)

    def register_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions to append to agent contexts.

        The reference plugin exposes zero tools — it's purely a
        review-pipeline contributor. Plugin authors wiring LLM-callable
        tools should return a list of OpenAI / JSON-Schema tool dicts here.
        """
        return []

    # Optional lifecycle hooks — all no-ops for the canonical example.
    # Plugin authors override these as needed.

    def before_task_execution(self, contract: AgentContract) -> None:
        """Called immediately before an agent worker is spawned. No-op."""
        return

    def after_task_execution(
        self, contract: AgentContract, result: Dict[str, Any]
    ) -> None:
        """Called immediately after an agent worker exits. No-op."""
        return

    def on_state_transition(
        self, issue_id: str, from_state: str, to_state: str
    ) -> None:
        """Triggered on Linear ticket state change. No-op."""
        return


__all__ = [
    "HelloWorldPlugin",
    "escalate_when_hello",
    "force_rework_when_hello_world",
    "no_hello_comments",
]
