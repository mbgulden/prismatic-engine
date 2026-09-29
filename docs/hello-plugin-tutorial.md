# Hello plugin tutorial

This tutorial creates a minimal local plugin based on the shipped hello-world plugin.

## 0. Install the engine first

`plugin-load-gate` (step 4) is a console script installed with the package:

```bash
pip install -e ".[gateway]"
```

## 1. Copy the reference plugin

```bash
cp -R plugins/prismatic_hello_world plugins/my_hello_plugin
```

`plugins/` is a symlink to `prismatic/shipped_plugins/`. On a checkout without
symlink support (e.g. some Windows setups), copy from
`prismatic/shipped_plugins/prismatic_hello_world` instead.

## 2. Rename manifest fields

Edit:

```text
plugins/my_hello_plugin/plugin-manifest.yaml
```

Change at least:

```yaml
name: my-hello-plugin
description: "My first plugin — what it does in one line."
entry_point: my_hello_plugin.plugin:HelloWorldPlugin
```

The loader resolves `entry_point` by putting the manifest's parent directory on
`sys.path` when the directory name matches the first module segment, so
`my_hello_plugin.plugin` imports `plugins/my_hello_plugin/plugin.py`. The module
path uses underscores — Python cannot import dashed names.

Also update `plugins/my_hello_plugin/__init__.py`: its docstring still describes
`prismatic_hello_world`.

## 3. Inspect the plugin class

Open:

```text
plugins/my_hello_plugin/plugin.py
```

A plugin should subclass `PrismaticPlugin`, initialize safely, and register tools/capabilities without side effects at import time.

## 4. Validate

```bash
python scripts/plugin_architecture validate plugins/my_hello_plugin/plugin-manifest.yaml
python scripts/plugin_architecture catalog
plugin-load-gate
```

`validate` must report zero errors; `plugin-load-gate` must list
`my-hello-plugin` as `loaded`.

## 5. Replace the copied tests, then add your own

Delete the copied `plugins/my_hello_plugin/tests/test_hello_world.py` — it
imports the **original** `prismatic_hello_world` plugin, not your copy. Then
create:

```text
plugins/my_hello_plugin/tests/test_my_hello_plugin.py
```

Test the class directly and verify no network/credential side effects are required for import.

## 6. Submit a PR

Include:

- manifest
- plugin code
- README
- tests
- policy/governance explanation
- artifact/provenance behavior if your plugin emits files

## Common mistakes

- Storing credential values in the manifest.
- Using `plugins/` for incomplete future work.
- Skipping `plugin-load-gate`.
- Implementing publish/export without an approval gate.
- Forgetting step 0: `plugin-load-gate` only exists after `pip install`.
- Leaving the copied `test_hello_world.py` in place: it tests the original plugin.
