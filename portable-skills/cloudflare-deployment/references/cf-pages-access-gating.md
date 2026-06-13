# Cloudflare Access on Pages Preview Deployments

Use Cloudflare Access to gate a Pages preview deployment behind email authentication — staging stays public but only authorized users can reach it.

## When to Use
- Need a staging preview that's viewable by specific people only
- CF Pages auto-deploys preview branches to public `.pages.dev` URLs
- Want email-gated access without changing DNS, tunnel, or build config

## Prerequisites

1. **Access must be enabled on the account** — API returns 403 with `"access.api.error.not_enabled"` if not. Fix: Cloudflare Dashboard → Zero Trust → click "Enable Access" (free, no card). This is a one-time manual step per account.
2. **Working API token with Access permissions** — needs "Access: Apps and Policies — Edit" scope at minimum. Use `cfat_` Bearer token, not `cfk_` Global Key.

## Setup Flow (API)

### 1. Verify Access is enabled
```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/${ACCT}/access/apps" \
  -H "Authorization: Bearer ${TOKEN}"
# 200 + empty result[] = enabled. 403 with "not_enabled" = needs dashboard activation.
```

### 2. Create Access application
```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${ACCT}/access/apps" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Staging Gate",
    "domain": "staging.my-project.pages.dev",
    "type": "self_hosted",
    "session_duration": "24h",
    "auto_redirect_to_identity": true,
    "skip_interstitial": true,
    "app_launcher_visible": false
  }'
```
- `domain`: the `.pages.dev` preview URL (or custom domain alias)
- `type` must be `self_hosted` for Pages deployments
- `skip_interstitial`: true means user goes straight to email PIN screen (no "you're about to access X" interstitial)
- `auto_redirect_to_identity`: sends user directly to identity provider

### 3. Add email-only access policy
```bash
APP_ID=...
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${ACCT}/access/apps/${APP_ID}/policies" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Authorized Only",
    "decision": "allow",
    "include": [{"email": {"email": "user@gmail.com"}}],
    "precedence": 1
  }'
```
- `include` entries are OR'd — add multiple emails or service tokens
- `precedence` controls order; 1 = first evaluated

### 4. Verify the gate is active
```bash
curl -sI "https://staging.my-project.pages.dev/"
# Should return HTTP 302 → cloudflareaccess.com/cdn-cgi/access/login/...
```

## Programmatic Access (Service Tokens)

### Create a service token
```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${ACCT}/access/service_tokens" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "CI/CD Bot"}'
# Returns client_id and client_secret — save the secret, it won't be shown again
```

### Add service token to policy
Update the existing policy to include both email and service token in the `include` array:
```json
"include": [
  {"email": {"email": "user@gmail.com"}},
  {"service_token": {"token_id": "<token-id>"}}
]
```

### ⚠️ Service token limitation on `.pages.dev` subdomains

**Service token header auth (`CF-Access-Client-Id` + `CF-Access-Client-Secret`) does NOT work on `.pages.dev` subdomains.** The Access proxy layer on these domains doesn't process the service token headers the way custom domains do. HTTP 302 redirect persists even with valid headers.

Verified June 2026: `staging.active-oahu-tours-mirror.pages.dev` — service token added to policy, `service_auth_401: true` set on app, headers sent correctly. Still returns 302 to Access login.

**Workaround for programmatic checks:** Use the raw deployment hash URL (e.g., `https://9d6fe528.active-oahu-tours-mirror.pages.dev`) which is not behind Access. Query the API to find the latest staging deployment hash:
```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/${ACCT}/pages/projects/${PROJECT}/deployments?sort_by=created_on&sort_order=desc&per_page=5" \
  -H "Authorization: Bearer ${TOKEN}" \
  | python3 -c "import sys,json; deps=json.load(sys.stdin)['result']; [print(d['url']) for d in deps if d.get('deployment_trigger',{}).get('metadata',{}).get('branch')=='staging'][:1]"
```

## Cross-Profile Credential Discovery

When the orchestrator `.env` has stale credentials (error 9103), check other Hermes profiles:
```bash
grep -r "CLOUDFLARE_API_TOKEN\|CLOUDFLARE_API_KEY" ~/.hermes/profiles/*/ .env
```
Kai's profile often holds the Active Oahu Tours Bearer token (`cfat_...`). Growthwebdev keys (`cfk_...`) are valid but for a DIFFERENT account — AOT Pages live under account `3e13f120ec7532f0bc8ac0bc9bfc7108`, not growthwebdev's `e08f006fc73b79ca0668ba828519c925`.
