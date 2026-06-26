"""
pwp.plugin — PwpPlugin (Prismatic Web Plugin) implementation.

This module is the entry point declared in plugin-manifest.yaml
(`entry_point: "pwp.plugin:PwpPlugin"`). The PluginLoader dynamically
imports this module and instantiates ``PwpPlugin`` so the plugin can
participate in the engine's lifecycle.

Capabilities exposed
--------------------
* ``register_tools()`` returns the ``pwp_pipeline`` tool (and
  ``pwp_health`` for re-running the health check on demand).
* ``on_init()`` performs a health check; failures are logged but do NOT
  raise — the engine runs plugins in try-catch isolation so a misconfigured
  PWP install cannot bring down the dispatcher.

Source material
---------------
* GRO-2506 — PWP-I14: PWP plugin packaging (this issue)
* GRO-2491 — PWP project root (Astro + EmDash editable site kernel)
* OKF ADR: ``okf/projects/prismatic-web-plugin/decisions/2026-06-26-astro-emdash-pwp-standard.md``
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from prismatic.interface.plugin import (
    AgentContract,
    PluginContext,
    PrismaticPlugin,
)

# Health check is in a sibling module so it can be re-used by the
# install command without re-importing this file.
from pwp.health import check as _health_check  # noqa: E402

logger = logging.getLogger("pwp.plugin")


class PwpPlugin(PrismaticPlugin):
    """Prismatic Web Plugin — durable website-production system."""

    # Capability declarations mirror the manifest so the runtime can
    # introspect without re-parsing YAML.
    REQUIRED_CAPABILITIES = (
        "cloudflare.api",
        "cloudflare.account",
        "filesystem.workspace",
        "okf.read",
        "linear.api",
    )
    OPTIONAL_CAPABILITIES = ("github.api",)

    def __init__(self) -> None:
        self._last_health: List[Dict[str, Any]] = []
        self._context: PluginContext | None = None

    # ── mandatory hooks ─────────────────────────────────────────────────

    def on_init(self, context: PluginContext) -> None:
        """Initialize the plugin.

        Runs the health check and logs results. Failures here are
        captured but do not raise — the engine isolates plugin init.
        """
        self._context = context
        results = _health_check()
        self._last_health = results
        failed = [r for r in results if r.get("status") == "fail"]
        if failed:
            logger.warning(
                "PWP plugin initialized with %d missing capability row(s): %s",
                len(failed),
                ", ".join(r["id"] for r in failed),
            )
            for row in failed:
                logger.warning(
                    "  [%s] %s — missing: %s",
                    row["id"],
                    row.get("description", ""),
                    ", ".join(row.get("missing", [])),
                )
        else:
            logger.info("PWP plugin initialized; all required capabilities present.")

    def register_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions registered for agent contexts.

        Tools follow the OpenAI / JSON Schema shape so any provider
        can consume them without coupling.
        """
        return [
            {
                "name": "pwp_health",
                "description": (
                    "Run the PWP plugin health check and return a list of "
                    "capability rows. Use this before dispatching a PWP task "
                    "to surface missing credentials or filesystem paths."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "name": "pwp_pipeline",
                "description": (
                    "Run a PWP pipeline stage (ingest | synthesize | distill | "
                    "scaffold | staging | approval | production) for a client site. "
                    "Returns a stage-summary dict; raises on hard failure."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "stage": {
                            "type": "string",
                            "enum": [
                                "ingest",
                                "synthesize",
                                "distill",
                                "scaffold",
                                "staging",
                                "approval",
                                "production",
                            ],
                            "description": "Pipeline stage to execute.",
                        },
                        "client_slug": {
                            "type": "string",
                            "description": "URL-safe client identifier (e.g. 'active-oahu-tours').",
                        },
                    },
                    "required": ["stage", "client_slug"],
                    "additionalProperties": False,
                },
            },
        ]

    # ── optional lifecycle hooks ───────────────────────────────────────

    def before_task_execution(self, contract: AgentContract) -> None:
        """Pre-task hook — log the persona and allowed directories."""
        logger.info(
            "PWP before_task_execution: thread=%s persona=%s allowed=%s",
            contract.thread_id,
            contract.persona_id,
            contract.allowed_dirs,
        )

    def after_task_execution(
        self, contract: AgentContract, result: Dict[str, Any]
    ) -> None:
        """Post-task hook — log the worker result for forensics."""
        logger.info(
            "PWP after_task_execution: thread=%s status=%s",
            contract.thread_id,
            result.get("status", "unknown"),
        )

    def on_state_transition(
        self, issue_id: str, from_state: str, to_state: str
    ) -> None:
        """Surface Linear state transitions to the plugin so it can
        trigger pipeline stages when an issue moves to ``In Review`` or
        ``Done`` (e.g. fire a staging deploy)."""
        logger.info(
            "PWP on_state_transition: %s %s → %s",
            issue_id,
            from_state,
            to_state,
        )