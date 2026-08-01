"""
Prismatic Engine Core — plugin interface layer.

This package defines the ABC (Abstract Base Class) boundary between the
core dispatcher and dynamically loaded plugins.  Plugins MUST inherit from
``PrismaticPlugin`` and implement the required lifecycle hooks.

Contents
--------
* **plugin.py**  — PrismaticPlugin ABC, PluginContext, AgentContract
* **hooks.py**   — canonical hook-name constants + typing
"""

__all__ = ["PrismaticPlugin", "PluginContext", "AgentContract", "PluginValidationError",
           "HOOK_NAMES"]

from .plugin import PrismaticPlugin, PluginContext, AgentContract, PluginValidationError
from .hooks import HOOK_NAMES
