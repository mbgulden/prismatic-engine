# GRO-3509 webhook queue quarantine

## Change
- Added a drainer-level quarantine for the synthetic `TEST-001` webhook row so it is marked `processed` before any dispatch attempt.
- Excluded `TEST-001` from pending/backfill selection to prevent future crash-loop retries.
- Made `scripts/drain_webhook_queue.py` tolerate both historical `raw_json` queue schemas and gateway-created `payload` schemas.
- Verification uses an ad-hoc `/tmp/hermes-verify-*` script rather than committing under `tests/`, because Ned lane governance owns only `scripts/`, `prismatic/`, and `plugins/`.

## Live queue action
The local `prismatic_state/linear_webhook_queue.db` was inspected before implementation. It currently contains zero rows, so there was no live `id = 1` / `TEST-001` row to mutate in this checkout. The shipped quarantine is idempotent and will mark that mock row terminal if it reappears.

## Verification
Run an ad-hoc verifier from `/tmp` that imports `scripts/drain_webhook_queue.py` and checks:

1. `TEST-001` is marked `processed`.
2. `TEST-001` is excluded from pending event selection.
3. Gateway `payload`-schema rows still drain as `raw_json` for dispatcher compatibility.
4. The live queue action is idempotent.
