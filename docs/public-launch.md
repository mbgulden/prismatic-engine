# Public launch guide

This is the stable public-launch entrypoint for Prismatic Engine. It points first-time users at the maintained launch surfaces and the local-only checks that prove the public path still works.

## What Prismatic Engine is

Prismatic Engine is a local-first orchestration engine for plugin-governed automation. PE Core owns lifecycle, policy, jobs, artifacts, provenance, dashboard/API visibility, and plugin discovery. Plugins own domain-specific implementation.

## North Star and user touchpoint

The one-sentence North Star is:

```text
Install engine → get immediate value → attach governed capabilities as needed → operate them visibly from the dashboard → detach without losing state.
```

See [`north-star.md`](north-star.md) for the canonical milestone map and plugin ecosystem rubric.

The current working operator workflow is Telegram/headless, but the intended product surface is dashboard-first: most commands, tools, functions, approvals, jobs, artifacts, and evidence views should be available directly in the dashboard as the main or only user touchpoint. See [`dashboard-primary-touchpoint.md`](dashboard-primary-touchpoint.md) and [`okf-evidence-map.md`](okf-evidence-map.md).

## Quickstart

```bash
git clone https://github.com/mbgulden/prismatic-engine.git
cd prismatic-engine
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
cp .env.example .env
python scripts/public_launch_smoke.py
```

Expected marker:

```text
PUBLIC_LAUNCH_SMOKE_OK
```

For the detailed first-user path, see [`public-onboarding.md`](public-onboarding.md).

## Run the dashboard

```bash
prismatic-gateway --host 127.0.0.1 --port 9000
```

Open:

```text
http://127.0.0.1:9000/dashboard
```

Useful local APIs:

```text
/api/plugins/catalog
/api/plugins/architecture
/api/plugins/governance
/api/plugins/jobs
/api/plugins/artifacts
/api/plugins/audit-events
/api/pwp/status
```

## Validate plugins

```bash
python scripts/plugin_architecture catalog
python scripts/plugin_architecture validate plugins/pwp/plugin-manifest.yaml
plugin-load-gate
```

Future or incomplete plugins belong under [`plugin-blueprints/`](plugin-blueprints/) until they import, validate, and pass the live load gate.

## Governance, policy, jobs, and artifacts

PE Core provides generic production-readiness surfaces:

- durable plugin jobs and audit events
- generic policy decisions and approval gates
- universal artifact/provenance records
- artifact approval and export policy checks
- dashboard operations views for jobs, artifacts, policy, approvals, provenance, and audit events

See [`prismatic-plugin-architecture.md`](prismatic-plugin-architecture.md) for the full operator/developer contract.

## PWP reference lifecycle

PWP is the canonical reference plugin. It demonstrates the safe lifecycle:

```text
connect
→ create job
→ policy checked
→ approval before publish/export
→ artifact/provenance registered
→ dashboard/API history visible
→ safe disconnect without deleting artifacts
```

See [`pwp-reference-lifecycle.md`](pwp-reference-lifecycle.md).

## Security and public-use boundaries

Before sharing or deploying beyond localhost, run:

```bash
python scripts/public_security_readiness_audit.py
```

Expected marker:

```text
PUBLIC_SECURITY_READINESS_OK
```

Public defaults are local-first. Remote exposure, auth, reverse proxies, TLS, CORS origins, and production deployment hardening are operator responsibilities. See [`security.md`](security.md) and [`public-security-readiness.md`](public-security-readiness.md).

## Troubleshooting

See [`troubleshooting.md`](troubleshooting.md) for common setup errors around editable installs, missing extras, plugin validation, and Gateway startup.
