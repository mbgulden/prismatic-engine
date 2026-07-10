# Prismatic quality gates

Quality gate modules in this package are intended to run against installed
Prismatic Engine artifacts in CI and release automation.

## Plugin load gate dependency contract

`plugin-load-gate` imports `prismatic.core.registry.PluginLoader` and exercises
the same plugin-loading path used by the engine. `PluginLoader` validates plugin
core-version constraints with `packaging.specifiers.SpecifierSet` and
`packaging.version.Version`, so the root package metadata must declare
`packaging` as a runtime dependency.

If CI reports `PluginLoader import failed: No module named 'packaging'`, install
the current package from the branch and rerun:

```bash
python -m prismatic.quality.plugin_load
```

The gate should fail only for real shipped-plugin load failures, not for missing
engine runtime dependencies.