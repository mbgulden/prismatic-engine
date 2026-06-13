# CF Pages API Auth — X-Auth-Key vs Bearer Token

**Updated June 11, 2026** — auth behavior is KEY-DEPENDENT, not just key-type-dependent. Some Global Keys have sufficient scope to work with Pages endpoints; some Bearer tokens don't.

## The Two-Key Rule

Keep both auth methods available:
- **Global Key (`cfk_`)** — works for wrangler Pages deploy when paired with `CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY` env vars. May work for REST API Pages endpoints depending on key scope.
- **API Token (Bearer, `cfut_`/`cfk_`)** — required for `/user/tokens/verify` and some `/accounts` endpoints. May FAIL for Pages operations if insufficiently scoped.

## Auth Matrix (varies by key scope — test both)

| Endpoint | X-Auth-Key (Global Key `cfk_`) | Bearer (API Token) |
|----------|-------------------------------|---------------------|
| `/user` | ✅ | ✅ |
| `/user/tokens/verify` | ❌ (needs Bearer) | ✅ |
| `/zones` | ✅ | ✅ (if zone-scoped) |
| `/accounts` | ✅ (some keys work) | ✅ (if account-scoped) |
| `/accounts/:id/pages/projects` (REST API) | ✅ (Global Key `cfk_` from GrowthWebDev worked Jun 2026) | ❌ (scoped `cfut_` Pages token returned 10000 auth error) |
| `npx wrangler pages deploy` | ✅ (with `CLOUDFLARE_API_KEY` + `CLOUDFLARE_EMAIL` env vars) | ❌ (`cfut_` token returned 9103 "Unknown X-Auth-Key"; `cfk_` Bearer returned 10000 auth error) |

## Diagnostic Flow

1. **Test Global Key first:** `curl -H "X-Auth-Email: $EMAIL" -H "X-Auth-Key: $KEY" https://api.cloudflare.com/client/v4/user` → if 200, key is valid
2. **Test Bearer token:** `curl -H "Authorization: Bearer $TOKEN" https://api.cloudflare.com/client/v4/user/tokens/verify` → if 200, token is valid
3. **If Bearer fails on Pages but Global Key works:** use Global Key + Email for wrangler: `CLOUDFLARE_API_KEY=$KEY CLOUDFLARE_EMAIL=$EMAIL npx wrangler pages deploy dist --project-name=X`
4. **If both fail on Pages:** the account may not have Workers & Pages activated (error 7003) or the key lacks Pages permissions

## Account IDs

| Account | ID |
|---------|-----|
| GrowthWebDev (`michael@growthwebdev.com`) | `196c1798da487413b0281ccc570f05a1` |
| Active Oahu (`michael@activeoahu.com`) | `3e13f120ec7532f0bc8ac0bc9bfc7108` |

The account ID in tunnel tokens may differ — always use the ID from `GET /accounts` for Pages API calls.

## Working Credential Pattern (GrowthWebDev, Jun 2026)

```bash
# Wrangler Pages deploy — works
CLOUDFLARE_API_KEY="$CLOUDFLARE_GROWTHWEB_API_KEY" \
CLOUDFLARE_EMAIL="$CLOUDFLARE_GROWTHWEB_EMAIL" \
npx wrangler pages deploy dist --project-name beyondsaas

# REST API Pages list — works
curl -H "X-Auth-Email: $CLOUDFLARE_GROWTHWEB_EMAIL" \
     -H "X-Auth-Key: $CLOUDFLARE_GROWTHWEB_API_KEY" \
     "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_PAGES_ACCOUNT_ID/pages/projects"
```

**Key insight:** The `cfut_` Pages-scoped token did NOT work for either wrangler or REST API (returned 10000 auth error). The `cfk_` Global Key with Email header worked for BOTH. This contradicts the earlier assumption that Global Keys always fail on Pages endpoints — it depends on the specific key's permissions.

## Test Procedure

Load `cf-credential-test-procedure` skill for the full test matrix and dead-key cleanup workflow.
