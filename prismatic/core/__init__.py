"""
Prismatic Engine Core — runtime services layer.

This package contains the concrete implementations that the dispatcher
daemon relies on at run time:  plugin loading, contract enforcement,
swarm locking, and the event-loop dispatcher itself.

Contents
--------
* **compat.py**    — ``VersionResolver`` — semantic-version conflict detection and compatibility matrix
* **contracts.py**  — path-boundary validation (``validate_path``)
* **dispatcher.py** — polling event loop and task router (see also root ``prismatic/dispatcher.py``)
* **locking.py**    — ``SwarmLockManager`` workspace concurrency mutexes
* **registry.py**   — ``PluginLoader`` — scans, validates, loads plugins
"""

__all__ = [
    "PluginLoader",
    "validate_path",
    "SecurityException",
    "SwarmLockManager",
    "Dispatcher",
    "CircuitBreakerRouter",
    "get_router",
    "check_and_route_agy",
    "CircuitBreakerState",
    "MODEL_PRIORITY_CHAIN",
    "VersionResolver",
    "PluginVersionInfo",
    "ResolutionReport",
    "CompatibilityMatrix",
    "ConflictInfo",
    "ResolutionResult",
]

from .registry import PluginLoader
from .contracts import validate_path, SecurityException
from .locking import SwarmLockManager
from .dispatcher import Dispatcher
from .router import (
    CircuitBreakerRouter,
    get_router,
    check_and_route_agy,
    CircuitBreakerState,
    MODEL_PRIORITY_CHAIN,
)
from .compat import (
    VersionResolver,
    PluginVersionInfo,
    ResolutionReport,
    CompatibilityMatrix,
    ConflictInfo,
    ResolutionResult,
)
