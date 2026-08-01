"""
Canonical hook names and type stubs for the Prismatic plugin system.

Plugins subscribe to hooks by including the hook name in their
``plugin-manifest.yaml`` ``hooks`` list.  The core dispatcher calls
``PluginLoader.execute_hook(hook_name, ...)`` at the appropriate lifecycle
points.

GRO-2228: PWP (Prismatic Web Plugin) hook set
---------------------------------------------
The 4 PWP hooks are fired by the orchestrator at pipeline boundaries so
that the web-publishing pipeline (and any other plugin that runs
multi-stage work) can observe and react to lifecycle events without
having to monkey-patch the dispatcher itself.

The 4 PWP hooks, in the order they fire during a normal pipeline run:

  1. ``on_pre_pipeline``   — fires once, before any pipeline stage starts
  2. ``on_post_pipeline``  — fires once, after all stages complete successfully
  3. ``on_error``          — fires when any stage raises (replaces the normal
                              ``on_post_pipeline`` for the failed run)
  4. ``on_deploy``         — fires after the post-pipeline publish step
                              pushes artifacts to a deployment target

The PWP set is orthogonal to the GRO-1497 hook set (issue dispatch,
review, credit threshold); both can be subscribed to in the same plugin
manifest.
"""

from typing import Any, Dict, List, Optional

# ── canonical hook-name constants ────────────────────────────────────────────

# Core lifecycle (existed pre-GRO-2228)
HOOK_ON_INIT                 = "on_init"
HOOK_BEFORE_TASK_EXECUTION   = "before_task_execution"
HOOK_AFTER_TASK_EXECUTION    = "after_task_execution"
HOOK_ON_STATE_TRANSITION     = "on_state_transition"

# GRO-1497 dispatcher hooks
HOOK_ON_ISSUE_DISPATCH       = "on_issue_dispatch"
HOOK_ON_REVIEW_COMPLETE      = "on_review_complete"
HOOK_ON_PIPELINE_STAGE       = "on_pipeline_stage"
HOOK_ON_CREDIT_THRESHOLD     = "on_credit_threshold"

# GRO-2228 PWP pipeline hooks
HOOK_ON_PRE_PIPELINE         = "on_pre_pipeline"
HOOK_ON_POST_PIPELINE        = "on_post_pipeline"
HOOK_ON_ERROR                = "on_error"
HOOK_ON_DEPLOY               = "on_deploy"

HOOK_NAMES: List[str] = [
    HOOK_ON_INIT,
    HOOK_BEFORE_TASK_EXECUTION,
    HOOK_AFTER_TASK_EXECUTION,
    HOOK_ON_STATE_TRANSITION,
    HOOK_ON_ISSUE_DISPATCH,
    HOOK_ON_REVIEW_COMPLETE,
    HOOK_ON_PIPELINE_STAGE,
    HOOK_ON_CREDIT_THRESHOLD,
    HOOK_ON_PRE_PIPELINE,
    HOOK_ON_POST_PIPELINE,
    HOOK_ON_ERROR,
    HOOK_ON_DEPLOY,
]

# ── PWP hook grouping helpers ───────────────────────────────────────────────
# Use these when a plugin manifest only wants to opt in to the PWP set.

PWP_HOOK_NAMES: List[str] = [
    HOOK_ON_PRE_PIPELINE,
    HOOK_ON_POST_PIPELINE,
    HOOK_ON_ERROR,
    HOOK_ON_DEPLOY,
]

# ── type stubs ───────────────────────────────────────────────────────────────
# These mirror the signatures that PluginLoader.execute_hook() forwards.

def on_init(context: Any) -> None:
    """Executed when the core dispatcher initializes."""

def before_task_execution(contract: Any) -> None:
    """Called immediately before an agent worker is spawned."""

def after_task_execution(contract: Any, result: Optional[Dict[str, Any]] = None) -> None:
    """Called immediately after an agent worker exits."""

def on_state_transition(issue_id: str, from_state: str, to_state: str) -> None:
    """Triggered when a Linear ticket or task changes status."""


# ── GRO-1497 dispatcher hook stubs ──────────────────────────────────────────

def on_issue_dispatch(issue_id: str, agent_name: str, payload: Dict[str, Any]) -> None:
    """Fired when an issue is dispatched to a provider/agent runner."""

def on_review_complete(
    issue_id: str,
    origin_agent: str,
    reviewer_agent: str,
    results: Dict[str, Any],
) -> None:
    """Fired when a peer review cycle finishes and results route back."""

def on_pipeline_stage(
    issue_id: str,
    stage_name: str,
    status: str,
    metadata: Dict[str, Any],
) -> None:
    """Fired when a pipeline stage starts, fails, or completes."""

def on_credit_threshold(
    thread_id: str,
    provider: str,
    current_spend: int,
    limit: int,
    op: str,
) -> None:
    """Fired when credit consumption crosses a safety threshold."""


# ── GRO-2228 PWP hook stubs ─────────────────────────────────────────────────

def on_pre_pipeline(pipeline_id: str, context: Dict[str, Any]) -> None:
    """
    Fired exactly once, *before* any pipeline stage runs.

    Plugins that need to set up per-pipeline resources (temp dirs,
    feature flags, build context, etc.) should subscribe to this hook.

    Args:
        pipeline_id: Unique identifier for the pipeline run (typically
            ``{issue_id}-{timestamp}`` or a UUID).
        context: Arbitrary metadata describing the pipeline (issue ID,
            branch, target environment, etc.).  Read-only — plugins
            should not mutate this dict.
    """

def on_post_pipeline(pipeline_id: str, result: Dict[str, Any]) -> None:
    """
    Fired exactly once, *after* all pipeline stages complete successfully.

    The ``on_error`` hook will fire instead of this one if any stage
    raises — i.e. the two are mutually exclusive per pipeline run.

    Args:
        pipeline_id: Same ID passed to ``on_pre_pipeline``.
        result: Aggregated output of every stage.  Always contains at
            minimum ``{"status": "succeeded", "stages": [...]}``.
    """

def on_error(pipeline_id: str, exc: BaseException, stage: str) -> None:
    """
    Fired when a pipeline stage raises an unhandled exception.

    The PWP contract guarantees this fires *exactly once* per failed
    pipeline run, even if multiple stages raise.  Only the first
    exception is reported.

    Args:
        pipeline_id: Same ID passed to ``on_pre_pipeline``.
        exc: The exception instance that caused the failure.
        stage: Name of the stage that raised (e.g. ``"build"``).
    """

def on_deploy(pipeline_id: str, target: str, artifact: Dict[str, Any]) -> None:
    """
    Fired after the post-pipeline publish step pushes artifacts.

    Fires *after* ``on_post_pipeline`` on a successful run, and is
    skipped on a failed run.  A failed deploy itself does NOT
    re-trigger ``on_error`` — the deploy stage is treated as a
    best-effort notification.

    Args:
        pipeline_id: Same ID passed to ``on_pre_pipeline``.
        target: Deployment target identifier (e.g. ``"cloudflare-pages"``,
            ``"vercel"``, ``"s3://bucket/path"``).
        artifact: Dictionary describing the deployed artifact (URL,
            commit SHA, size, etc.).
    """
