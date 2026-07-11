# GRO-3482 — prismatic-consumer restart-loop root cause

Timestamp: 2026-07-06T14:02:25Z

## Finding

Root cause: `prismatic-consumer.service` was crashing at Python import time because `prismatic.supervisor.recovery` dereferenced a missing `Settings.prismatic_supervisor_dlq` property while importing the supervisor recovery module.

Failing layer: configuration/settings contract between `prismatic/supervisor/recovery.py` and `prismatic/core/settings.py`, surfaced through the systemd consumer service.

Impact: systemd restarted `prismatic-consumer.service` every 5 seconds. During the observed window the consumer never reached its event-draining loop, so SQLite bus events could accumulate and AGY supervisor dispatch would not run.

## Evidence

### Stack trace

`/home/ubuntu/.prismatic/logs/consumer.err.log` contains repeated copies of this traceback:

```text
Traceback (most recent call last):
  File "/home/ubuntu/.hermes/profiles/orchestrator/scripts/event_handlers/dispatch_consumer_v3.py", line 41, in <module>
    from prismatic.supervisor.recovery import (
  File "/home/ubuntu/work/prismatic-engine/prismatic/supervisor/recovery.py", line 57, in <module>
    DLQ_PATH = Path(Settings().prismatic_supervisor_dlq or os.path.expanduser("~/.prismatic/supervisor/dlq.jsonl"))
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/work/prismatic-engine/prismatic/core/settings.py", line 38, in __getattr__
    raise AttributeError(f"'Settings' object has no attribute '{name}'")
AttributeError: 'Settings' object has no attribute 'prismatic_supervisor_dlq'. Did you mean: 'prismatic_supervisor_max'?
```

Probe output at 2026-07-06T14:02:25Z:

```text
consumer.err.log bytes 26010
AttributeError count 68
ModuleNotFound count 0
first AttributeError offset 644
```

### systemd restart-loop signal

`journalctl -u prismatic-consumer --since '2026-07-05 18:34:00' --until '2026-07-05 18:38:00'` shows the service exiting with status 1 and restarting every 5 seconds:

```text
2026-07-05T18:34:53+00:00 ... Started prismatic-consumer.service
2026-07-05T18:34:53+00:00 ... prismatic-consumer.service: Main process exited, code=exited, status=1/FAILURE
2026-07-05T18:34:58+00:00 ... Scheduled restart job, restart counter is at 1.
2026-07-05T18:34:58+00:00 ... Started prismatic-consumer.service
2026-07-05T18:34:58+00:00 ... prismatic-consumer.service: Main process exited, code=exited, status=1/FAILURE
2026-07-05T18:35:03+00:00 ... Scheduled restart job, restart counter is at 2.
```

`systemctl show prismatic-consumer` confirmed the restart cadence:

```text
RestartUSec=5s
ExecStart=/usr/bin/python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/event_handlers/dispatch_consumer_v3.py
```

## Why it failed

`dispatch_consumer_v3.py` imports `prismatic.supervisor.recovery` before entering the consumer loop. The recovery module computed `DLQ_PATH` at module import time from `Settings().prismatic_supervisor_dlq`. In the deployed settings registry, `Settings.__getattr__` raises `AttributeError` for unknown properties unless `PRISMATIC_TESTING=1` or the matching environment variable exists. Production did not define `PRISMATIC_SUPERVISOR_DLQ`, and `Settings` had no typed `prismatic_supervisor_dlq` property, so the import aborted the process before the consumer could run.

A related earlier failure mode is visible in rotated logs: before the recovery module was present on the active import path, the same import line failed with `ModuleNotFoundError: No module named 'prismatic.supervisor.recovery'`. After the module landed, the failure advanced to the missing DLQ setting contract above.

## Fix path

The clear fix is to make the settings contract explicit and keep import-time configuration defaults safe:

1. Add `Settings.prismatic_supervisor_dlq` with default `~/.prismatic/supervisor/dlq.jsonl` and `PRISMATIC_SUPERVISOR_DLQ` override.
2. Keep `prismatic.supervisor.recovery` importable when `PRISMATIC_SUPERVISOR_DLQ` is unset.
3. Add regression tests that import `prismatic.supervisor.recovery` with the env var unset and with a custom override.

This fix path already appears in commit `ff787f1698e9ff2955552e29a1bc1ac0df002ce5`:

```text
[Ned] Fix supervisor DLQ settings fallback (#GRO-3511)

prismatic/core/settings.py:
+    @property
+    def prismatic_supervisor_dlq(self) -> str:
+        return os.environ.get("PRISMATIC_SUPERVISOR_DLQ", str(Path.home() / ".prismatic" / "supervisor" / "dlq.jsonl"))

prismatic/supervisor/tests/test_recovery.py:
+def test_recovery_import_uses_default_dlq_when_env_missing():
+def test_recovery_import_uses_env_dlq_override(tmp_path):
```

## Current runtime check

As of this investigation, the live service is no longer restart-looping:

```text
systemctl is-active prismatic-consumer -> active
MainPID=2746156
NRestarts=0
ActiveEnterTimestamp=Mon 2026-07-06 05:47:44 UTC
```

So GRO-3482 is root-caused. The remaining engineering work, if any, is to ensure the `ff787f16` settings contract fix is included in the branch/release line that owns `prismatic-consumer.service`; the live service has been stable since 2026-07-06 05:47:44 UTC.
