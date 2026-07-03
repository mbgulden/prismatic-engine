# GRO-2993 plugin registration telemetry

## Change

Plugin discovery now records best-effort rows in `telemetry_plugin_registered` via `TelemetryCollector.record_plugin_registered()`.

Runtime behavior:

- successful plugin load records `plugin_name`, `version`, `success=1`, and `registered_at`;
- failed plugin registration records the plugin directory name, `success=0`, and the exception string;
- telemetry errors are swallowed so plugin loading remains non-blocking.

## Operator verification

```sql
SELECT plugin_name, success, COUNT(*)
FROM telemetry_plugin_registered
GROUP BY plugin_name, success;
```

## Evidence from this run

- `python3 -m pytest prismatic/test_plugin_registration_telemetry.py tests/test_pwp_hooks.py -q` → `8 passed in 0.27s`
- live SQLite probe after loading a fixture plugin → `[('demo-plugin', 1, 1)]`
- broader neighboring tests `tests/test_three_plugins_coexist.py tests/test_pwp_undo.py` still have pre-existing failures unrelated to this change (`Dispatcher.initialize_plugins` missing; rollback expectation mismatch).
