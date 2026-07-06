# GRO-3483 — Curator restart-loop root cause trace

## Verdict

`prismatic-curator.service` was restart-looping because the runtime process was launched as:

```text
/home/ubuntu/.prismatic/venv_stable/bin/python3 -u -m prismatic.curator.lane --poll-interval 3
```

but the editable `prismatic-engine` install in that venv resolved `prismatic` directly from the live checkout at:

```text
/home/ubuntu/work/prismatic-engine/prismatic
```

During the failing window, that live checkout/runtime mapping could not resolve `prismatic.curator.lane`; the captured Python failure was:

```text
/home/ubuntu/.prismatic/venv_stable/bin/python3: No module named prismatic.curator.lane
```

That is an import/package-shape failure at service startup, not an EventBus processing failure.

## Evidence collected

### Systemd restart loop

`systemctl show prismatic-curator` now reports the service healthy:

```text
ActiveState=active
SubState=running
MainPID=2596566
Restart=always
NRestarts=0
ActiveEnterTimestamp=Mon 2026-07-06 01:16:08 UTC
```

The prior journal window shows the actual loop:

```text
2026-07-05T18:37:50+00:00 Started prismatic-curator.service
2026-07-05T18:37:50+00:00 prismatic-curator.service: Main process exited, code=exited, status=1/FAILURE
2026-07-05T18:37:55+00:00 Scheduled restart job, restart counter is at 1
...
2026-07-05T18:39:29+00:00 Scheduled restart job, restart counter is at 19
2026-07-05T18:39:30+00:00 prismatic-curator.service: Main process exited, code=exited, status=1/FAILURE
2026-07-05T18:39:32+00:00 Stopped prismatic-curator.service
2026-07-05T18:39:32+00:00 Started prismatic-curator.service
```

### Runtime command and editable install target

Current unit metadata:

```text
ExecStart=/usr/bin/env PYTHONUNBUFFERED=1 /home/ubuntu/.prismatic/venv_stable/bin/python3 -u -m prismatic.curator.lane --poll-interval 3
WorkingDirectory=/home/ubuntu/work/prismatic-engine
Environment=PRISMATIC_HOME=/home/ubuntu
Environment=PRISMATIC_BUS_DB=/home/ubuntu/.prismatic/bus/event_log.sqlite
Environment=PRISMATIC_CURATOR_DB=/home/ubuntu/.prismatic/curator/state.sqlite
Environment=PRISMATIC_DIGEST_DIR=/home/ubuntu/.prismatic/curator/digests
```

The venv editable finder pins the package to the shared checkout:

```python
MAPPING = {
    'prismatic': '/home/ubuntu/work/prismatic-engine/prismatic',
    'prismatic_engine': '/home/ubuntu/work/prismatic-engine/prismatic_engine',
    'prismatic_state': '/home/ubuntu/work/prismatic-engine/prismatic_state',
}
```

So a partial checkout, merge-base mismatch, or package/module shadowing in `/home/ubuntu/work/prismatic-engine/prismatic` is immediately visible to systemd on restart.

### Captured failing layer

The stderr capture in `/home/ubuntu/.prismatic/logs/curator-digest.log` contains:

```text
/home/ubuntu/.prismatic/venv_stable/bin/python3: No module named prismatic.curator.lane
<frozen runpy>:128: RuntimeWarning: 'prismatic.curator.lane' found in sys.modules after import of package 'prismatic.curator', but prior to execution of 'prismatic.curator.lane'; this may result in unpredictable behaviour
[curator] digest emitted: /home/ubuntu/.prismatic/curator/digests/2026-07-05.md
```

That points to Python module resolution/package layout before curator runtime logic runs.

### Current health after package restoration

The live checkout now resolves the module:

```text
prismatic.curator -> /home/ubuntu/work/prismatic-engine/prismatic/curator/__init__.py
prismatic.curator.lane -> /home/ubuntu/work/prismatic-engine/prismatic/curator/lane.py
```

The current service has been active since `2026-07-06T01:16:08Z`, and the curator state DB is advancing:

```text
/home/ubuntu/.prismatic/curator/state.sqlite: tagged_events count=16168, max(rowid)=16168
/home/ubuntu/.prismatic/bus/event_log.sqlite: events count=10000, max(rowid)=78093
```

## Root cause

The restart loop was caused by the service's use of an editable install pointed at the mutable shared checkout. At the failing point, the `prismatic.curator` import surface was inconsistent with the unit's `-m prismatic.curator.lane` entrypoint, so Python exited immediately with `No module named prismatic.curator.lane`. Because the unit has `Restart=always`, systemd retried every ~5 seconds and produced a restart loop.

## Fix path

1. Keep `prismatic.curator` as a package with a real `prismatic/curator/__init__.py` and `prismatic/curator/lane.py`; do not deploy a tree where `prismatic/curator.py` shadows or replaces the package entrypoint expected by systemd.
2. Stop running production services directly from a dirty mutable checkout via editable install. Deploy the curator service from a pinned commit/release path, or refresh the editable install only after the target commit passes an import smoke:

   ```bash
   /home/ubuntu/.prismatic/venv_stable/bin/python3 -c 'import prismatic.curator.lane; print(prismatic.curator.lane.__file__)'
   /home/ubuntu/.prismatic/venv_stable/bin/python3 -m prismatic.curator.lane --once
   ```

3. Add a pre-restart guard to the deploy/restart path: refuse `systemctl restart prismatic-curator` if the import smoke fails.
4. Optional hardening: send `StandardOutput`/`StandardError` for `prismatic-curator.service` to a dedicated current log file, so Python tracebacks are captured alongside systemd restart counters instead of being split across rotated curator logs.

## Current state

The service is currently running, so this issue does not require an emergency restart. The actionable fix is deployment hardening: guard the curator restart with import smoke checks and stop exposing systemd to partially merged package layouts.
