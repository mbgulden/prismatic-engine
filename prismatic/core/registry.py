"""
PluginLoader — dynamic plugin discovery, validation, and registration.

Scans ``$PRISMATIC_HOME/plugins/`` for ``plugin-manifest.yaml`` files,
validates version constraints, dynamically imports Python modules, and
exposes loaded plugins, registered personas, and tools to the event loop.

All hook execution is wrapped in try-catch isolation — a crashing plugin
never brings down the dispatcher daemon.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from prismatic.interface.plugin import (
    PluginContext,
    AgentContract,
    PrismaticPlugin,
    PluginValidationError,
)

logger = logging.getLogger("prismatic.loader")


class PluginLoader:
    """
    Orchestrates discovery, validation, and dynamic loading of Prismatic
    plugins from the target plugin directory.
    """

    def __init__(self, core_version: str, plugins_dir: str) -> None:
        self.core_version = core_version
        self.plugins_dir = plugins_dir
        self.loaded_plugins: Dict[str, PrismaticPlugin] = {}
        self.registered_personas: Dict[str, Dict[str, Any]] = {}
        self.registered_tools: List[Dict[str, Any]] = []

    # ── public API ─────────────────────────────────────────────────────

    def scan_and_load_plugins(self, context: PluginContext) -> None:
        """
        Scan ``$PRISMATIC_HOME/plugins/`` for ``plugin-manifest.yaml``
        files, validate requirements, and dynamically register plugins.
        """
        if not os.path.exists(self.plugins_dir):
            logger.warning(
                "Plugin directory does not exist: %s", self.plugins_dir
            )
            return

        for entry in os.scandir(self.plugins_dir):
            if not entry.is_dir():
                continue

            manifest_path = Path(entry.path) / "plugin-manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                self._load_plugin(manifest_path, context)
            except Exception:
                logger.error(
                    "Failed to load plugin from %s",
                    manifest_path.parent.name,
                    exc_info=True,
                )

    def execute_hook(self, hook_name: str, *args: Any, **kwargs: Any) -> None:
        """
        Execute *hook_name* across every registered plugin in try-catch
        isolation.  A single plugin failure is logged but does not
        interrupt the dispatcher event loop.
        """
        for name, plugin in self.loaded_plugins.items():
            if not hasattr(plugin, hook_name):
                continue
            try:
                hook_func = getattr(plugin, hook_name)
                hook_func(*args, **kwargs)
            except Exception:
                logger.error(
                    "Plugin '%s' failed during hook '%s'",
                    name,
                    hook_name,
                    exc_info=True,
                )

    # ── internal ───────────────────────────────────────────────────────

    def _load_plugin(
        self, manifest_path: Path, context: PluginContext
    ) -> None:
        with open(manifest_path, "r") as fh:
            manifest = yaml.safe_load(fh)

        name = manifest.get("name")
        version = manifest.get("version")
        entry_point = manifest.get("entry_point")
        core_constraint = manifest.get("core_version_constraint")

        if not all([name, version, entry_point, core_constraint]):
            raise PluginValidationError(
                f"Missing required fields in manifest: {manifest_path}"
            )

        # 1. Core version validation
        specifier = SpecifierSet(core_constraint)
        if Version(self.core_version) not in specifier:
            raise PluginValidationError(
                f"Core version '{self.core_version}' does not satisfy "
                f"constraint '{core_constraint}' for plugin '{name}'."
            )

        # 2. Dynamic import
        module_path, class_name = entry_point.split(":")
        plugin_root = str(manifest_path.parent)

        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        try:
            module = importlib.import_module(module_path)
            plugin_class = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            raise PluginValidationError(
                f"Failed to import entry point '{entry_point}': {exc}"
            ) from exc

        if not issubclass(plugin_class, PrismaticPlugin):
            raise PluginValidationError(
                f"Class '{class_name}' must inherit from PrismaticPlugin."
            )

        # 3. Instantiate + fire on_init in isolation
        plugin_instance = plugin_class()
        try:
            plugin_instance.on_init(context)
        except Exception as exc:
            raise PluginValidationError(
                f"Exception raised during on_init execution: {exc}"
            ) from exc

        self.loaded_plugins[name] = plugin_instance

        # 4. Register personas
        for persona in manifest.get("personas", []):
            persona_id = persona.get("id")
            self.registered_personas[persona_id] = persona
            logger.info(
                "Registered persona '%s' from plugin '%s'",
                persona_id,
                name,
            )

        # 5. Register tools
        try:
            tools = plugin_instance.register_tools()
            self.registered_tools.extend(tools)
        except Exception:
            logger.error(
                "Plugin '%s' failed to register tools", name, exc_info=True
            )

        logger.info("Successfully loaded plugin '%s' (v%s)", name, version)
