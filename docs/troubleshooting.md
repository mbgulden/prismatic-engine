# Troubleshooting

## `python -m pytest` cannot import pytest

Install the package in a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
```

## Gateway starts but dashboard does not load

Confirm the process is listening and use the explicit dashboard URL:

```bash
prismatic-gateway --host 127.0.0.1 --port 9000
open http://127.0.0.1:9000/dashboard
```

If the port is busy, choose another port:

```bash
prismatic-gateway --host 127.0.0.1 --port 9010
```

## Plugin load gate fails

Run:

```bash
plugin-load-gate
python scripts/plugin_architecture catalog
```

Common causes:

- manifest `entry_point` does not match the Python class
- missing required capability
- incomplete plugin placed under `plugins/` instead of `docs/plugin-blueprints/`
- import-time side effects requiring credentials or network access

## Policy blocks a job or artifact

Check the policy preview endpoint:

```bash
curl -s -X POST http://127.0.0.1:9000/api/plugins/policy/preview \
  -H 'content-type: application/json' \
  -d '{"kind":"job_request","plugin_name":"example-plugin","action":"smoke_validate"}' | python -m json.tool
```

Policy decisions are conservative. Publish/export/deploy/delete/write/costly actions require approval or an explicit safe exemption.

## Artifacts cannot publish/export

Verify:

- artifact exists
- artifact has provenance
- artifact approval state is `approved`
- artifact is not rejected
- policy decision is `allow`

## One-command smoke fails

Run it with verbose Python errors:

```bash
python scripts/public_launch_smoke.py
```

The script prints the failing step name before exiting non-zero.
