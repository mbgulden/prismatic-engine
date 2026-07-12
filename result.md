# GRO-3163: Per-agent API quota tracking + auto-throttle

## Issue Description
Opus quota is hit after ~15 dispatches/day. No throttling logic existed, meaning agents fired dispatches until Google returned 429, causing dispatches to fail.

## Resolution
1. **Model Daily limits**: Defined daily dispatch limits mapping for models. The `opus` model limit is set to `14` dispatches daily.
2. **Quota Tracking in `telemetry_credit_ledger`**:
   - Added a daily dispatch count tracker function `get_daily_dispatch_count(model, db_path)` that counts `dispatch` operations for a specific model since UTC midnight.
   - Updated `prismatic/curator/lane.py` to record every successfully spawned or queued dispatch under `operation="dispatch"` in `telemetry_credit_ledger` via `get_collector().record_credit()`.
3. **Auto-Throttle & Auto-Pause**:
   - Updated `decide_dispatch()` in `prismatic/curator/dispatcher.py` to query `get_daily_dispatch_count` and automatically pause dispatches for the requested model if it reaches the daily limit.
   - Surfaced the `quota_paused: bool` state in both `DispatchDecision` and its serialized `to_dict()` form.
4. **Unit Tests Added**:
   - `test_get_daily_dispatch_count`: Verifies correct database querying of dispatches within the daily UTC window.
   - `test_decide_dispatch_quota_paused`: Verifies that `decide_dispatch` returns `should_dispatch=False` and `quota_paused=True` once the limit is reached.
