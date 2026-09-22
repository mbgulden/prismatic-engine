# Contributing guide

This is the stable docs entrypoint for contributing to Prismatic Engine. The repository-level contribution policy lives in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Start clean

```bash
git checkout main
git pull --ff-only
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
```

## Verify the public path

```bash
python scripts/public_launch_smoke.py
python scripts/public_security_readiness_audit.py
python scripts/release_smoke.py
```

Expected markers:

```text
PUBLIC_LAUNCH_SMOKE_OK
PUBLIC_SECURITY_READINESS_OK
RELEASE_SMOKE_OK
```

## Plugin changes

For plugin architecture or plugin implementation changes, run at least:

```bash
python3 -m py_compile prismatic/plugin_architecture.py prismatic/pwp_integration.py prismatic/interface/plugin.py prismatic/core/registry.py prismatic/gateway/server.py tests/test_plugin_architecture.py
python3 -m pytest tests/test_plugin_architecture.py tests/test_plugin_loader_capability_validation.py tests/test_plugin_load_gate.py tests/test_pwp_integration.py -q
python3 scripts/plugin_architecture catalog
python3 scripts/plugin_architecture blueprint asset-forge-3d --class asset-forge-3d
python3 scripts/plugin_architecture validate docs/plugin-blueprints/asset_forge_3d/plugin-manifest.yaml
```

Also verify related job/artifact/policy/dashboard tests when those surfaces change.

## Safety rules

- Do not commit secrets.
- Use env var names and redacted status only.
- Keep incomplete future plugins under `docs/plugin-blueprints/`, not live `plugins/`.
- Prefer PE Core generic job/artifact/policy/governance APIs over plugin-specific hacks.
- Do not make destructive, costly, publish/export, production, or credentialed actions bypass approval gates.
- Do not delete artifacts on plugin disconnect.
- Keep the containment boundary: "dumb pipes, smart policy" — capability plugins provide infrastructure only, never agent choreography, tool-call decisions, or conversation state (see docs/infrastructure-capabilities.md).

## Pull requests

A good PR includes:

- small scoped diff
- docs for public-facing behavior
- tests or smoke coverage
- exact verification commands and outputs
- no generated/vendor/venv files
- no private paths or operator-only assumptions in public docs
