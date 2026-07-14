# Prismatic Dashboard Routing Contract

Updated: 2026-07-14

## Domain ownership

| Hostname | Purpose | Source of truth |
|---|---|---|
| `prismaticengine.com` / `www.prismaticengine.com` | Public marketing site | Separate marketing/site repo and deploy surface, currently `/home/ubuntu/work/prismatic-engine-site` locally |
| `prismatic.growthwebdev.com` | Protected Prismatic governance/control-plane gateway | `prismatic-engine` gateway service on port `9000` behind Cloudflare Access |

## Governance dashboard route

The governance dashboard is **not** the public marketing `index.html` and is **not** a Hermes plugin dashboard.

Canonical dashboard template:

```text
prismatic/gateway/templates/dashboard.html
```

The gateway must serve this same canonical governance dashboard from both:

```text
GET /
GET /dashboard
```

This keeps `prismatic.growthwebdev.com` governance-only and prevents the public marketing surface from being accidentally served behind the protected operator hostname.

## Marketing route

The marketing `index.html` belongs on:

```text
https://prismaticengine.com/
https://www.prismaticengine.com/
```

Do **not** use `prismatic-engine/index.html` as the gateway root for `prismatic.growthwebdev.com`.

If marketing needs to change, update/deploy the separate marketing site surface instead of patching `prismatic/gateway/server.py`.

## Anti-regression checks

A focused verifier should assert:

- `GET http://127.0.0.1:9000/` returns dashboard markers such as `Ingestion Queue`, `Merge Pipeline`, `Workspaces`, `Skills`, and `Plugins`.
- `GET http://127.0.0.1:9000/dashboard` returns the same governance dashboard markers.
- Neither gateway route returns the marketing marker `One Engine. Full Spectrum Autonomy`.
- `https://prismaticengine.com/` returns the marketing marker `One Engine. Full Spectrum Autonomy`.
- `https://prismatic.growthwebdev.com/dashboard` remains Cloudflare Access protected unless a narrow verifier-IP bypass is intentionally active.

## Related implementation

- Gateway route handlers: `prismatic/gateway/server.py`
- Canonical governance dashboard: `prismatic/gateway/templates/dashboard.html`
- Public marketing local checkout: `/home/ubuntu/work/prismatic-engine-site`
