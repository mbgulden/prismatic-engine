"""Registry-backed plugin loader for Prismatic Engine.

The loader reads ``registry.json`` from a plugin directory, imports enabled
plugin modules, instantiates their ``Plugin`` class, and can mount plugin routes
onto a FastAPI application.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from prismatic.plugins.base import PrismaticPlugin


class PluginLoadError(RuntimeError):
    """Raised when the registry or a plugin entry cannot be loaded."""


def read_registry(plugins_dir: Path) -> dict[str, Any]:
    """Read and parse ``registry.json`` from ``plugins_dir``.

    Missing registries are treated as an empty plugin set so a fresh engine can
    boot before operators install any plugins.
    """

    registry_path = Path(plugins_dir) / "registry.json"
    if not registry_path.exists():
        return {"plugins": []}
    try:
        data = json.loads(registry_path.read_text())
    except json.JSONDecodeError as exc:
        raise PluginLoadError(f"invalid plugin registry JSON: {registry_path}") from exc
    if not isinstance(data, dict):
        raise PluginLoadError(f"plugin registry must be a JSON object: {registry_path}")
    plugins = data.get("plugins", [])
    if not isinstance(plugins, list):
        raise PluginLoadError("plugin registry field 'plugins' must be a list")
    return data


def load_plugins(plugins_dir: Path) -> list[PrismaticPlugin]:
    """Discover and instantiate enabled plugins from ``registry.json``.

    Registry entry format::

        {"name": "example", "module": "example_plugin", "enabled": true}

    Disabled entries are skipped. Each enabled module must expose a ``Plugin``
    class whose instance implements :class:`PrismaticPlugin`.
    """

    plugins_dir = Path(plugins_dir)
    registry = read_registry(plugins_dir)
    loaded: list[PrismaticPlugin] = []

    # Support registries that point at sibling modules/packages inside the
    # plugin directory while still allowing fully-qualified import paths.
    plugin_path = str(plugins_dir.resolve())
    added_path = False
    if plugin_path not in sys.path:
        sys.path.insert(0, plugin_path)
        added_path = True

    try:
        for entry in registry.get("plugins", []):
            if not isinstance(entry, dict):
                raise PluginLoadError("plugin registry entries must be objects")
            if not entry.get("enabled", True):
                continue
            module_name = entry.get("module")
            if not module_name or not isinstance(module_name, str):
                raise PluginLoadError("enabled plugin entry missing string 'module'")
            module = importlib.import_module(module_name)
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls is None:
                raise PluginLoadError(
                    f"plugin module {module_name!r} has no Plugin class"
                )
            plugin = plugin_cls()
            if not isinstance(plugin, PrismaticPlugin):
                raise PluginLoadError(
                    f"plugin module {module_name!r} Plugin is not a PrismaticPlugin"
                )
            loaded.append(plugin)
    finally:
        if added_path:
            try:
                sys.path.remove(plugin_path)
            except ValueError:
                pass

    return loaded


def include_plugin_routes(app: Any, plugins: Iterable[PrismaticPlugin]) -> int:
    """Merge plugin routes into a FastAPI app.

    Route objects with an ``routes`` attribute are treated as APIRouter-like and
    passed to ``app.include_router``. Callable route installers are called with
    the app. Empty route lists are ignored. Returns the number of mounted route
    objects/installers.
    """

    mounted = 0
    for plugin in plugins:
        for route in plugin.routes():
            if hasattr(route, "routes") and hasattr(app, "include_router"):
                app.include_router(route)
                mounted += 1
            elif callable(route):
                route(app)
                mounted += 1
            else:
                raise PluginLoadError(
                    f"plugin {plugin.name!r} returned unsupported route object: {route!r}"
                )
    return mounted


def load_and_include_plugin_routes(
    app: Any, plugins_dir: Path
) -> list[PrismaticPlugin]:
    """Load enabled plugins and mount their FastAPI routes onto ``app``."""

    plugins = load_plugins(plugins_dir)
    include_plugin_routes(app, plugins)
    return plugins
