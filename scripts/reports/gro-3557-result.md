# GRO-3557 Result — Dynamic client IP whitelisting

## Summary

Implemented process-local dynamic IP registration for the Prismatic Gateway.

## Changed paths

- `prismatic/gateway/security.py` — IP/CIDR parsing, trusted-proxy client-IP resolution, process-local runtime allowlist, and one-time secret consumption.
- `prismatic/gateway/server.py` — `POST /api/gateway/auth/ip-whitelist` endpoint plus middleware that blocks non-allowlisted `/api/gateway/*` callers while exempting the registration endpoint.
- `prismatic/test_gateway_ip_whitelist.py` — focused regression coverage for blocked API calls, dynamic registration bypass, one-time secret replay rejection, and trusted-proxy header handling.
- `README.md` — operator documentation for `PRISMATIC_ALLOWED_IPS`, `PRISMATIC_TRUSTED_PROXIES`, and `PRISMATIC_IP_WHITELIST_SECRET`.

## Verification

```bash
python3 -m pytest prismatic/test_gateway_ip_whitelist.py -q
# 3 passed, 5 warnings in 0.54s
```

Warnings are existing FastAPI/TestClient deprecations; no test failures.

## Operational notes

- Empty `PRISMATIC_ALLOWED_IPS` preserves current open/internal gateway behavior.
- Static entries can be IPs or CIDRs.
- Runtime registrations are intentionally in-memory and disappear on gateway restart.
- Forwarded headers are honored only when the immediate peer is in `PRISMATIC_TRUSTED_PROXIES`.
- The one-time registration secret can come from `PRISMATIC_IP_WHITELIST_SECRET` or comma-separated `PRISMATIC_IP_WHITELIST_SECRETS`.
