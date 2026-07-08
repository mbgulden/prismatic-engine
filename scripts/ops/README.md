# Operations Notes

## GRO-3617 dispatcher process observer

`prismatic.dispatcher` now registers every `subprocess.Popen` returned by AGY/Jules/Codex launchers with a daemon process observer. The observer updates `telemetry_agent_runs` through `TelemetryCollector.update_agent_run()` when the child exits, closing the prior `status='dispatched'` / `end_time=NULL` gap that blocked the GRO-2978 acceptance query.

Regression coverage lives in `prismatic/tests/test_dispatch_observer_gro3617.py` and exercises both closure updates and the GRO-2979 dispatch-cap helpers.
