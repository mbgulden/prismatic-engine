# my-greeter — minimal example plugin

A from-scratch minimal plugin: one manifest, one module, one registered check.
It mirrors the steps in `docs/hello-plugin-tutorial.md` without copying the
hello-world reference.

## Layout

```text
my_greeter/
  __init__.py
  plugin.py              # GreeterPlugin(PrismaticPlugin)
  plugin-manifest.yaml   # entry_point: my_greeter.plugin:GreeterPlugin
```

## Run it

```bash
# from the repo root, with the package installed:
python3 examples/hello-plugin/run.py
```

Expected output ends with `HELLO_PLUGIN_EXAMPLE_OK`.

## Validate it the same way the tutorial does

```bash
python scripts/plugin_architecture validate examples/hello-plugin/my_greeter/plugin-manifest.yaml
```

## Load it for real

Copy `my_greeter/` under `plugins/` (or `prismatic/shipped_plugins/`) and run
`plugin-load-gate` — it should list `my-greeter` as `loaded`.
