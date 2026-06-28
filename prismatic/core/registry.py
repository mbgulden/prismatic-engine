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
from prismatic.core.hardware_profiles import (
    HardwareProfileError,
    HardwareProfileRegistry,
)

logger = logging.getLogger("prismatic.loader")


# Known system-level capabilities the PluginLoader recognises. Plugins
# requesting a capability NOT in this set are warned (not rejected) so the
# loader can stay forward-compatible with hosts that add new capabilities.
_KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {
        # Built-in review subsystem capabilities (Gap 9+):
        "secret-scan-engine",
        "quality-check-engine",
        "impact-rule-engine",
        "action-rule-engine",
        # Generic system capabilities from GRO-1497 §3.1:
        "gpu",
        "git",
        "network",
        "filesystem-write",
        "process-spawn",
    }
)


class PluginLoader:
    """
    Orchestrates discovery, validation, and dynamic loading of Prismatic
    plugins from the target plugin directory.
    """

    def __init__(
        self,
        core_version: str,
        plugins_dir: str,
        hardware_registry: HardwareProfileRegistry | None = None,
    ) -> None:
        self.core_version = core_version
        self.plugins_dir = plugins_dir
        self.loaded_plugins: Dict[str, PrismaticPlugin] = {}
        self.registered_personas: Dict[str, Dict[str, Any]] = {}
        self.registered_tools: List[Dict[str, Any]] = []
        self.hardware_registry = hardware_registry

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

    def _resolve_environment_capabilities(
        self, context: PluginContext
    ) -> set[str]:
        """Return the set of capabilities the host currently provides.

        Reads ``context.config["environment_capabilities"]`` if present;
        falls back to a conservative default set that matches the
        review-subsystem capabilities (so HelloWorldPlugin and similar
        reference plugins can load under bare test contexts).
        """
        default_caps = {
            "secret-scan-engine",
            "quality-check-engine",
            "impact-rule-engine",
            "action-rule-engine",
        }
        cfg = getattr(context, "config", None) or {}
        declared = cfg.get("environment_capabilities")
        if isinstance(declared, (set, list, tuple, frozenset)):
            return set(declared) | default_caps
        return set(default_caps)

    def _resolve_active_provider(self, context: PluginContext) -> str | None:
        """Return the active LLM provider name, or None if unconfigured."""
        cfg = getattr(context, "config", None) or {}
        provider = cfg.get("active_provider")
        return provider if isinstance(provider, str) else None

    def _resolve_provider_version(
        self, context: PluginContext, provider: str
    ) -> str | None:
        """Return the active provider's version string, or None if unset."""
        cfg = getattr(context, "config", None) or {}
        versions = cfg.get("provider_versions")
        if isinstance(versions, dict):
            value = versions.get(provider)
            return value if isinstance(value, str) else None
        return None

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

        # 1b. Required-capabilities validation (Gap 10 — Plugin Discovery Hardening).
        #     Plugins declare what system features they need; the loader
        #     cross-checks against the environment capability set exposed
        #     via PluginContext. Capabilities the host doesn't recognise are
        #     warned (not rejected) so future host versions don't break
        #     existing plugins that pre-date the capability.
        required_caps = manifest.get("required_capabilities") or []
        if required_caps:
            env_caps = self._resolve_environment_capabilities(context)
            for cap in required_caps:
                if cap in env_caps:
                    continue
                if cap in _KNOWN_CAPABILITIES:
                    raise PluginValidationError(
                        f"Plugin '{name}' requires capability {cap!r} "
                        f"which is not available in the current environment "
                        f"(available: {sorted(env_caps)})."
                    )
                logger.warning(
                    "Plugin '%s' declares unknown required capability "
                    "%r — host does not recognise this capability. Plugin "
                    "will load, but its behaviour may be undefined.",
                    name,
                    cap,
                )

        # 1c. Provider-constraint validation (Gap 10).
        #     Plugins can list providers under `blocked_providers`; the
        #     loader inspects the runtime provider (exposed via the
        #     context) and refuses to load if the active provider is
        #     blocked. The plugin-manifest schema also accepts
        #     `provider_constraints` (a richer form allowing per-provider
        #     version constraints); both are honoured here.
        blocked = set(manifest.get("blocked_providers") or [])
        active_provider = self._resolve_active_provider(context)
        if active_provider and active_provider in blocked:
            raise PluginValidationError(
                f"Plugin '{name}' blocks active provider {active_provider!r} "
                f"(blocked_providers: {sorted(blocked)})."
            )

        provider_constraints = manifest.get("provider_constraints") or {}
        if active_provider and active_provider in provider_constraints:
            constraint_str = provider_constraints[active_provider]
            try:
                constraint = SpecifierSet(constraint_str)
            except Exception as exc:
                raise PluginValidationError(
                    f"Plugin '{name}' has invalid provider_constraint "
                    f"{constraint_str!r} for provider {active_provider!r}: "
                    f"{exc}"
                ) from exc
            provider_version = self._resolve_provider_version(
                context, active_provider
            )
            if provider_version is not None and Version(
                provider_version
            ) not in constraint:
                raise PluginValidationError(
                    f"Plugin '{name}' provider_constraint "
                    f"{constraint_str!r} not satisfied for "
                    f"{active_provider}=={provider_version}."
                )

        # 1a. Hardware profile validation (optional)
        for field_name in ("hardware_profile", "execution_profile"):
            requested = manifest.get(field_name)
            if requested is not None:
                if self.hardware_registry is None:
                    logger.warning(
                        "Plugin '%s' declares '%s: %s' but no "
                        "HardwareProfileRegistry configured — skipping "
                        "validation.",
                        name,
                        field_name,
                        requested,
                    )
                elif not self.hardware_registry.is_valid(requested):
                    raise PluginValidationError(
                        f"Plugin '{name}' declares unknown "
                        f"'{field_name}: {requested}'. "
                        f"Known profiles: "
                        f"{', '.join(self.hardware_registry.profile_names)}"
                    )
                else:
                    logger.info(
                        "Plugin '%s' validated '%s: %s'",
                        name,
                        field_name,
                        requested,
                    )

        # 2. Dynamic import
        module_path, class_name = entry_point.split(":")
        # The plugin module lives at ``<plugin_dir>/<basename>.py`` where
        # ``plugin_dir`` is the manifest's parent directory. The dotted
        # module path (``module_path``) needs the directory ABOVE
        # ``plugin_dir`` on sys.path so the import resolves as a top-level
        # package. For ``plugins/prismatic_hello_world/plugin.py`` with
        # ``entry_point: prismatic_hello_world.plugin:HelloWorldPlugin``,
        # sys.path needs to include ``plugins/``.
        plugin_root = str(manifest_path.parent)
        parent_root = str(manifest_path.parent.parent)
        first_module_segment = module_path.split(".")[0]

        # If the plugin directory itself is named after the first segment
        # of the module path (e.g. plugin_dir == "prismatic_hello_world"
        # and first_module_segment == "prismatic_hello_world"), then the
        # dotted import resolves only if the PARENT directory is on
        # sys.path. Otherwise (single-segment module names like
        # ``my_module:MyPlugin`` with my_module.py directly inside the
        # plugin_dir), the plugin_dir itself goes on sys.path.
        if Path(plugin_root).name == first_module_segment:
            if parent_root not in sys.path:
                sys.path.insert(0, parent_root)
        elif plugin_root not in sys.path:
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
