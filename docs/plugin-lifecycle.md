# Generic Plugin Lifecycle Manager

The generic plugin lifecycle manager lives on `PluginLoader`
(`prismatic/core/registry.py`) and gives operators a uniform way to
attach, enable, disable, and unload any `PrismaticPlugin` at runtime —
with suspend/resume state preserved across detach cycles.

It is intentionally separate from the sandbox pod lifecycle
(`prismatic/plugins/lifecycle_manager.py`), which manages process-level
plugin sandboxes. This manager governs *loader-level* lifecycle: whether
a plugin's hooks fire, and what state survives a disable/enable round
trip.

## Contract

### Plugin-side hooks (`prismatic/interface/plugin.py`)

Both are optional with default no-ops, so existing plugins are
unaffected:

```python
def on_suspend(self) -> Dict[str, Any]:
    """Return JSON-serializable state to preserve. Default: {}."""

def on_resume(self, state: Dict[str, Any]) -> None:
    """Restore previously preserved state. Default: no-op."""
```

`on_suspend()` is called in try/except isolation — a crashing plugin can
never break `disable()`; empty state is preserved instead. If the
returned value is not a dict, or is not JSON-serializable, the loader
logs a warning/error and preserves `{}`.

### Loader API (`prismatic.core.registry.PluginLoader`)

| Method | Signature | Behaviour |
|---|---|---|
| `attach` | `(manifest_path, config=None, context=None) -> str` | Load ONE plugin immediately, outside the directory scan. Validates `config` against the manifest's optional `config_schema` (JSON Schema) via `jsonschema`; raises `PluginValidationError` on failure. Returns the plugin name. Falls back to the context from the last `scan_and_load_plugins()` when `context` is omitted. |
| `enable` | `(name) -> None` | Re-loads the instance if it was dropped by `unload()` (import + `on_init` fire again). Calls `on_resume(state)` in isolation with the state read from the plugin's `state.json` (`{}` when absent), then marks the plugin enabled so `execute_hook` dispatches to it again. |
| `disable` | `(name) -> dict` | Calls `on_suspend()` in isolation and persists `{"version": 1, "saved_at": <utc iso>, "state": <dict>}` to the plugin's `state.json`. Marks the plugin disabled; `execute_hook` skips disabled plugins. Returns the persisted payload. |
| `unload` | `(name) -> None` | `disable()` then drops the instance from `loaded_plugins` and removes the plugin's registered tools/personas/MCP servers/routes/artifact types. The state file is left in place. |
| `plugin_status` | `(name) -> dict` | `{"enabled": bool, "loaded": bool, "state_preserved": bool, "version": str}`. Unknown names return zero-value status (`state_preserved` still reflects the state file on disk). |
| `all_plugin_status` | `() -> dict` | `plugin_status()` for every registered plugin. |

`enable`, `disable`, and `unload` raise `PluginValidationError` for
unknown plugin names.

### Manifest opt-ins

```yaml
# plugin-manifest.yaml
auto_enable: false      # default true — register at scan but start DISABLED
config_schema:         # optional JSON Schema for operator config
  type: object
  properties:
    retries: {type: integer, minimum: 0}
  required: [retries]
```

- **`auto_enable: false`** — the plugin is imported, instantiated, and
  registered at scan time, but starts *disabled*: its hooks are skipped
  by `execute_hook` until an operator calls `enable(name)`. This is how
  capability plugins ship disabled-by-default.
- **`config_schema`** — when present, `attach(manifest, config=...)`
  validates `config` with `jsonschema` before loading and raises
  `PluginValidationError` on failure. A missing config is only an error
  when the schema lists `required` fields. On the scan path, config is
  read from `context.config["plugin_configs"][name]`. Validated config
  is stored on `loader.plugin_configs[name]` and is visible to the
  plugin at `on_init` time under
  `context.config["plugin_configs"][name]`.

Re-attaching (or `enable()` after `unload()`) an already-registered name
first removes that plugin's prior registry entries, so tools, personas,
and surfaces are never duplicated across re-loads.

## State directory layout

Suspend state follows the repo's `PRISMATIC_HOME` convention
(`Path(os.environ.get("PRISMATIC_HOME") or Path.home())`):

```
$PRISMATIC_HOME/
└── plugin-state/
    └── <plugin-name>/
        └── state.json      # {"version": 1, "saved_at": "<utc iso>", "state": {...}}
```

Helpers in `prismatic/core/registry.py`:

- `prismatic_home() -> Path`
- `plugin_state_file(plugin_name, home=None) -> Path`
- `get_default_plugin_loader() / set_default_plugin_loader()` — the most
  recently constructed `PluginLoader` registers itself, so the gateway
  can merge live loader status into dashboard endpoints without
  threading the loader through every call layer.

## Operator flow

```python
from prismatic.core.registry import PluginLoader
from prismatic.interface.plugin import PluginContext

loader = PluginLoader(core_version="0.2.0", plugins_dir="/path/to/plugins")
ctx = PluginContext(config={}, db_connection=None, state_dir="/path/to/state")

# Attach a single plugin with operator config (validated by config_schema).
name = loader.attach("plugins/my_plugin/plugin-manifest.yaml",
                     config={"retries": 3}, context=ctx)

# Take it out of the hook bus, preserving suspend state.
loader.disable(name)

# Bring it back; on_resume() receives the preserved state.
loader.enable(name)

# Fully detach; the state file remains for the next attach/enable.
loader.unload(name)

# Inspect.
loader.plugin_status(name)
loader.all_plugin_status()
```

## Dashboard

Per-plugin `enabled` and `state_preserved` are operator-visible in two
places (both additive — no existing fields changed):

- `GET /api/plugins/governance` — each plugin item now also carries
  `enabled` and `state_preserved`.
- `GET /api/plugins/lifecycle` — new endpoint merging
  `plugin_catalog()` with `loader.all_plugin_status()`; each item carries
  `enabled`, `loaded`, `state_preserved`, `version`, plus the full
  `lifecycle` status dict.

When no `PluginLoader` has run in the gateway process, the endpoints
fall back to a disk-based view (`state.json` presence under
`$PRISMATIC_HOME/plugin-state/`), so suspend state left by other
processes is still visible.
