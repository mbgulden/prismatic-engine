# Dashboard screenshots

The current public dashboard is available at:

```text
http://127.0.0.1:9000/dashboard
```

Start it locally:

```bash
prismatic-gateway --host 127.0.0.1 --port 9000
```

## Plugin policy dashboard

The Plugins tab shows governance, durable jobs, artifacts, and policy enforcement state.

![Plugin policy dashboard overview](assets/dashboard-plugin-policy-overview.svg)

Smoke-test markers in the dashboard template:

```text
plugin-policy-summary
plugin-policy-decision
renderPluginPolicy
Policy Enforcement
blocked_reason
```

## Capturing a real screenshot

For release notes or website publishing, run the Gateway locally and capture the browser view after `python scripts/public_launch_smoke.py` passes. Keep screenshots free of credentials and private workspace data.
