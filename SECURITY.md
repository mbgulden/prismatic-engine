# Security policy

Prismatic Engine is an alpha-stage local-first agent orchestration project. Please treat security issues seriously and avoid public disclosure until a fix is available.

## Supported versions

| Version | Supported |
|---|---:|
| `main` | ✅ best-effort |
| tagged releases | ✅ when published |
| old untagged commits | ❌ |

## Reporting a vulnerability

Please report privately to the repository owner/maintainers. Do not include real credentials in reports, screenshots, logs, fixtures, or tests.

Include:

- affected commit/version
- affected component
- reproduction steps
- expected vs actual behavior
- impact assessment
- proposed fix if known

## Secret handling rules

Run the public security readiness audit before publishing a branch or release:

```bash
python scripts/public_security_readiness_audit.py
```

Expected marker:

```text
PUBLIC_SECURITY_READINESS_OK
```

- Never commit `.env`, credential files, provider responses containing credentials, or private keys.
- Keep public examples free of credential material.
- Prefer environment variables or a secret manager for live integrations.
- Policy results, job inputs, artifact metadata, provenance, manifests, docs, and tests must not store credential values.

## High-risk surfaces

Extra review is required for:

- plugin loading/import behavior
- filesystem reads/writes
- artifact export/publish actions
- webhook/auth logic
- dashboard/API endpoints that mutate durable state
- external-service connectors
- destructive, costly, public-facing, or production-affecting actions
