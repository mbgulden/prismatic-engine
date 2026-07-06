from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI

from prismatic.plugins import PrismaticPlugin
from prismatic.plugins.loader import (
    PluginLoadError,
    include_plugin_routes,
    load_and_include_plugin_routes,
    load_plugins,
)


def _write_plugin_module(path: Path, *, class_body: str | None = None) -> None:
    body = class_body or '''
class Plugin(PrismaticPlugin):
    @property
    def name(self):
        return "demo"

    @property
    def version(self):
        return "1.0.0"

    @property
    def description(self):
        return "Demo plugin"

    def subscriptions(self):
        return ["engine.started"]

    def routes(self):
        router = APIRouter()

        @router.get("/demo")
        def demo():
            return {"ok": True}

        return [router]
'''
    path.write_text(
        "from fastapi import APIRouter\n"
        "from prismatic.plugins.base import PrismaticPlugin\n\n"
        + textwrap.dedent(body).lstrip()
    )


def test_base_class_importable() -> None:
    assert PrismaticPlugin.__name__ == "PrismaticPlugin"


def test_loader_instantiates_enabled_plugins_and_skips_disabled(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin_module(plugins_dir / "enabled_plugin.py")
    _write_plugin_module(
        plugins_dir / "disabled_plugin.py",
        class_body='''
class Plugin(PrismaticPlugin):
    @property
    def name(self):
        return "disabled"

    @property
    def version(self):
        return "1.0.0"

    @property
    def description(self):
        return "Disabled plugin"
''',
    )
    (plugins_dir / "registry.json").write_text(
        '{"plugins": ['
        '{"name": "demo", "module": "enabled_plugin", "enabled": true},'
        '{"name": "disabled", "module": "disabled_plugin", "enabled": false}'
        ']}'
    )

    plugins = load_plugins(plugins_dir)

    assert [plugin.name for plugin in plugins] == ["demo"]
    assert plugins[0].subscriptions() == ["engine.started"]
    assert str(plugins_dir.resolve()) not in sys.path


def test_empty_missing_registry_loads_no_plugins(tmp_path: Path) -> None:
    assert load_plugins(tmp_path) == []


def test_loader_rejects_non_plugin_class(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "bad_plugin.py").write_text("class Plugin: pass\n")
    (plugins_dir / "registry.json").write_text(
        '{"plugins": [{"name": "bad", "module": "bad_plugin"}]}'
    )

    with pytest.raises(PluginLoadError, match="not a PrismaticPlugin"):
        load_plugins(plugins_dir)


def test_include_plugin_routes_mounts_fastapi_router(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin_module(plugins_dir / "route_plugin.py")
    (plugins_dir / "registry.json").write_text(
        '{"plugins": [{"name": "demo", "module": "route_plugin"}]}'
    )
    app = FastAPI()

    plugins = load_and_include_plugin_routes(app, plugins_dir)

    assert [plugin.name for plugin in plugins] == ["demo"]
    assert any(getattr(route, "path", None) == "/demo" for route in app.routes)


def test_include_plugin_routes_accepts_callable_installers() -> None:
    class CallableRoutePlugin(PrismaticPlugin):
        @property
        def name(self) -> str:
            return "callable"

        @property
        def version(self) -> str:
            return "1.0.0"

        @property
        def description(self) -> str:
            return "Callable route installer"

        def routes(self):
            def install(app: FastAPI) -> None:
                @app.get("/callable")
                def callable_route():
                    return {"ok": True}

            return [install]

    app = FastAPI()

    mounted = include_plugin_routes(app, [CallableRoutePlugin()])

    assert mounted == 1
    assert any(getattr(route, "path", None) == "/callable" for route in app.routes)
