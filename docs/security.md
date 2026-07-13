# Security guide

This is the stable public security entrypoint for Prismatic Engine. The detailed readiness audit lives in [`public-security-readiness.md`](public-security-readiness.md), and vulnerability reporting lives in [`../SECURITY.md`](../SECURITY.md).

## Local-first defaults

Prismatic Engine's public quickstart is designed for local development:

```text
127.0.0.1 / localhost dashboard and APIs
local editable install
local state files
credential-free smoke tests
```

Do not expose the Gateway directly to the public internet without adding production controls such as auth, TLS, reverse proxy hardening, explicit CORS origins, logging, backups, and deployment-specific rate limits.

## Required readiness check

Run:

```bash
python scripts/public_security_readiness_audit.py
```

Expected marker:

```text
PUBLIC_SECURITY_READINESS_OK
```

The audit checks public-facing security assumptions including redaction, local CORS defaults, wildcard CORS rejection with credentials, safe examples, artifact path traversal assumptions, and public documentation warnings.

## Secrets and credentials

- Do not commit raw secrets in code, docs, manifests, tests, fixtures, or examples.
- Manifest and docs should list env var names only.
- Use redacted statuses such as `configured`, `missing`, or `redacted`.
- If tests need detector fixtures, construct token-like strings at runtime rather than committing high-confidence secret patterns.

## Plugin policy safety

PE Core policy gates destructive/costly/public actions. Approval should be required for actions such as:

```text
publish
export
deploy
delete
destroy
write / overwrite
batch
costly
credentialed
external-service
production / public
```

Rejected jobs cannot run. Publish/export operations require provenance and approval before they can proceed.

## Artifact path safety

Artifact records may reference URLs or local paths. Local hashing is intentionally constrained to repo/state/temp-safe locations; unsafe path traversal should be blocked by the registry and covered by tests.

## Public-use boundary

The public repo is suitable for local evaluation and development when the smoke/security checks pass. Production deployment remains an operator responsibility and should include auth, secrets management, observability, backups, and network hardening appropriate for the environment.
