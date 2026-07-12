# GRO-3504 — Linear API key rotation evidence

Timestamp: 2026-07-06T13:24Z
Agent: Ned

## Request

Rotate the leaked Linear API key across the Hermes / Prismatic swarm:

- Generate a new Linear API key in Linear settings.
- Update the key in `/home/ubuntu/.hermes_env`.
- Update the key in the `prismatic-dispatcher.service` systemd user-service environment.
- Verify dispatcher and gateway services connect to Linear using the new credentials.

## Findings

This task is blocked at the provider boundary: Linear personal API keys are generated from the Linear UI under Account / Security & Access. I checked the live Linear GraphQL mutation schema with the current credential and found no personal-API-key creation or rotation mutation. The only relevant key/secret mutations exposed were OAuth/webhook/access-key operations, not personal API-key generation.

I did not fabricate or write a replacement token. Rotating to a made-up value would take the dispatcher offline.

## Current credential locations found

Sanitized inventory only; no secret values are recorded here.

- `/home/ubuntu/.hermes_env` contains `LINEAR_API_KEY`.
- `/home/ubuntu/.hermes/profiles/ned/.env` contains `LINEAR_API_KEY`.
- `/home/ubuntu/.hermes/profiles/orchestrator/.env` contains `LINEAR_API_KEY` plus Linear webhook/OAuth variables.
- `/home/ubuntu/.config/systemd/user/prismatic-dispatcher.service` contains an inline `Environment=LINEAR_API_KEY=...` entry.
- `/etc/systemd/system/prismatic-gateway.service` uses `/home/ubuntu/.prismatic/env.d/linear_oauth.env` for OAuth/webhook configuration rather than the personal API key.

## Verification performed with current credential

- Linear API smoke query succeeded: `viewer` and `organization` returned successfully for the GrowthWebDev workspace.
- Prismatic gateway health endpoint succeeded: `GET http://127.0.0.1:9000/health` returned `status: ok`.
- Dispatcher process is running: `/home/ubuntu/.prismatic/venv_stable/bin/prismatic-engine serve` is present in the process table.
- Gateway process is running: `python3 -m prismatic.gateway.server --port 9000` is present in the process table.
- Recent dispatcher log shows active Linear dispatches; recent gateway log shows healthy event-bus activity and `/health` 200 responses.

These checks prove the old credential is still functional. They do not satisfy the rotation acceptance criteria because no new provider-generated key is available.

## Required human action

Michael must create a new Linear personal API key in Linear settings and provide/install it through a secure channel. Once the new key exists, the safe rotation sequence is:

1. Update `/home/ubuntu/.hermes_env`.
2. Update `/home/ubuntu/.hermes/profiles/ned/.env` and `/home/ubuntu/.hermes/profiles/orchestrator/.env` if those profiles should keep direct Linear API access.
3. Replace the inline key in `/home/ubuntu/.config/systemd/user/prismatic-dispatcher.service`.
4. Run `systemctl --user daemon-reload` from a shell with a user systemd bus, then restart `prismatic-dispatcher.service`.
5. Verify Linear GraphQL smoke query, dispatcher log dispatch activity, and gateway `/health`.
6. Revoke the old Linear API key in Linear settings.

## Disposition

Blocked pending provider-generated replacement key. No secret values were committed.
