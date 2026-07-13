# Plugin developer guide

Plugins add domain-specific capability while Prismatic Engine keeps lifecycle, policy, jobs, artifacts, and dashboard visibility in core.

## Plugin anatomy

A plugin directory should include:

```text
plugins/my_plugin/
├── plugin-manifest.yaml
├── plugin.py
├── README.md
└── tests/
```

## Manifest minimum

```yaml
schema_version: "1.0"
name: my-plugin
version: "0.1.0"
description: My first Prismatic plugin
author: Your Name
license: AGPL-3.0-only
entry_point: my_plugin.plugin:MyPlugin
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

## Python class minimum

```python
from prismatic.interface.plugin import PrismaticPlugin


class MyPlugin(PrismaticPlugin):
    def on_init(self) -> None:
        self.state["initialized"] = True

    def register_tools(self) -> dict:
        return {"hello": lambda name="world": f"hello {name}"}
```

## Validate before PR

```bash
python scripts/plugin_architecture catalog
python scripts/plugin_architecture validate plugins/my_plugin/plugin-manifest.yaml
plugin-load-gate
python -m pytest plugins/my_plugin/tests -q
```

## Policy expectations

Use conservative governance declarations:

- `risk_level: low` only for read-only/local/dry-run actions.
- Add `approval_gates` for publish/export/deploy/delete/write/costly actions.
- Do not place credential values in manifests. Document env var names in plugin docs instead.
- Emit artifacts through PE Core so provenance is durable.

## Live plugin vs blueprint

Do not place incomplete future plugins under `plugins/`. Use `docs/plugin-blueprints/` until the plugin imports, validates, and passes the load gate.
