# Prismatic quality gates

Quality gate modules in this package are intended to run against installed
Prismatic Engine artifacts in CI and release automation.

## Plugin load gate dependency contract

`plugin-load-gate` imports `prismatic.core.registry.PluginLoader` and exercises
the same plugin-loading path used by the engine. `PluginLoader` validates plugin
core-version constraints with `packaging.specifiers.SpecifierSet` and
`packaging.version.Version`, so the root package metadata must declare
`packaging` as a runtime dependency. PWP theme validation uses JSON Schema for
schema-contract checks, so `jsonschema` is also a runtime dependency rather than
a local test-machine assumption.

If CI reports `PluginLoader import failed: No module named 'packaging'`, the root
runtime dependencies are incomplete.

The GitHub `Plugin Load Gate` workflow installs the project with the `dev` extra
before running the gate and its focused pytest module:

```bash
pip install -e ".[dev]"
python -m prismatic.quality.plugin_load
python -m pytest tests/test_plugin_load_gate.py -v
```

If CI reports `No module named pytest`, the `dev` extra is incomplete. The gate
should fail only for real shipped-plugin load failures, not for missing
engine/runtime/test harness dependencies.
