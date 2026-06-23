"""
PrismaticPlugin — Abstract Base Class for all Prismatic Engine plugins.

This module defines the contract that every plugin must fulfill.
Plugins are discovered dynamically via ``plugin-manifest.yaml`` files
inside ``$PRISMATIC_HOME/plugins/`` and loaded by
``prismatic.core.registry.PluginLoader``.

Usage
-----
.. code-block:: python

    from prismatic.interface.plugin import PrismaticPlugin, PluginContext

    class MyPlugin(PrismaticPlugin):
        def on_init(self, context: PluginContext) -> None:
            ...   # set up databases, connections, compile libs

        def register_tools(self) -> List[Dict[str, Any]]:
            return [{"name": "my_tool", "description": "...", "parameters": {...}}]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PluginContext:
    """
    Context provided to plugins during initialization.

    Contains runtime configurations, shared state databases, and helper
    clients.  The *config* dict carries the entire core configuration
    that was loaded at dispatcher start-up.
    """

    config: Dict[str, Any]
    db_connection: Any
    state_dir: str
    telemetry_client: Optional[Any] = None
    lock_manager: Optional[Any] = None


@dataclass
class AgentContract:
    """
    Contract defining directory access permissions and action limits for
    an agent run.

    Mirrors the contract pattern used by the Hub's ``ContractManager``
    so that tools registered by plugins can perform path-boundary
    enforcement without needing to understand the full system topology.
    """

    thread_id: str
    persona_id: str
    allowed_dirs: List[str] = field(default_factory=list)
    read_only_dirs: List[str] = field(default_factory=list)
    max_actions: int = 10
    execution_env: str = "production"


class PluginValidationError(Exception):
    """Raised when plugin validation or load operation fails."""


class PrismaticPlugin(ABC):
    """
    Abstract Base Class for all Prismatic Engine plugins.

    Plugins must inherit from this class and implement lifecycle hooks.
    The core calls these hooks in try-catch isolation so that a
    misbehaving plugin never crashes the dispatcher event loop.
    """

    # ── mandatory hooks ──────────────────────────────────────────────────

    @abstractmethod
    def on_init(self, context: PluginContext) -> None:
        """
        Executed when the core dispatcher initializes.

        Used to set up databases, establish connections, or compile
        libraries.  The *context* provides access to the core
        configuration, database handles, and optional telemetry / lock
        managers.
        """
        ...

    @abstractmethod
    def register_tools(self) -> List[Dict[str, Any]]:
        """
        Return a list of tool definitions to append to agent contexts.

        Tool definitions must comply with the OpenAI / JSON Schema
        format.  Return an empty list if the plugin does not expose any
        tools.
        """
        return []

    # ── optional lifecycle hooks ─────────────────────────────────────────

    def before_task_execution(self, contract: AgentContract) -> None:
        """
        Called immediately before an agent worker is spawned.

        Allows setting up lock variables, temporary files, or
        environment constraints scoped to the upcoming task.
        """
        return

    def after_task_execution(
        self, contract: AgentContract, result: Dict[str, Any]
    ) -> None:
        """
        Called immediately after an agent worker exits.

        Enables cleanup, log collection, or metrics collection.  The
        *result* dictionary contains the worker's exit payload.
        """
        return

    def on_state_transition(
        self, issue_id: str, from_state: str, to_state: str
    ) -> None:
        """
        Triggered when a Linear ticket or task changes status.

        Useful for syncing observability logs or archiving build
        outputs in response to workflow transitions.
        """
        return

    # ── GRO-1497 dispatcher hooks (optional) ────────────────────────────

    def on_issue_dispatch(
        self, issue_id: str, agent_name: str, payload: Dict[str, Any]
    ) -> None:
        """Called immediately after an issue is dispatched to a provider."""
        return

    def on_review_complete(
        self,
        issue_id: str,
        origin_agent: str,
        reviewer_agent: str,
        results: Dict[str, Any],
    ) -> None:
        """Called when a reviewer agent completes and signals the origin agent."""
        return

    def on_pipeline_stage(
        self,
        issue_id: str,
        stage_name: str,
        status: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Called when a pipeline stage starts, fails, or completes."""
        return

    def on_credit_threshold(
        self,
        thread_id: str,
        provider: str,
        current_spend: int,
        limit: int,
        op: str,
    ) -> None:
        """Called when credit policy checks trigger warnings or denials."""
        return

    # ── GRO-2228 PWP pipeline hooks (optional) ──────────────────────────

    def on_pre_pipeline(
        self, pipeline_id: str, context: Dict[str, Any]
    ) -> None:
        """
        Fired exactly once, *before* any pipeline stage runs.

        See :data:`prismatic.interface.hooks.HOOK_ON_PRE_PIPELINE` for the
        full PWP contract.  Default no-op.
        """
        return

    def on_post_pipeline(
        self, pipeline_id: str, result: Dict[str, Any]
    ) -> None:
        """
        Fired exactly once, *after* all pipeline stages complete
        successfully.  Default no-op.
        """
        return

    def on_error(
        self, pipeline_id: str, exc: BaseException, stage: str
    ) -> None:
        """
        Fired when a pipeline stage raises an unhandled exception.

        Default no-op.  See :data:`prismatic.interface.hooks.HOOK_ON_ERROR`.
        """
        return

    def on_deploy(
        self, pipeline_id: str, target: str, artifact: Dict[str, Any]
    ) -> None:
        """
        Fired after the post-pipeline publish step pushes artifacts.

        Default no-op.  See :data:`prismatic.interface.hooks.HOOK_ON_DEPLOY`.
        """
        return
