# Hello plugin tutorial

This tutorial creates a minimal local plugin based on the shipped hello-world plugin.

## 1. Copy the reference plugin

```bash
cp -R plugins/prismatic_hello_world plugins/my_hello_plugin
```

## 2. Rename manifest fields

Edit:

```text
plugins/my_hello_plugin/plugin-manifest.yaml
```

Change at least:

```yaml
name: my-hello-plugin
entry_point: my_hello_plugin.plugin:HelloWorldPlugin
```

Keep `risk_level: low` while your plugin is read-only/local.

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

## 5. Add tests

Create:

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
