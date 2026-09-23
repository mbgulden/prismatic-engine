# Dashboard control-plane authorization

The gateway has one fail-closed HTTP middleware in
`prismatic.gateway.control_auth`. It protects control-plane mutations before
FastAPI route dispatch. This is source behavior only; adding the source does not
configure credentials or deploy/reload any running gateway.

## Credential file

Set `PRISMATIC_CONTROL_AUTH_FILE` to an **absolute** path outside the source
tree. The target must be a regular file, must not be a symbolic link, and must
have no group or world permission bits (normally mode `0600`). The gateway
reads and validates it at protected-request time, so removal, replacement, or a
permission/configuration error fails closed immediately. Imports never read the
file.

The strict JSON schema is:

```json
{
  "version": 1,
  "credentials": [
    {
      "actor": "dashboard-operations",
      "token_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "roles": ["operator"]
    }
  ]
}
```

Only the keys shown are accepted. `credentials` must be non-empty. Every actor
must be a non-empty string, every digest must be a unique lowercase 64-character
SHA-256 hexadecimal digest, and every role list must be non-empty and contain
no duplicates. Supported roles are `operator`, `approver`, and `executor`.
Roles are independent: a credential requiring more than one capability must
list every role explicitly.

Generate a digest without putting plaintext in the JSON file, for example by
using an appropriately secured offline provisioning process. Plaintext control
tokens must never be stored in this file, command histories, logs, or source.

## Request authentication

Protected requests authenticate only through:

```text
Authorization: Bearer <token>
```

Cookies, query parameters, and request bodies are never token sources. The
middleware hashes the presented token with SHA-256 and compares digests with
constant-time comparisons. It does not log or return tokens or digests.

Missing configuration, invalid configuration, a missing/invalid Bearer token,
or an unknown token returns a generic `401` and `WWW-Authenticate: Bearer`.
An authenticated credential lacking the exact required role receives a generic
`403`.

On success, handlers retain their existing request and response behavior. The
middleware adds these non-secret metadata values:

- `request.state.control_actor`: configured actor (available only in-process);
- `request.state.control_roles`: configured explicit role set;
- `request.state.control_authorization_class`: role required for this request;
- `X-Prismatic-Control-Authorization` response header with value `authorized`;
- `X-Prismatic-Control-Role: operator|approver|executor` response header.

Actor identity, token identity, and digests are not included in response
headers.

## Policy

- `GET`, `HEAD`, and `OPTIONS` are read-only and bypass control authorization.
- Paths under `/webhooks/*` bypass only this middleware. Their provider-specific
  signature checks remain authoritative and unchanged.
- Every other HTTP method, including mutations to unknown routes, is protected
  and defaults to `operator`.
- Approval, rejection, approval-request, operator-action approval,
  `pr-approval`, `pr-create-approved`, promotion-decision, and final
  authorization route families require `approver`.
- Real-executor arming/invocation, PR-executor, and executor-run canary routes
  (including `executor-runs/canary-dry-run`) require `executor`.
- `/native-crons/{id}/action` requires `executor` when its parsed JSON action is
  `run`; other lifecycle actions require `operator`. Body inspection preserves
  the bytes for the downstream handler.
- Queue retry/purge and plugin, PWP, quota, foundation, skills, AGY-ledger, and
  other ordinary mutations require `operator` unless an approval or executor
  rule above is more specific.

Malformed native-cron JSON is not treated as `run`; it still requires the
default `operator` role and is then handled normally by FastAPI/the route. This
preserves existing validation behavior after authorization succeeds.

## Portal Phase 1: auth providers and API tokens

The credential file above still works exactly as before and is evaluated
first. Two additional subject kinds are accepted, in order:

1. **Portal API tokens** (`Authorization: Bearer <token>`): revocable tokens
   minted via `POST /api/tokens`, scoped to the `viewer` or `operator` portal
   role. Secrets are stored as SHA-256 digests in
   `$PRISMATIC_STATE_DIR/db/portal_tokens.db` (mode 0600) and are shown only
   once, at creation. A token is valid only on the instance that minted it.
   `viewer` passes read-only routes and is rejected (403) on any protected
   route; `operator` implies the `operator`/`approver`/`executor` control
   roles. Tokens can never mint tokens.
2. **Auth-provider identities**: the configured provider
   (`prismatic.gateway.auth_providers`) establishes identity per request --
   `cloudflare-access` (edge email header), `basic-auth` (instance users
   file), `tailnet-only` (Tailscale peer), `localhost-only` (loopback) --
   and the instance's `identity_roles` mapping (`$PRISMATIC_STATE_DIR/instance.json`)
   maps that identity to `viewer` / `operator` / `admin`. The portal never
   hard-codes a provider; the default (`cloudflare-access`, no mapping) keeps
   the historical posture unchanged.

The provider is chosen at install time:
`python -m pe.deploy.install --auth-provider <name> --admin-identities <subjects>`,
persisted to `instance.json` by the `auth` install step. Existing instances
can be managed without reinstalling via `python -m pe.deploy.instance`
(`show`, `set-provider`, `grant <subject> <role>`, `revoke <subject>`).

`GET /api/tokens`, `POST /api/tokens`, and `DELETE /api/tokens/{id}` require
the `admin` portal role (granted only via the identity mapping) and are
managed from the dashboard Settings tab. On success the middleware also sets
`request.state.portal_roles` and `request.state.auth_provider` for audit use.

## Operational validation

Before enabling a token for a client, validate in a non-production environment:

1. reads work without a token;
2. ordinary mutations return `401` without a valid file/token;
3. each token can access only its explicitly listed roles;
4. native-cron `run` and real-executor paths reject non-executors;
5. approval paths reject non-approvers;
6. responses and logs contain no presented token or digest.

Keep proxy-level containment and provider webhook verification in place. This
middleware does not replace either boundary.
