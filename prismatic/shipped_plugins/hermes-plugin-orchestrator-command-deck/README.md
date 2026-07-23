# Orchestrator Command Deck

The Orchestrator Command Deck is the operator-facing control plane for swarm status, mode changes, agent lifecycle controls, queue operations, and log feedback.

## Operator feedback contract

The dashboard must not present local-only button success as if the backend accepted the command. The React plugin uses the persistent plugin API at `/api/plugins/hermes-plugin-orchestrator-command-deck` for every operator action:

- `GET /status` hydrates mode, agents, queue, and live sync metadata.
- `POST /mode` commits Interactive / Collaborative / Autonomous mode changes.
- `POST /agent/{agent}/control` commits Start / Pause / Resume / Kill lifecycle actions and appends an agent log.
- `POST /tasks/dispatch` creates a persistent queue item.
- `POST /tasks/{task_id}/reorder` moves queue items up or down.
- `POST /tasks/{task_id}/cancel` removes a queue item and appends an agent log.
- `GET /agent/{agent}/logs` refreshes the selected log console.

The header shows a live/degraded/error API badge. The mode panel shows the current pending command, last command failure, or the persistent-control success contract so operators can tell whether they are driving the backend or only seeing fallback state.

## Verification

For command-plane hardening, verify both layers:

1. `node --check plugins/hermes-plugin-orchestrator-command-deck/dashboard/dist/index.js`
2. A FastAPI `TestClient` smoke test that exercises status, mode, dispatch, reorder, agent control, logs, and cancel against `dashboard/plugin_api.py` using a temporary `HERMES_HOME`.
