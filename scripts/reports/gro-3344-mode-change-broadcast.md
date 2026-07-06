# GRO-3344 — Gateway mode-change broadcast

## Operator contract

`POST /api/mode` now has three side effects that happen in a single request:

1. Persist the selected mode to `~/.prismatic/system_mode.json` (or `PRISMATIC_SYSTEM_MODE_FILE` when set for tests/operators).
2. Publish a `system.mode_changed` event on the gateway `EventBus` with `mode` and `previous_mode` in the payload.
3. Broadcast the same event shape to clients connected to the gateway FastAPI `/ws` endpoint so the Hub can react without waiting for a restart.

Agents and supervisors that already poll the mode file can pick up the new value on their next poll; real-time UI and bus subscribers receive the immediate notification.

## Verification

Targeted regression coverage lives in `prismatic/test_gateway_mode.py`:

- `test_set_mode_persists_and_publishes_bus_event`
- `test_mode_change_reaches_fastapi_websocket_clients`
- `test_ipc_bridge_accepts_system_mode_changed_event`
