# Prismatic Engine

**Local-first agent orchestration, plugin governance, and artifact provenance for teams building with AI agents.**

Prismatic Engine gives you a small, inspectable control plane for agent work:

- a FastAPI Gateway and dashboard
- plugin discovery and load gates
- durable plugin jobs and audit events
- universal artifact/provenance records
- policy/approval enforcement before risky work runs
- quality gates and one-command smoke checks

Prismatic is still alpha, but the public path below is designed to work from a clean checkout without Michael-specific infrastructure.

---

## Quick start

### Requirements

| Component | Supported |
|---|---|
| Python | 3.10, 3.11, 3.12, 3.13 |
| Node | Optional; only needed for plugin/dashboard assets that declare Node tooling |
| OS | Linux/macOS for local development; Linux recommended for service deployment |
| Package manager | `pip` with a Python virtual environment |

### Install locally

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

Expected result:

```text
PUBLIC_LAUNCH_SMOKE_OK
```

### Run the Gateway/dashboard

```bash
. .venv/bin/activate
prismatic-gateway --host 127.0.0.1 --port 9000
```

Then open:

```text
http://127.0.0.1:9000/dashboard
```

Useful API checks:

```bash
curl -s http://127.0.0.1:9000/api/plugins/catalog | python -m json.tool
curl -s http://127.0.0.1:9000/api/plugins/governance | python -m json.tool
```

---

## Minimal demo

Run the included smoke/demo command:

```bash
python scripts/public_launch_smoke.py
```

The smoke test verifies:

- package imports
- CLI availability
- plugin catalog generation
- shipped plugin load gate
- Gateway TestClient health
- plugin governance API
- job/artifact/policy API basics
- dashboard policy markers

For a plugin-specific demo, see the hello plugin tutorial:

```text
docs/hello-plugin-tutorial.md
```

---

## Architecture overview

Prismatic has five public-facing layers:

```text
CLI / Gateway / Dashboard
        │
        ▼
Plugin catalog + loader
        │
        ▼
Policy + governance gate
        │
        ▼
Durable jobs + audit events
        │
        ▼
Artifact/provenance registry
```

Core files:

| Area | Files |
|---|---|
| CLI | `prismatic/cli/__init__.py` |
| Gateway/dashboard | `prismatic/gateway/server.py`, `prismatic/gateway/templates/dashboard.html` |
| Plugin loader | `prismatic/core/registry.py` |
| Plugin architecture/catalog | `prismatic/plugin_architecture.py` |
| Jobs/audit | `prismatic/plugin_jobs.py` |
| Artifacts/provenance | `prismatic/plugin_artifacts.py` |
| Policy enforcement | `prismatic/plugin_policy.py` |
| Public smoke | `scripts/public_launch_smoke.py` |
| Release smoke | `scripts/release_smoke.py`, `scripts/release_check.py` |

Deep dive:

- [Public onboarding guide](docs/public-onboarding.md)
- [Architecture overview](docs/public-architecture.md)
- [Plugin developer guide](docs/plugin-developer-guide.md)
- [PWP reference lifecycle](docs/pwp-reference-lifecycle.md)
- [Hello plugin tutorial](docs/hello-plugin-tutorial.md)
- [Troubleshooting guide](docs/troubleshooting.md)
- [Public security readiness audit](docs/public-security-readiness.md)
- [Release process](docs/release-process.md)
- [Release checklist](docs/release-checklist.md)
- [Migration notes](docs/migrations.md)
- [Upgrade guide](docs/upgrade-guide.md)
- [Stable CLI entrypoints](docs/stable-cli-entrypoints.md)
- [Dashboard screenshots](docs/dashboard-screenshots.md)
- [Release notes](CHANGELOG.md)
- [Security policy](SECURITY.md)
- [Contribution guide](CONTRIBUTING.md)

---

## Environment configuration

Start with:

```bash
cp .env.example .env
cp config/prismatic.sample.yaml config.local.yaml
```

Or bootstrap a fresh local release/dev environment:

```bash
bash scripts/bootstrap_env.sh
```

The default `.env.example` is safe: it contains no secrets and points local state into `./prismatic_state`.

Credentials such as `GITHUB_TOKEN`, `LINEAR_API_KEY`, or provider keys are optional unless you enable integrations that require them. Never commit `.env`.

---

## Plugin development in 60 seconds

Inspect shipped plugins:

```bash
python scripts/plugin_architecture catalog
plugin-load-gate
```

Copy the hello plugin as a starting point:

```bash
cp -R plugins/prismatic_hello_world plugins/my_plugin
```

Then edit:

```text
plugins/my_plugin/plugin-manifest.yaml
plugins/my_plugin/plugin.py
```

Read the full guide:

```text
docs/plugin-developer-guide.md
```

---

## Verification commands

```bash
python scripts/public_launch_smoke.py
python scripts/public_security_readiness_audit.py
python scripts/dashboard_visual_qa.py
python scripts/release_check.py
python scripts/release_smoke.py
python scripts/plugin_architecture catalog
plugin-load-gate
python -m pytest tests/test_plugin_policy.py tests/test_plugin_artifacts.py tests/test_plugin_jobs.py -q
```

For formatting/linting if `ruff` is installed:

```bash
python -m ruff check prismatic tests scripts
python -m ruff format --check prismatic tests scripts
```

---

## Deployment notes

The quickstart does not require systemd. Long-running operator deployments may use the service scripts and runbooks in `docs/` and `scripts/ops/`, but those are intentionally separate from the public first-user path.

---

## License

Prismatic Engine is licensed under **AGPL-3.0-only**. See [`LICENSE`](LICENSE).

## Security

Please read [`SECURITY.md`](SECURITY.md) before reporting vulnerabilities or wiring credentials.
