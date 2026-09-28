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
import json
import logging
import os
import sys
from dataclasses import replace as _dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

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


# ── generic plugin lifecycle manager (suspend / resume / state) ─────────
#
# Suspend/resume state lives under ``$PRISMATIC_HOME/plugin-state/<name>/``,
# following the repo's PRISMATIC_HOME convention
# (``Path(os.environ.get("PRISMATIC_HOME") or Path.home())``).


def prismatic_home() -> Path:
    """Return the operator home directory used for plugin runtime state."""
    return Path(os.environ.get("PRISMATIC_HOME") or Path.home())


def plugin_state_file(plugin_name: str, home: Path | None = None) -> Path:
    """Return the path of the suspend/resume state file for *plugin_name*.

    Layout: ``$PRISMATIC_HOME/plugin-state/<plugin_name>/state.json``.
    """
    return (home or prismatic_home()) / "plugin-state" / plugin_name / "state.json"


_default_loader: "PluginLoader | None" = None


def get_default_plugin_loader() -> "PluginLoader | None":
    """Return the most recently constructed PluginLoader, if any.

    The gateway dashboard uses this to merge live loader lifecycle
    status into its plugin endpoints without requiring the loader to
    be passed through every call layer.
    """
    return _default_loader


def set_default_plugin_loader(loader: "PluginLoader | None") -> None:
    """Register *loader* as the process-default PluginLoader."""
    global _default_loader
    _default_loader = loader


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
        # Future creative/media/asset service capabilities:
        "video-generation",
        "image-generation",
        "audio-generation",
        "music-generation",
        "sfx-generation",
        "game-asset-generation",
        "3d-asset-generation",
        "asset-forge-3d",
        "mcp-server",
        "external-service",
        "artifact-store",
        "asset-index",
        "dashboard-surface",
        "gateway-api",
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
        self.registered_mcp_servers: List[Dict[str, Any]] = []
        self.registered_api_routes: List[Dict[str, Any]] = []
        self.registered_artifact_types: List[Dict[str, Any]] = []
        self.registered_capability_contracts: Dict[str, Dict[str, Any]] = {}
        self.hardware_registry = hardware_registry
        # ── generic lifecycle manager state ──────────────────────────
        # enabled_plugins: plugin name -> operator enabled flag. A plugin
        # that is loaded but disabled still occupies loaded_plugins, but
        # execute_hook() skips it until enable() is called.
        self.enabled_plugins: Dict[str, bool] = {}
        # Validated per-plugin operator config (from attach(config=...) or
        # context config["plugin_configs"][name]).
        self.plugin_configs: Dict[str, Any] = {}
        # Per-plugin load records so enable() can re-load after unload().
        self._plugin_records: Dict[str, Dict[str, Any]] = {}
        # Identity-indexed registry entries contributed per plugin, used
        # to remove them cleanly on unload() / re-attach without
        # duplicating tools across re-loads.
        self._plugin_registered_tools: Dict[str, List[Dict[str, Any]]] = {}
        self._plugin_registered_mcp: Dict[str, List[Dict[str, Any]]] = {}
        self._plugin_registered_routes: Dict[str, List[Dict[str, Any]]] = {}
        self._plugin_registered_artifacts: Dict[str, List[Dict[str, Any]]] = {}
        self._plugin_registered_personas: Dict[str, List[str]] = {}
        # Last context handed to scan_and_load_plugins — used as the
        # fallback context for attach() when no explicit context is given.
        self._last_context: Optional[PluginContext] = None
        set_default_plugin_loader(self)

    # ── public API ─────────────────────────────────────────────────────

    def scan_and_load_plugins(self, context: PluginContext) -> None:
        """
        Scan ``$PRISMATIC_HOME/plugins/`` for ``plugin-manifest.yaml``
        files, validate requirements, and dynamically register plugins.
        """
        self._last_context = context
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

        GRO-2991: also push a telemetry event to telemetry_hook_fired via
        TelemetryCollector.record_hook_fired() — fire-and-forget so a
        telemetry outage never breaks the hook bus.
        """
        import time as _time

        start = _time.monotonic()
        fired_count = 0
        for name, plugin in self.loaded_plugins.items():
            # Generic lifecycle manager: disabled plugins keep their
            # instance loaded but are skipped by the hook bus until
            # enable() is called.
            if not self.enabled_plugins.get(name, True):
                continue
            if not hasattr(plugin, hook_name):
                continue
            try:
                hook_func = getattr(plugin, hook_name)
                hook_func(*args, **kwargs)
                fired_count += 1
            except Exception:
                logger.error(
                    "Plugin '%s' failed during hook '%s'",
                    name,
                    hook_name,
                    exc_info=True,
                )
        duration_ms = (_time.monotonic() - start) * 1000.0

        # ── Telemetry: GRO-2991 wire record_hook_fired ───────────────
        # Best-effort. Pull run_id/issue_id from kwargs if available so
        # the operator dashboard can join hook fires against pipeline runs.
        try:
            from prismatic.telemetry import get_collector
            collector = get_collector()
            run_id = kwargs.get("run_id") if isinstance(kwargs, dict) else None
            issue_id = kwargs.get("issue_id") if isinstance(kwargs, dict) else None
            collector.record_hook_fired(
                hook_name=hook_name,
                event_type="plugin.execute_hook",
                run_id=run_id,
                issue_id=issue_id,
                success=fired_count > 0 or not self.loaded_plugins,
                duration_ms=duration_ms,
            )
        except Exception:
            pass  # best-effort

    # ── generic plugin lifecycle manager (public API) ──────────────────

    def attach(
        self,
        manifest_path: str | Path,
        config: Dict[str, Any] | None = None,
        context: PluginContext | None = None,
    ) -> str:
        """Load ONE plugin immediately, outside the directory scan.

        Args:
            manifest_path: Path to the plugin's ``plugin-manifest.yaml``.
            config: Optional operator config for the plugin. When the
                manifest declares ``config_schema`` (JSON Schema) the
                config is validated with ``jsonschema`` and a
                :class:`PluginValidationError` is raised on failure.
            context: PluginContext for ``on_init``. Defaults to the
                context most recently passed to
                :meth:`scan_and_load_plugins`.

        Returns:
            The loaded plugin's name.

        Raises:
            PluginValidationError: On manifest/import/config failure, or
                when no context is available.
        """
        manifest_path = Path(manifest_path)
        ctx = context or self._last_context
        if ctx is None:
            raise PluginValidationError(
                "attach() requires a PluginContext: pass context= or call "
                "scan_and_load_plugins() first."
            )
        manifest = self._read_manifest(manifest_path)
        self._validate_manifest(manifest, ctx, manifest_path)
        return self._instantiate_and_register(manifest, manifest_path, ctx, config=config)

    def enable(self, name: str) -> None:
        """Enable *name* and resume it with its preserved state.

        If the instance was dropped by :meth:`unload`, it is re-loaded
        (imported, instantiated, ``on_init`` fired) first. The plugin's
        ``on_resume(state)`` hook is then called in try/except isolation
        with the state previously persisted by :meth:`disable`
        (``{}`` when no state file exists), and the plugin is marked
        enabled so :meth:`execute_hook` dispatches to it again.

        Raises:
            PluginValidationError: If *name* is not a known plugin.
        """
        record = self._plugin_records.get(name)
        if record is None:
            raise PluginValidationError(
                f"Cannot enable unknown plugin '{name}'."
            )
        if name not in self.loaded_plugins:
            manifest = record["manifest"]
            self._instantiate_and_register(
                manifest,
                record["manifest_path"],
                record["context"],
                config=record.get("config"),
            )
        plugin = self.loaded_plugins[name]

        state: Dict[str, Any] = {}
        state_path = plugin_state_file(name)
        if state_path.exists():
            try:
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and isinstance(saved.get("state"), dict):
                    state = saved["state"]
            except Exception:
                logger.warning(
                    "Could not read preserved state for plugin '%s'; "
                    "resuming with empty state.",
                    name,
                    exc_info=True,
                )

        # Contract order: resume hook first, then mark enabled.
        try:
            plugin.on_resume(state)
        except Exception:
            logger.error(
                "Plugin '%s' failed during on_resume", name, exc_info=True
            )
        self.enabled_plugins[name] = True
        logger.info("Enabled plugin '%s'", name)

    def disable(self, name: str) -> Dict[str, Any]:
        """Disable *name*, preserving its suspend state to disk.

        Calls the plugin's ``on_suspend()`` hook in try/except isolation
        and persists the returned dict as JSON to
        ``$PRISMATIC_HOME/plugin-state/<name>/state.json`` in the form
        ``{"version": 1, "saved_at": <utc iso>, "state": <dict>}``.
        The plugin stays loaded but :meth:`execute_hook` skips it until
        :meth:`enable` is called.

        Returns:
            The persisted payload dict.

        Raises:
            PluginValidationError: If *name* is not a known plugin.
        """
        if name not in self._plugin_records:
            raise PluginValidationError(
                f"Cannot disable unknown plugin '{name}'."
            )
        plugin = self.loaded_plugins.get(name)
        state: Dict[str, Any] = {}
        if plugin is not None:
            try:
                result = plugin.on_suspend()
                if isinstance(result, dict):
                    state = result
                else:
                    logger.warning(
                        "Plugin '%s' on_suspend() returned non-dict %r; "
                        "preserving empty state.",
                        name,
                        type(result),
                    )
            except Exception:
                logger.error(
                    "Plugin '%s' failed during on_suspend", name, exc_info=True
                )

        payload: Dict[str, Any] = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }
        state_path = plugin_state_file(name)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            serialized = json.dumps(payload, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            logger.error(
                "Plugin '%s' on_suspend() state is not JSON-serializable; "
                "preserving empty state.",
                name,
                exc_info=True,
            )
            payload["state"] = {}
            serialized = json.dumps(payload, indent=2, sort_keys=True)
        state_path.write_text(serialized, encoding="utf-8")

        self.enabled_plugins[name] = False
        logger.info("Disabled plugin '%s' (state preserved)", name)
        return payload

    def unload(self, name: str) -> None:
        """Unload *name*: disable it (persisting suspend state), then drop
        the instance from ``loaded_plugins``.

        The state file is left in place so a later :meth:`attach` /
        :meth:`enable` can resume with the preserved state. The plugin's
        registered tools, MCP servers, API routes, artifact types, and
        personas are removed from the loader registries.

        Raises:
            PluginValidationError: If *name* is not a known plugin.
        """
        if name not in self._plugin_records:
            raise PluginValidationError(
                f"Cannot unload unknown plugin '{name}'."
            )
        self.disable(name)
        self.loaded_plugins.pop(name, None)
        self._remove_plugin_registrations(name)
        logger.info("Unloaded plugin '%s'", name)

    def plugin_status(self, name: str) -> Dict[str, Any]:
        """Return lifecycle status for *name*.

        ``{"enabled": bool, "loaded": bool, "state_preserved": bool,
        "version": str}``. Unknown names return a zero-value status
        (``state_preserved`` still reflects whether a state file exists
        on disk).
        """
        record = self._plugin_records.get(name)
        return {
            "enabled": bool(self.enabled_plugins.get(name, False))
            if record is not None
            else False,
            "loaded": name in self.loaded_plugins,
            "state_preserved": plugin_state_file(name).exists(),
            "version": str(record.get("version", "")) if record else "",
        }

    def all_plugin_status(self) -> Dict[str, Dict[str, Any]]:
        """Return :meth:`plugin_status` for every registered plugin."""
        return {name: self.plugin_status(name) for name in self._plugin_records}

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
        manifest_path = Path(manifest_path)
        manifest = self._read_manifest(manifest_path)
        self._validate_manifest(manifest, context, manifest_path)
        config = self._context_plugin_config(context, manifest.get("name"))
        self._instantiate_and_register(
            manifest, manifest_path, context, config=config
        )

    def _read_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        """Parse a manifest file and check its required fields."""
        with open(manifest_path, "r") as fh:
            manifest = yaml.safe_load(fh)

        if not isinstance(manifest, dict):
            raise PluginValidationError(
                f"Manifest is not a mapping: {manifest_path}"
            )

        name = manifest.get("name")
        version = manifest.get("version")
        entry_point = manifest.get("entry_point")
        core_constraint = manifest.get("core_version_constraint")

        if not all([name, version, entry_point, core_constraint]):
            raise PluginValidationError(
                f"Missing required fields in manifest: {manifest_path}"
            )
        if not str(core_constraint).strip():
            raise PluginValidationError(
                f"Blank core_version_constraint in manifest: {manifest_path}"
            )
        return manifest

    def _validate_manifest(
        self,
        manifest: Dict[str, Any],
        context: PluginContext,
        manifest_path: Path,
    ) -> None:
        """Run pre-import validation: core version, required capabilities,
        provider constraints, and hardware profiles."""
        name = manifest.get("name")
        core_constraint = manifest.get("core_version_constraint")

        # 1. Core version validation — fail closed on ANY version problem.
        # Only PluginValidationError ever leaves this block: a blank
        # constraint, a malformed specifier, or an unparseable engine version
        # is a rejection, never a crash and never a match-everything guess.
        if not str(core_constraint or "").strip():
            raise PluginValidationError(
                f"Blank core_version_constraint for plugin '{name}'."
            )
        try:
            specifier = SpecifierSet(str(core_constraint).strip())
            engine_version = Version(str(self.core_version).strip())
        except InvalidSpecifier as e:
            raise PluginValidationError(
                f"Malformed core_version_constraint '{core_constraint}' "
                f"for plugin '{name}': {e}"
            ) from e
        except InvalidVersion as e:
            raise PluginValidationError(
                f"Unparseable engine version '{self.core_version}': {e}"
            ) from e
        if engine_version not in specifier:
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

    def _context_plugin_config(
        self, context: PluginContext, name: str
    ) -> Optional[Dict[str, Any]]:
        """Return operator config for *name* from the dispatcher config.

        Reads ``context.config["plugin_configs"][name]`` when present;
        used by the scan path so manifests with ``config_schema`` can be
        validated without an explicit ``attach(config=...)`` call.
        """
        cfg = getattr(context, "config", None) or {}
        plugin_configs = cfg.get("plugin_configs")
        if isinstance(plugin_configs, dict):
            value = plugin_configs.get(name)
            return value if isinstance(value, dict) else None
        return None

    def _instantiate_and_register(
        self,
        manifest: Dict[str, Any],
        manifest_path: Path,
        context: PluginContext,
        config: Dict[str, Any] | None = None,
    ) -> str:
        """Validate operator config, import, instantiate, and register.

        Shared by :meth:`_load_plugin` (scan path), :meth:`attach`, and
        :meth:`enable` re-loads. Returns the plugin name. Loading a name
        that was already registered first removes its prior registry
        entries so tools/personas are never duplicated across re-loads.
        """
        name = manifest.get("name")
        version = manifest.get("version")
        entry_point = manifest.get("entry_point")

        # 0. Operator config validation (config_schema is optional).
        schema = manifest.get("config_schema")
        if schema is not None:
            if not isinstance(schema, dict):
                raise PluginValidationError(
                    f"Plugin '{name}' declares a non-object config_schema."
                )
            if config is None:
                if schema.get("required"):
                    raise PluginValidationError(
                        f"Plugin '{name}' requires operator config "
                        f"(config_schema requires: {schema['required']}) "
                        f"but none was provided."
                    )
            else:
                try:
                    import jsonschema

                    jsonschema.validate(instance=config, schema=schema)
                except ImportError as exc:
                    raise PluginValidationError(
                        f"Plugin '{name}' declares config_schema but the "
                        f"'jsonschema' package is unavailable: {exc}"
                    ) from exc
                except Exception as exc:
                    raise PluginValidationError(
                        f"Plugin '{name}' config failed config_schema "
                        f"validation: {exc}"
                    ) from exc

        if config is not None:
            self.plugin_configs[name] = config

        # Clean slate for this plugin's registry entries (idempotent
        # re-attach / enable-after-unload).
        self._remove_plugin_registrations(name)

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

        # 3. Instantiate + fire on_init in isolation. Operator config is
        # made visible to the plugin under context config["plugin_configs"].
        plugin_instance = plugin_class()
        init_context = context
        if config is not None:
            merged = dict(context.config or {})
            existing = merged.get("plugin_configs")
            plugin_configs = dict(existing) if isinstance(existing, dict) else {}
            plugin_configs[name] = config
            merged["plugin_configs"] = plugin_configs
            init_context = _dataclass_replace(context, config=merged)
        try:
            plugin_instance.on_init(init_context)
        except Exception as exc:
            raise PluginValidationError(
                f"Exception raised during on_init execution: {exc}"
            ) from exc

        self.loaded_plugins[name] = plugin_instance
        self._plugin_records[name] = {
            "manifest_path": Path(manifest_path),
            "context": context,
            "config": config,
            "manifest": manifest,
            "version": str(version),
        }
        # Manifest opt-in: explicitly false => registered but DISABLED
        # (hooks skipped until enable()). Default true preserves the
        # historical scan behaviour.
        self.enabled_plugins[name] = bool(manifest.get("auto_enable", True))

        # 4. Register personas (tracked per plugin for clean unload).
        persona_ids: List[str] = []
        for persona in manifest.get("personas", []):
            persona_id = persona.get("id")
            persona_ids.append(persona_id)
            self.registered_personas[persona_id] = persona
            logger.info(
                "Registered persona '%s' from plugin '%s'",
                persona_id,
                name,
            )
        self._plugin_registered_personas[name] = persona_ids

        # 5. Register tools (tracked by identity for clean unload).
        tools: List[Dict[str, Any]] = []
        try:
            tools = plugin_instance.register_tools() or []
            self.registered_tools.extend(tools)
        except Exception:
            logger.error(
                "Plugin '%s' failed to register tools", name, exc_info=True
            )
        self._plugin_registered_tools[name] = list(tools)

        # 6. Register optional discovery/integration surfaces. These are
        # best-effort so old plugins remain compatible and experimental
        # media/service plugins can progressively declare more surfaces.
        try:
            contract = plugin_instance.capability_contract()
            if contract:
                self.registered_capability_contracts[name] = contract
        except Exception:
            logger.error("Plugin '%s' failed to expose capability contract", name, exc_info=True)
        mcp_entries: List[Dict[str, Any]] = []
        try:
            for server in plugin_instance.register_mcp_servers():
                if isinstance(server, dict):
                    entry = {"plugin": name, **server}
                    self.registered_mcp_servers.append(entry)
                    mcp_entries.append(entry)
        except Exception:
            logger.error("Plugin '%s' failed to register MCP servers", name, exc_info=True)
        self._plugin_registered_mcp[name] = mcp_entries
        route_entries: List[Dict[str, Any]] = []
        try:
            for route in plugin_instance.register_api_routes():
                if isinstance(route, dict):
                    entry = {"plugin": name, **route}
                    self.registered_api_routes.append(entry)
                    route_entries.append(entry)
        except Exception:
            logger.error("Plugin '%s' failed to register API routes", name, exc_info=True)
        self._plugin_registered_routes[name] = route_entries
        artifact_entries: List[Dict[str, Any]] = []
        try:
            for artifact in plugin_instance.register_artifact_types():
                if isinstance(artifact, dict):
                    entry = {"plugin": name, **artifact}
                    self.registered_artifact_types.append(entry)
                    artifact_entries.append(entry)
        except Exception:
            logger.error("Plugin '%s' failed to register artifact types", name, exc_info=True)
        self._plugin_registered_artifacts[name] = artifact_entries

        logger.info("Successfully loaded plugin '%s' (v%s)", name, version)
        return name

    def _remove_plugin_registrations(self, name: str) -> None:
        """Remove registry entries previously contributed by *name*."""
        for tool in self._plugin_registered_tools.pop(name, []):
            try:
                self.registered_tools.remove(tool)
            except ValueError:
                pass
        for entry in self._plugin_registered_mcp.pop(name, []):
            try:
                self.registered_mcp_servers.remove(entry)
            except ValueError:
                pass
        for entry in self._plugin_registered_routes.pop(name, []):
            try:
                self.registered_api_routes.remove(entry)
            except ValueError:
                pass
        for entry in self._plugin_registered_artifacts.pop(name, []):
            try:
                self.registered_artifact_types.remove(entry)
            except ValueError:
                pass
        for persona_id in self._plugin_registered_personas.pop(name, []):
            self.registered_personas.pop(persona_id, None)
        self.registered_capability_contracts.pop(name, None)


# ── GRO-2228: PWP pipeline hook orchestrator ───────────────────────────────

from prismatic.interface.hooks import (  # noqa: E402
    HOOK_ON_PRE_PIPELINE,
    HOOK_ON_POST_PIPELINE,
    HOOK_ON_ERROR,
    HOOK_ON_DEPLOY,
)

# A PWP pipeline stage is a ``(name, callable)`` pair.  The callable
# receives the pipeline context dict and returns any JSON-serializable
# value that will be stored under ``result["stages"][i]["output"]``.
Stage = Tuple[str, Callable[[Dict[str, Any]], Any]]


class PWPPluginRunner:
    """
    Orchestrates a PWP (Prismatic Web Plugin) pipeline run.

    Wraps a list of stage callables with the 4 PWP hooks:

    1. ``on_pre_pipeline``  — fires *once* before any stage runs
    2. ``on_post_pipeline`` — fires *once* if every stage succeeded
    3. ``on_error``         — fires *once* if any stage raises (the
                              exception is re-raised after the hook
                              returns so callers can still see it)
    4. ``on_deploy``        — fires *once* after a successful
                              post-pipeline, if a ``deploy_target`` is
                              provided to :meth:`run`

    All hook dispatches go through :meth:`PluginLoader.execute_hook`,
    so a crashing plugin can never abort the run.

    Example::

        runner = PWPPluginRunner(loader)
        result = runner.run(
            pipeline_id="GRO-2228-abc",
            context={"issue_id": "GRO-2228", "branch": "ned/GRO-2228"},
            stages=[
                ("build",  build_site),
                ("test",   run_tests),
            ],
            deploy_target="cloudflare-pages",
        )

    Args:
        loader: A :class:`PluginLoader` instance whose ``loaded_plugins``
            will be notified at each hook.
    """

    def __init__(self, loader: "PluginLoader") -> None:
        self.loader = loader

    def run(
        self,
        pipeline_id: str,
        context: Dict[str, Any],
        stages: Iterable[Stage],
        deploy_target: Optional[str] = None,
        deploy_artifact_provider: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Run a PWP pipeline.

        Args:
            pipeline_id: Unique identifier for the run; threaded through
                every hook invocation.
            context: Read-only metadata describing the pipeline.
            stages: Iterable of ``(stage_name, callable)`` pairs.  Each
                callable is invoked with the post-pipeline ``context``
                dict (mutations are visible to later stages).
            deploy_target: If provided, ``on_deploy`` fires after
                ``on_post_pipeline`` with this target name.
            deploy_artifact_provider: Optional callable that, given the
                aggregated ``result`` dict, returns the artifact dict
                forwarded to ``on_deploy``.  Required when
                ``deploy_target`` is set.

        Returns:
            The aggregated ``result`` dict.  Contains at minimum::

                {
                    "status": "succeeded",
                    "stages": [{"name": ..., "ok": True, "output": ...}, ...],
                }

        Raises:
            BaseException: Re-raises the first exception any stage
                raised, *after* firing the ``on_error`` hook.
        """
        self.loader.execute_hook(HOOK_ON_PRE_PIPELINE, pipeline_id, context)

        result: Dict[str, Any] = {"status": "running", "stages": []}
        try:
            for stage_name, stage_fn in stages:
                try:
                    output = stage_fn(context)
                except BaseException as exc:
                    result["status"] = "failed"
                    result["failed_stage"] = stage_name
                    self.loader.execute_hook(
                        HOOK_ON_ERROR, pipeline_id, exc, stage_name
                    )
                    raise
                result["stages"].append(
                    {"name": stage_name, "ok": True, "output": output}
                )
        finally:
            # No-op finally block — kept for symmetry with the
            # orchestrator's future resource-cleanup hook.
            pass

        result["status"] = "succeeded"
        self.loader.execute_hook(HOOK_ON_POST_PIPELINE, pipeline_id, result)

        if deploy_target is not None:
            if deploy_artifact_provider is None:
                raise ValueError(
                    "deploy_artifact_provider is required when "
                    "deploy_target is set"
                )
            artifact = deploy_artifact_provider(result)
            self.loader.execute_hook(
                HOOK_ON_DEPLOY, pipeline_id, deploy_target, artifact
            )

        return result
