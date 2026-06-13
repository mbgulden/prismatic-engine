"""
Canonical hook names and type stubs for the Prismatic plugin system.

Plugins subscribe to hooks by including the hook name in their
``plugin-manifest.yaml`` ``hooks`` list.  The core dispatcher calls
``PluginLoader.execute_hook(hook_name, ...)`` at the appropriate lifecycle
points.
"""

from typing import Any, Dict, List, Optional

# ── canonical hook-name constants ────────────────────────────────────────────

HOOK_ON_INIT                 = "on_init"
HOOK_BEFORE_TASK_EXECUTION   = "before_task_execution"
HOOK_AFTER_TASK_EXECUTION    = "after_task_execution"
HOOK_ON_STATE_TRANSITION     = "on_state_transition"

HOOK_NAMES: List[str] = [
    HOOK_ON_INIT,
    HOOK_BEFORE_TASK_EXECUTION,
    HOOK_AFTER_TASK_EXECUTION,
    HOOK_ON_STATE_TRANSITION,
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
