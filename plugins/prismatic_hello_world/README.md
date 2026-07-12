# prismatic-hello-world

The canonical reference plugin for the Prismatic Engine. Use this directory
as the starting point when authoring a new plugin — duplicate it, rename the
package, and replace the placeholder patterns with your own.

This plugin demonstrates ALL FOUR Sprint 1 contribution patterns on the
`ReviewerRegistry`: secret-pattern, quality-check, impact-rule, and
action-rule. Every field of the `plugin-manifest.yaml` schema is populated
so you can see what a fully fleshed-out manifest looks like in one place.

## How to copy this plugin

Duplicate this directory and rename the package. The new package name MUST
use underscores (not dashes) because Python's import machinery can't load
modules whose dotted names contain dashes. So `prismatic_hello_world` stays
`prismatic_hello_world`, but `my-cool-plugin` would become
`my_cool_plugin`. Update both the directory name and the `entry_point`
field in `plugin-manifest.yaml` to match — they must stay in sync, or the
`PluginLoader` will fail to find your plugin class at load time. You can
keep the display `name` in the manifest with dashes if you prefer
(`my-cool-plugin` is fine there — humans read it, Python doesn't).

After renaming, edit `plugin-manifest.yaml`. Bump `version`, change `name`
and `entry_point`, replace `description`, `author`, and `license`, and
tighten `core_version_constraint` if your plugin depends on a newer API
surface. List every capability your plugin requires from the host under
`required_capabilities` — the `PluginLoader` cross-checks these against the
runtime environment at load time and rejects plugins that ask for
capabilities the host doesn't provide. If you need to opt out of specific
runtime providers (for example, a plugin that conflicts with self-hosted
GitHub Actions runners), list them under `blocked_providers`.

Then edit `plugin.py`. Replace the four placeholder pattern functions with
your own logic: `no_hello_comments` is a quality-check callable (takes a
unified-diff string, returns a list of findings); `escalate_when_hello` and
`force_rework_when_hello_world` are impact-rule and action-rule callables
respectively (each takes `(PRReviewResult, current_value)` and returns a
new value or `None` to defer). Register them all inside `HelloWorldPlugin.on_init`
via `registry.register_secret_pattern`, `registry.register_check`,
`registry.register_impact_rule`, and `registry.register_action_rule`. The
`getattr(context, "review_registry", None)` lookup is defensive — bare
`PluginContext` objects don't expose a registry, and silently no-oping in
that case lets your plugin load cleanly under tests that don't construct a
full dispatcher context.

Finally, register the plugin with the host. Drop the directory into
`$PRISMATIC_HOME/plugins/` (or wherever `PluginLoader.plugins_dir` points)
and the loader will auto-discover it on the next dispatcher start. To
exercise the plugin from a test, build a `PluginContext` (or a richer
duck-typed subclass that exposes a `review_registry`), construct a
`PluginLoader(core_version, plugins_dir)`, and call
`scan_and_load_plugins(context)`. The loader will instantiate your plugin
class, call `on_init`, and your patterns will be live for every subsequent
`PipelineOrchestrator.process()` call. Add plugin-internal tests under
`tests/test_<your_plugin>.py` and ship a few integration tests at the repo
level under `tests/test_plugin_loader_<your_plugin>.py` that build a
`tmp_path` with a minimal manifest and verify the loader picks your plugin
up end-to-end.

## Pattern reference

| Pattern | Registry method | Signature | Purpose |
| --- | --- | --- | --- |
| Secret pattern | `register_secret_pattern(regex, kind, severity)` | `severity ∈ {critical, high, medium, warning}` | Augment built-in secret scanner with company-internal token formats |
| Quality check | `register_check(fn, name=...)` | `fn(diff: str) -> list[Finding]` | Project-specific linter (no-print, no-TODO, docstring coverage, etc.) |
| Impact rule | `register_impact_rule(fn)` | `fn(result, current) -> str \| None` | Override `classify_impact()` per-project (e.g. escalate safety-critical paths) |
| Action rule | `register_action_rule(fn)` | `fn(result, current) -> str \| None` | Override `decide_next_action()` per-project (e.g. force rework on broken tests) |

Impact rules and action rules run in separate pools (Gap 11). A plugin can
register an action-only override without affecting impact classification,
and a rule returning an invalid value for its target channel is silently
ignored rather than leaking across the boundary. First non-None rule wins
in registration order.

## Files in this directory

- `plugin-manifest.yaml` — fully populated example manifest (every field
  from GRO-1497 §1.1).
- `plugin.py` — `HelloWorldPlugin` class + the four pattern functions.
- `__init__.py` — package marker; required for Python to recognise this
  directory as importable.
- `tests/test_hello_world.py` — plugin-internal tests proving the
  registrations work and the patterns behave as advertised.

## See also

- `specs/implementation-plans/GRO-1497-plugin-interface-plan.md` — full
  manifest schema reference.
- `prismatic/interface/plugin.py` — `PrismaticPlugin` ABC + `PluginContext`
  dataclass.
- `prismatic/review/registry.py` — `ReviewerRegistry` (all four
  `register_*` methods, plus `compose()` for thread-safe snapshots).
- `prismatic/core/registry.py` — `PluginLoader` (discovery + dynamic
  import).