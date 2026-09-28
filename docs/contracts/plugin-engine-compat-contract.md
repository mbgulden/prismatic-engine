# Plugin ↔ Engine Compatibility Contract

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-09-27
**Supersedes:** PR #380 ("PWP–PE compatibility contract") — that PR was PWP-specific
and 2 months stale. This document is the general, engine-wide revival of its concept.
Do not merge #380; this contract replaces it.

## Parties

- **The engine:** the Prismatic Engine package (`prismatic`), which hosts the generic
  plugin lifecycle (`PluginLoader`, `PluginContext`, manifest schema, capability and
  provider constraint semantics).
- **The plugins:** every shipped plugin under `prismatic/shipped_plugins/` (router,
  mesh, cron, curator, merge, pwp, and any future shipped plugin), each versioned
  independently of the engine and each declaring its own primitive dependencies.

## The version (single source of truth)

The contract version **is the engine package version**: `prismatic.__version__`,
which mirrors `version` in `pyproject.toml`. There is exactly one number. No
separate "plugin API version" exists — a second version is a second thing to forget
to bump, and the failure mode of a forgotten bump is silent contract breakage.
The package version cannot be forgotten: it is the release.

Rationale: plugin compatibility is about the plugin-facing interface (manifest
schema, `PluginContext` surface, lifecycle hooks, capability/provider constraint
semantics). Those change only on engine releases, and every release bumps the one
version. Plugins declare PEP 440 ranges against it; wide ranges
(e.g. `>=0.2.0, <2.0.0`) absorb releases that don't touch the interface.

## The declaration (plugin side)

Every `plugin-manifest.yaml` **must** declare:

```yaml
core_version_constraint: ">=0.2.0, <2.0.0"   # PEP 440 specifier set, required
```

- The field is **required**: `PluginLoader._read_manifest` raises
  `PluginValidationError` on a manifest that omits it. There is no default;
  an undeclared compatibility is a fail-closed rejection, not a guess.
- The specifier is evaluated with `packaging.specifiers.SpecifierSet` against
  `packaging.version.Version(engine_version)`.

## The check (load time, fail closed)

`PluginLoader._validate_manifest` runs the version check **before import** of the
plugin's entry point, before capability/provider validation, before `on_init`:

- Match → load proceeds.
- Mismatch → `PluginValidationError("Core version '<v>' does not satisfy
  constraint '<c>' for plugin '<name>'.")`. The plugin is **not loaded**; the
  error names the running version, the declared constraint, and the plugin.
- The check never raises anything else and never warns-and-continues. A plugin
  that cannot prove compatibility with the running engine does not run.

## Conformance (CI)

- **`.github/workflows/plugin-load.yml`** runs the Gap 13 ship-time load gate on
  every PR touching `prismatic/shipped_plugins/**`, `prismatic/core/registry.py`,
  the plugin architecture, **and `pyproject.toml` / `prismatic/__init__.py`**
  (so a version-bump PR cannot skip the conformance check), plus every push to
  `main`. The gate fails the build if any shipped plugin fails to load — version
  mismatch included.
- **`tests/test_plugin_load_gate.py`** proves the fail-closed behavior:
  `test_gate_fails_on_version_mismatch` ships a plugin with an unsatisfiable
  constraint and asserts the gate reports `version_mismatch` with the
  "does not satisfy constraint" detail.
- **`test_all_shipped_plugin_constraints_include_current_engine_version`**
  validates every real shipped manifest's declared range against the current
  engine version. This is the standing conformance assertion: if the engine
  version moves outside a plugin's declared range, the unit suite goes red
  before the plugin ever ships.

## Versioning policy (what breaks the contract)

A change to any of the following is a **contract break** and must be accompanied
by a major (or the next minor, per the project's semver discipline) engine
version bump **and** a review of every shipped plugin's declared range:

- `plugin-manifest.yaml` schema: required fields, `schema_version` semantics.
- `PluginContext` surface: attributes and methods plugins may touch.
- Lifecycle hooks: `on_init` / `on_suspend` / `on_resume` signatures and ordering.
- Capability constraint semantics (`required_capabilities`).
- Provider constraint semantics (`provider_constraints`).

Anything else — bug fixes, new engine features plugins don't touch, new optional
manifest fields — is not a contract break. When in doubt, treat it as a break:
a falsely-narrow range fails loudly at load time (safe); a falsely-wide range
fails silently in production (unsafe).

## Non-claims

- This contract does **not** cover plugin-to-primitive compatibility (e.g.
  `prismatic-cron` ↔ `swarmcron`): that is declared in each manifest's
  `dependencies.pip` and enforced by the package installer, not the loader.
- This contract does **not** cover plugin-to-plugin compatibility: plugins are
  isolated and must not depend on each other's internals.
- This contract does **not** version the dashboard, gateway HTTP API, or CLI:
  those have their own versioning.

## Acceptance

- [x] Engine version has exactly one source of truth (`prismatic.__version__`).
- [x] `core_version_constraint` is a required manifest field; omission fails closed.
- [x] Mismatch fails closed at load time with an error naming version, constraint, plugin.
- [x] CI runs the conformance gate on plugin, loader, **and version-bump** changes.
- [x] Unit tests prove the fail-closed behavior and the standing conformance assertion.
- [x] PR #380 is superseded and stays unmerged.
