# API Key Management Gauntlet

A 9-test sequence that validates API key management end-to-end. Use this as a template for any API that needs key registration, auth, and revocation.

## Test Sequence

```python
# 1. REGISTER: Create a new key
POST /v1/keys/register {"email": "...", "name": "...", "tier": "free"}
# Expect: 201, api_key returned, tier+rate_limit correct

# 2. STATUS: Check the key
GET /v1/keys/status (X-API-Key header)
# Expect: 200, tier/rate_limit/usage_count match

# 3. AUTH USE: Hit a protected endpoint with the key
POST /v1/bodygraph (X-API-Key header)
# Expect: 200, valid response body

# 4. NOAUTH: Hit the noauth variant (if exists)
POST /v1/bodygraph/noauth
# Expect: 200, same valid response (no key needed)

# 5. MISSING KEY: Hit protected endpoint without key
POST /v1/bodygraph (no X-API-Key header)
# Expect: 401, "Missing X-API-Key header"

# 6. REVOKE: Revoke the key
DELETE /v1/keys/revoke (X-API-Key header)
# Expect: 200, "API key revoked successfully"

# 7. REVOKED STATUS: Check the revoked key
GET /v1/keys/status (X-API-Key header — same key)
# Expect: 200, success=false, "API key not found"

# 8. BAD TIER: Register with invalid tier
POST /v1/keys/register {"email": "...", "tier": "platinum"}
# Expect: 422, validation error

# 9. PRO TIER: Register with higher tier
POST /v1/keys/register {"email": "...", "name": "...", "tier": "pro"}
# Expect: 201, tier=pro, rate_limit=1000
```

## What Each Test Validates

| # | Test | Validates |
|---|------|-----------|
| 1 | Register | Key creation, tier parsing, rate limit assignment |
| 2 | Status | Key lookup, metadata retrieval |
| 3 | Auth use | Middleware integration, auth + response pipeline |
| 4 | Noauth | Unauthenticated path still works |
| 5 | Missing key | Auth middleware rejects missing headers |
| 6 | Revoke | Key deletion works |
| 7 | Revoked status | Revoked keys are truly gone |
| 8 | Bad tier | Input validation on tier field |
| 9 | Pro tier | Multiple tiers work correctly |

## Tier Configuration Template

```python
TIER_CONFIG = {
    "free": {"rate_limit": 100, "description": "100 requests/minute"},
    "pro": {"rate_limit": 1000, "description": "1,000 requests/minute"},
    "enterprise": {"rate_limit": 10000, "description": "10,000 requests/minute"},
}
```

## Pitfalls

- **Terminal truncation**: Shell variables truncate long API keys. Use Python `urllib.request` directly (not curl → shell variable → curl) for multi-step tests that reuse the key.
- **Auth middleware must exist first**: The gauntlet assumes auth middleware is already wired. Test the middleware separately (missing key → 401) before testing key registration.
- **Revoke before re-registering**: Each test run should use a fresh key. Revoke at the end to avoid polluting the key store.
