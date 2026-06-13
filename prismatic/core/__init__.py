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
    "PluginLoader",
    "validate_path",
    "SecurityException",
    "SwarmLockManager",
    "Dispatcher",
]

from .registry import PluginLoader
from .contracts import validate_path, SecurityException
from .locking import SwarmLockManager
from .dispatcher import Dispatcher
