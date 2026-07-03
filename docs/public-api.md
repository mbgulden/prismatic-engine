# Prismatic Engine Public API

The public API is a FastAPI gateway for controlled agent dispatch and credit
policy checks. It is separate from the internal gateway under
`prismatic.gateway.server`.

## Run

```bash
python -m prismatic.api.main --host 127.0.0.1 --port 8000
```

For development with the optional gateway extras installed:

```bash
pip install -e '.[gateway]'
```

## Authentication

All stateful endpoints use Bearer token authentication. Configure one of:

- `PRISMATIC_API_KEY` for a single admin token
- `PRISMATIC_API_KEYS` for comma-separated tokens, optionally with one scope suffix per entry

Example request shape:

```bash
curl http://127.0.0.1:8000/api/v1/health \
  -H @headers.txt
```

where `headers.txt` contains the `Authorization` bearer header for the
configured API key.

The API fails closed when no API key is configured. For local development only,
set `PRISMATIC_API_ALLOW_DEV_TOKEN=1` to allow the known `prismatic-dev-token`
fallback. Do not expose a gateway using the development fallback publicly.

## Agent dispatch allowlist

`POST /api/v1/jobs` does not expose every internal dispatcher launcher by
default. It only permits the public allowlist:

```text
fred,kai,agy,jules,codex
```

Set `PRISMATIC_API_ALLOWED_AGENTS` to a comma-separated list to narrow or extend
that public API allowlist deliberately.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/` | Public API metadata |
| `GET` | `/api/v1/health` | Authenticated health check |
| `GET` | `/api/v1/credits` | Credit policy status sample |
| `GET` | `/api/v1/jobs` | List submitted in-memory jobs |
| `POST` | `/api/v1/jobs` | Queue a dispatch through the public agent allowlist |
| `GET` | `/api/v1/jobs/{job_id}` | Read queued job status |

The job store is intentionally in-memory for the MVP. A production deployment
should replace it with a durable queue before relying on restarts or horizontal
scaling.
