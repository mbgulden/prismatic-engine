"""
Prismatic Engine Core — runtime services layer.

This package contains the concrete implementations that the dispatcher
daemon relies on at run time: plugin loading and the AGY circuit breaker
router. Dead-code placeholders (formerly core/dispatcher.py, core/locking.py,
and core/contracts.py) were removed in GRO-XXXX — the legacy dispatcher
in prismatic/dispatcher.py and the lock CLI in prismatic/lock.py are the
real implementations.

Contents
--------
* **registry.py** — ``PluginLoader`` — scans, validates, loads plugins
* **router.py**  — ``CircuitBreakerRouter`` — AGY model fallback chain
"""

__all__ = [
    "PluginLoader",
    "CircuitBreakerRouter",
    "get_router",
    "check_and_route_agy",
    "CircuitBreakerState",
    "MODEL_PRIORITY_CHAIN",
]

from .registry import PluginLoader
from .router import (
    CircuitBreakerRouter,
    get_router,
    check_and_route_agy,
    CircuitBreakerState,
    MODEL_PRIORITY_CHAIN,
)
