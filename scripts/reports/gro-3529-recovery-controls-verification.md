# GRO-3529 — Recovery controls with live proof

## Scope
Expose visible restart, retry, and replay controls in the Prismatic Hub agent drawer and give each control a server-side effect that the UI can display after the action.

## Implementation
- Added `POST /api/dashboard/recovery-control` to the gateway. It accepts `restart`, `retry`, and `replay`, writes a durable JSON ledger under `PRISMATIC_STATE_DIR/dashboard_recovery_controls.json`, and returns a visible status string.
- Added `GET /api/dashboard/recovery-control/status` so the dashboard can read the latest recovery-control proof.
- Added Prismatic Hub drawer controls: Restart, Retry, Replay.
- Each UI control calls the gateway, appends a Recovery Control activity-feed entry, and updates the visible drawer status with the server acknowledgement.

## Verification evidence
- `npm run build` in `plugins/hermes-plugin-prismatic-hub/` → webpack compiled `dashboard/dist/index.js` successfully in 1238 ms.
- First pytest run caught a FastAPI response-model issue for `dict[str, Any] | JSONResponse`; fixed with `response_model=None` on the recovery-control route.
- `python3 -m pytest prismatic/tests/test_gateway_recovery_controls.py -q` → `2 passed, 5 warnings in 0.68s`.
- Targeted ad-hoc verifier `/tmp/hermes-verify-mm_1eudt.py` → `AD-HOC VERIFICATION PASSED: recovery controls source/dist/backend/tests/report are present; HEAD includes all deliverables; worktree clean`; verifier was removed after the run.

## Notes
The gateway endpoint records operator intent rather than shelling out from the browser. That is deliberate: the server-side effect is durable, auditable state plus event-bus publication, not an unauthenticated process kill/restart from a dashboard button.
