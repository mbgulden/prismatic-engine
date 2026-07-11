# Prismatic Harnesses

Harnesses adapt concrete agent runtimes to the Prismatic Engine `AgentHarness` contract.

## Hermes harness

`prismatic.harnesses.hermes.HermesHarness` wraps local Hermes bot agents that are managed by systemd.

Operational contract:

- `dispatch(task)` writes a JSON task envelope to `PRISMATIC_HERMES_SPOOL` (default `/tmp/prismatic/hermes-runs`) and starts the configured systemd unit with `systemctl start`.
- `status(run_id)` reads live unit state with `systemctl show --property=ActiveState,SubState,Result,MainPID,ExecMainStatus` and maps systemd state into the normalized harness status values.
- `logs(run_id, tail)` reads recent service logs with `journalctl -u <service> -n <tail> --no-pager --output=short-iso`.
- `cancel(run_id)` stops the service with `systemctl stop`.

Common configuration keys:

```python
HermesHarness({
    "target": "fred",
    "service_template": "hermes-{target}.service",
    "spool_dir": "/tmp/prismatic/hermes-runs",
    "start_on_dispatch": True,
})
```

Use `service_name` when a deployment has one fixed unit name rather than a target-derived template.
