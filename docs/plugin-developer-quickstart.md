# Plugin developer quickstart

This is the short public entrypoint for building a Prismatic Engine plugin. For the full guide, see [`plugin-developer-guide.md`](plugin-developer-guide.md) and [`hello-plugin-tutorial.md`](hello-plugin-tutorial.md).

## 1. Start from a working checkout

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
python scripts/public_launch_smoke.py
```

## 2. Choose live plugin or blueprint

- Use `plugins/` only for live plugins that import, validate, and pass `plugin-load-gate`.
- Use `docs/plugin-blueprints/` for future or incomplete plugins.

## 3. Minimum manifest

```yaml
schema_version: "1.0"
name: hello-plugin
version: "0.1.0"
description: Hello Prismatic plugin
author: Your Name
license: AGPL-3.0-only
entry_point: hello_plugin.plugin:HelloPlugin
core_version_constraint: ">=0.2.0"
plugin_class: utility
capabilities: []
required_capabilities: []
governance:
  readiness_state: ready
  risk_level: low
  approval_gates: []
  policy_checks: []
  production_blockers: []
```

## 4. Minimum plugin class

```python
from prismatic.interface.plugin import PrismaticPlugin


class HelloPlugin(PrismaticPlugin):
    def on_init(self) -> None:
        self.state["initialized"] = True

    def register_tools(self) -> dict:
        return {"hello": lambda name="world": f"hello {name}"}
```

## 5. Validate before PR

```bash
python scripts/plugin_architecture catalog
python scripts/plugin_architecture validate plugins/hello_plugin/plugin-manifest.yaml
plugin-load-gate
python -m pytest plugins/hello_plugin/tests -q
```

## 6. Production-readiness expectations

- Declare risk, permissions, approval gates, policy checks, provenance requirements, audit events, and job lifecycle in the manifest.
- Store env var names only. Never store credential values in manifests, docs, tests, or examples.
- Use PE Core job/artifact/policy APIs instead of custom one-off state stores.
- Emit artifacts through the universal artifact/provenance registry.
- Keep destructive, costly, publish/export, batch, and credentialed actions behind approval gates.
