"""
Prismatic Engine Core — runtime services layer.

This package contains the concrete implementations that the dispatcher
daemon relies on at run time:  plugin loading, contract enforcement,
swarm locking, and the event-loop dispatcher itself.

Contents
--------
* **contracts.py**  — path-boundary validation (``validate_path``)
* **dispatcher.py** — polling event loop and task router (see also root ``prismatic/dispatcher.py``)
* **locking.py**    — ``SwarmLockManager`` workspace concurrency mutexes
* **registry.py**   — ``PluginLoader`` — scans, validates, loads plugins
"""

__all__ = [
    "MODEL_PRIORITY_CHAIN",
    "CircuitBreakerRouter",
    "CircuitBreakerState",
    "Dispatcher",
    "DistributedComputeGovernor",
    "HardwareProfile",
    "HardwareProfileError",
    "HardwareProfileRegistry",
    "PluginLoader",
    "SecurityException",
    "SwarmLockManager",
    "check_and_route_agy",
    "get_router",
    "validate_path",
]

from .contracts import SecurityException, validate_path
from .dispatcher import Dispatcher
from .governor import DistributedComputeGovernor
from .hardware_profiles import (
    HardwareProfile,
    HardwareProfileError,
    HardwareProfileRegistry,
)
from .locking import SwarmLockManager
from .registry import PluginLoader
from .router import (
    MODEL_PRIORITY_CHAIN,
    CircuitBreakerRouter,
    CircuitBreakerState,
    check_and_route_agy,
    get_router,
)
