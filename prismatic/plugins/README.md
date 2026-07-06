# Prismatic runtime plugins

The `prismatic.plugins` package defines the minimal engine-facing contract for
runtime plugins and a registry-backed loader for enabling them.

## Base class

Plugins subclass `PrismaticPlugin` from `prismatic.plugins.base` and implement
three metadata properties:

```python
from prismatic.plugins.base import PrismaticPlugin

class Plugin(PrismaticPlugin):
    @property
    def name(self) -> str:
        return "example"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Example Prismatic plugin"
```

Optional hooks:

- `on_install(engine)`
- `on_enable(engine)`
- `on_disable(engine)`
- `subscriptions() -> list[str]`
- `handle_event(event: dict)`
- `routes()` for FastAPI routers or route installer callables
- `hub_panel() -> dict`

## Registry loader

`registry.json` lives beside the loader package and uses this shape:

```json
{
  "plugins": [
    {"name": "example", "module": "example_plugin", "enabled": true}
  ]
}
```

Load enabled plugins:

```python
from pathlib import Path
from prismatic.plugins.loader import load_plugins

plugins = load_plugins(Path("prismatic/plugins"))
```

Disabled registry entries (`"enabled": false`) are skipped.

## FastAPI route mounting

Plugins can return FastAPI `APIRouter` instances from `routes()`. Mount them into
an existing app with:

```python
from prismatic.plugins.loader import load_and_include_plugin_routes

plugins = load_and_include_plugin_routes(app, Path("prismatic/plugins"))
```

`include_plugin_routes(app, plugins)` also accepts callable route installers for
small plugins that prefer to mutate the app directly.
