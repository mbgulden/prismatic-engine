# Public security readiness audit

This document is the public-repository security pass for external users. It records what Prismatic Engine checks today, what assumptions are safe for local use, and what a remote/public deployment must configure before exposure.

## Status summary

| Area | Public-readiness status | Notes |
|---|---:|---|
| Secret scanning | ✅ local audit script | `scripts/public_security_readiness_audit.py` scans public text files for high-confidence raw secret values. |
| Environment variable examples | ✅ safe local defaults | `.env.example` has local-only defaults and no credential values. |
| Redaction tests | ✅ policy/artifact coverage | Policy and artifact paths redact token-like values and credential-keyed fields. |
| API auth expectations | 🟡 documented local-first | Mutating plugin APIs are intended for local/trusted operator use unless deployed behind external auth. |
| Dashboard exposure review | 🟡 documented local-first | Dashboard is for local/trusted operators; do not expose directly to the public internet. |
| Local vs remote deployment assumptions | ✅ documented | Local quickstart is safe; remote deployments require TLS, auth, explicit CORS, and secret management. |
| CORS review | ✅ hardened | Gateway defaults to local origins and rejects wildcard CORS with credentials. |
| Destructive action policy | ✅ enforced | Publish/export/deploy/delete/write/costly/public/production actions require approval or are blocked. |
| Dependency audit | 🟡 metadata check | Public audit verifies declared dependency metadata; run full dependency scanners in release CI. |
| Artifact path traversal checks | ✅ enforced | Artifact hashing only reads repo, configured state dir, or `/tmp`; traversal outside allowed roots is blocked. |
| Plugin sandbox assumptions | 🟡 documented | Plugins are Python code and are not OS-sandboxed by PE Core. Treat plugin installation as trusted code execution. |
| External service credential flow | ✅ documented | Store credentials in environment/secret manager only; manifests/docs may name env vars but never values. |

## Run the audit

```bash
python scripts/public_security_readiness_audit.py
```

Expected result:

```text
PUBLIC_SECURITY_READINESS_OK
```

The audit is local-only and credential-free. It does not call external services.

## Secret scanning

The public audit scans committed text-like files and fails on high-confidence secret patterns including:

- OpenAI-style `sk-...` tokens
- GitHub personal access tokens
- Slack bot/app tokens
- Google API-key shaped values
- PEM private key blocks
- long bearer-token values

This is not a replacement for GitHub secret scanning, pre-receive hooks, or organization-level scanners. Treat it as a fast local gate.

## Environment variable examples

`.env.example` contains only safe local defaults:

```text
PRISMATIC_STATE_DIR=./prismatic_state
PRISMATIC_HOST=127.0.0.1
PRISMATIC_PORT=9000
PRISMATIC_ALLOWED_IPS=127.0.0.1,::1
PRISMATIC_CORS_ORIGINS=http://127.0.0.1:9000,http://localhost:9000
PRISMATIC_PUBLIC_DEMO_MODE=1
```

Do not place real credential values in committed examples. For external services, document the credential flow in plugin docs and ask operators to export secrets from their shell or secret manager.

## Redaction tests

Redaction expectations:

- fields named like `token`, `secret`, `password`, `api_key`, `credential`, or `auth` redact to `[REDACTED]`
- token-like values redact even when nested
- environment variable names such as `PWP_SERVICE_API_KEY` are allowed to remain visible because they are not credential values
- policy decision `context` and artifact metadata/provenance/input summaries are redacted before persistence/return

## API auth expectations

Current public posture is **local/trusted operator by default**:

- observability endpoints use IP allowlist and optional bearer token
- plugin job/artifact/policy APIs are intended for local/trusted operators unless a remote deployment adds auth at the edge or reverse proxy
- OpenAPI schema generation is disabled for the Gateway
- webhooks must use HMAC secrets when configured

Remote deployments must add:

1. TLS termination
2. identity-aware access or reverse-proxy auth
3. explicit allowlisted dashboard/API origins
4. network restrictions for mutation endpoints
5. secret management outside repo files

## Dashboard exposure review

The dashboard can show operational state, plugin names, job/action names, artifact references, and policy reasons. It must not be published directly to the open internet.

Recommended remote deployment pattern:

```text
browser → identity-aware proxy/TLS → Prismatic Gateway on private network
```

Do not expose dashboard or plugin mutation APIs without authentication and authorization.

## Local vs remote deployment assumptions

Local quickstart assumptions:

- loopback host
- local state directory
- no external credentials required
- no systemd required
- no public network exposure

Remote assumptions:

- operator controls network edge
- explicit CORS origins
- auth in front of Gateway
- durable state path with access controls
- secrets from env/secret manager only
- dependency and container scanning in CI/deployment pipeline

## CORS review

Gateway CORS defaults to:

```text
http://127.0.0.1:9000
http://localhost:9000
```

Remote deployments may set:

```text
PRISMATIC_CORS_ORIGINS=https://dashboard.example.com
```

Wildcard CORS is rejected when credentials are enabled.

## Destructive action policy

The plugin policy gate treats these action tokens as risky:

```text
publish
export
deploy
delete
destroy
write
overwrite
batch
costly
external-service
credentialed
production
public
```

Job starts, artifact publish-ready transitions, and artifact export attempts call the policy layer before mutating state.

## Dependency audit

The public audit verifies core dependency metadata is present in `pyproject.toml`, including Python version and Gateway dependencies. For release builds, also run organization-approved dependency scanning such as Dependabot, pip-audit, OSV-Scanner, or GitHub Advanced Security.

## Artifact path traversal checks

Artifact hashing intentionally refuses arbitrary filesystem reads. Local file fingerprinting is limited to:

- repository root
- configured Prismatic state directory
- `/tmp`

External URLs are stored as references and are not fetched by the artifact registry.

## Plugin sandbox assumptions

Plugins are Python code loaded by Prismatic Engine. PE Core validates manifests and capabilities, but it does **not** provide an OS-level sandbox. Installing a plugin is equivalent to trusting code execution.

For untrusted plugins, run PE inside a separate user/container/VM with constrained filesystem and network permissions.

## External service credential flow

External service plugins should document:

- required credential environment variable names
- least-privilege scopes
- read vs write permissions
- rotation procedure
- whether credentials are needed for import, validation, dry-run, or only execution

Manifests should store only env var names and redacted status, never credential values.
