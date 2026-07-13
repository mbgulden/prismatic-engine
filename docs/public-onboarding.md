# Public onboarding

This guide is the external first-user path for Prismatic Engine. It avoids operator-only assumptions such as systemd, private Linear workspaces, or Michael-specific paths.

## 1. Install

```bash
git clone https://github.com/mbgulden/prismatic-engine.git
cd prismatic-engine
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
cp .env.example .env
```

## 2. Verify the install

```bash
python scripts/public_launch_smoke.py
python scripts/public_security_readiness_audit.py
```

Expected:

```text
PUBLIC_LAUNCH_SMOKE_OK
PUBLIC_SECURITY_READINESS_OK
```

## 3. Start the dashboard

```bash
prismatic-gateway --host 127.0.0.1 --port 9000
```

Open:

```text
http://127.0.0.1:9000/dashboard
```

## 4. Explore plugins

```bash
python scripts/plugin_architecture catalog
plugin-load-gate
```

Useful endpoints while the Gateway is running:

```text
/api/plugins/catalog
/api/plugins/governance
/api/plugins/jobs
/api/plugins/artifacts
```

## 5. Build your first plugin

Follow the hello plugin tutorial:

```text
docs/hello-plugin-tutorial.md
```

## What is optional?

- Linear/GitHub webhooks are optional for the first-user path.
- Model/provider credentials are optional unless you enable an integration that needs them.
- Systemd is optional and intended for long-running operator deployments only.
