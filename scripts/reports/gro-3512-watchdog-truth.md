# GRO-3512 watchdog source-of-truth note

The watchdog now treats `GET /health` on the live gateway as the liveness source of truth. `systemd --user is-active prismatic-dispatcher.service` and `~/.prismatic/run/heartbeat.pid` remain visible diagnostic signals, but they no longer turn a reachable gateway into a false-red report.

Acceptance mapping:

- False red removed: when `/health` returns HTTP 200, `scripts/watchdog.sh` exits 0 and resets the failure counter even if systemd is inactive/unavailable and `heartbeat.pid` is missing.
- Distinct failure classes: logs label the live endpoint as `source=live_gateway`, service checks as `diagnostic=service`, and heartbeat checks as `diagnostic=heartbeat`.
- Operator truth source: the Python gateway watchdog now records `source_of_truth=live_gateway_health_endpoint` in result dictionaries.

Focused verifier:

```bash
pytest scripts/test_watchdog_health_truth.py -q
# 2 passed
```

Live smoke against the local gateway endpoint:

```text
curl http://localhost:9000/health -> HTTP 200 {"status":"ok", ...}
PRISMATIC_HOME=/tmp/gro3512-watchdog-live PRISMATIC_PORT=9000 bash scripts/watchdog.sh
CHECK 1/3: live gateway health endpoint http://localhost:9000/health → 200 — PASS (source=live_gateway)
CHECK 2/3: systemd service prismatic-dispatcher.service inactive/unavailable — DIAGNOSTIC ONLY (live gateway healthy)
CHECK 3/3: heartbeat file check failed: MISSING: heartbeat.pid ... — DIAGNOSTIC ONLY (live gateway healthy)
✅ Healthy — no failures recorded
exit=0
```
