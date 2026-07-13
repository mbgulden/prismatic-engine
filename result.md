# Prismatic Engine: Linear Bot Plugin (GRO-3051)

## Requirement Overview
We implemented the **Linear Bot** plugin, which serves as the verification proof that the Prismatic Engine plugin system successfully supports plugins other than PWP (Prismatic Web Plugin). 

Specifically:
- **Event Flow**: `LinearBotPlugin` subscribes to the `pwp.pipeline.failed` event and publishes a corresponding `linear.issue.created` event with issue details.
- **Manifest Properties**:
  - Declares compatibility with core version `0.2.0` (`core_version_constraint: ">=0.2.0, <2.0.0"`).
  - Exposes support for both modes (`modes: "both"` for headless cron + interactive TUI).
  - Specifies network permission constraints (`permissions.network` includes `api.linear.app`).
  - Lists event publications (`events_published`) and subscriptions (`events_subscribed`).
- **Coexistence**: Configured `curator-tap` with a minimal manifest and plugin class so that **PWP + Linear Bot + curator-tap** coexist and load successfully with zero warnings.
- **Validator Correction**: Added `"npm"` to the whitelisted dependency keys in `prismatic/interface/manifest_schema.py` to allow the existing `visual-verifier` plugin to load correctly.
- **Test Coverage**: Added `tests/test_linear_bot_plugin.py` to assert manifest validation, event subscription/delivery, and core loader discovery.

---

## Technical Details

### 1. Linear Bot Manifest (`plugins/linear_bot/plugin-manifest.yaml`)
```yaml
schema_version: "1.1.0"
name: "linear-bot"
version: "1.0.0"
description: "Linear Bot plugin that subscribes to pwp.pipeline.failed and publishes linear.issue.created."
author: "Antigravity (agent:antigravity)"
entry_point: "linear_bot.plugin:LinearBotPlugin"
core_version_constraint: ">=0.2.0, <2.0.0"
modes: "both"
permissions:
  network:
    - "api.linear.app"
events_subscribed:
  - "pwp.pipeline.failed"
events_published:
  - "linear.issue.created"
hooks:
  - "on_init"
```

### 2. Linear Bot Python Implementation (`plugins/linear_bot/plugin.py`)
Registers an async handler `handle_event` on the event bus singleton `get_event_bus()`. When a `pwp.pipeline.failed` event is received, it extracts the error and pipeline ID and publishes `linear.issue.created`.
```python
class LinearBotPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(get_event_bus().subscribe(self.handle_event))
        except RuntimeError:
            get_event_bus()._handlers.add(self.handle_event)

    async def handle_event(self, event: SwarmEvent) -> None:
        if event.type == "pwp.pipeline.failed":
            payload = {
                "title": f"PWP Pipeline Failure: {event.payload.get('pipeline_id')}",
                "description": f"Pipeline failure detected.\nError details:\n{event.payload.get('error')}",
                "pipeline_id": event.payload.get("pipeline_id"),
                "error": event.payload.get("error"),
                "severity": "high",
                "source_event": event.to_dict()
            }
            await get_event_bus().publish(
                event_type="linear.issue.created",
                source="linear-bot",
                payload=payload
            )
```

### 3. Curator Tap Coexistence
We created a minimal manifest (`plugins/curator_tap/plugin-manifest.yaml`) and plugin module (`plugins/curator_tap/plugin.py`) so it is discovered and loaded without any warnings.

### 4. Manifest Validator Correction (`prismatic/interface/manifest_schema.py`)
```diff
@@ -169,7 +169,7 @@
         deps = manifest["dependencies"]
         if not isinstance(deps, dict):
             raise PluginValidationError("Field 'dependencies' must be a dictionary.")
-        allowed_dep_keys = {"pip", "plugins", "system"}
+        allowed_dep_keys = {"pip", "plugins", "system", "npm"}
         extra_keys = set(deps.keys()) - allowed_dep_keys
```

---

## Testing & Verification

### 1. Shipped Plugin Load Gate
Running the plugin load gate verifies all 7 shipped plugins (including `linear-bot` and `curator-tap`) load correctly without errors:
```bash
.venv_dev/bin/python -m prismatic.quality.plugin_load
```
```text
## ✅ Plugin Load Gate: PASS

**Plugins dir:** `/home/ubuntu/work/prismatic-engine/plugins`
**Core version:** `0.2.0`
**Loaded:** 7
**Failed:** 0
**Reason:** all 7 shipped plugins loaded successfully

### Findings

| Plugin | Status | Detail |
|---|---|---|
| `curator-tap` | loaded | loaded successfully |
| `example-plugin` | loaded | loaded successfully |
| `linear-bot` | loaded | loaded successfully |
| `prismatic-hello-world` | loaded | loaded successfully |
| `pwp-design-token-plugin` | loaded | loaded successfully |
| `pwp-hook-test-plugin` | loaded | loaded successfully |
| `visual-verifier` | loaded | loaded successfully |
```

### 2. Unit/Integration Tests (`tests/test_linear_bot_plugin.py`)
```bash
.venv_dev/bin/pytest tests/test_linear_bot_plugin.py -v
```
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
cachedir: .pytest_cache
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.14.1
collected 3 items                                                              

tests/test_linear_bot_plugin.py::test_linear_bot_manifest_and_metadata PASSED [ 33%]
tests/test_linear_bot_plugin.py::test_linear_bot_event_subscription_and_handling PASSED [ 66%]
tests/test_linear_bot_plugin.py::test_loader_loads_linear_bot PASSED     [100%]

============================== 3 passed in 14.97s ==============================
```
