# Cloudflare Pages — Domain Management via API

## When to Use
- Adding a custom domain to a CF Pages project programmatically
- Migrating a domain from tunnel → Pages
- Wrangler CLI doesn't have `pages domain` commands (current versions)

## Auth Discovery

Wrangler uses either Global API Key or OAuth token. To determine which:

```bash
npx wrangler whoami
# Output: "You are logged in with an Global API Key, associated with the email Michael@growthwebdev.com."
```

### Global API Key Auth Pattern

Once confirmed as Global Key, use `X-Auth-Email` + `X-Auth-Key` headers (NOT Bearer):

```bash
curl -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
     -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
     -H "Content-Type: application/json" \
     "https://api.cloudflare.com/client/v4/user/tokens/verify"
```

Note: The `/user/tokens/verify` endpoint requires Bearer auth even for Global Key accounts and will reject X-Auth-Email/X-Auth-Key. Use the `/user` endpoint to verify Global Key credentials:

```bash
# Verify Global API Key — returns account identity
curl -s "https://api.cloudflare.com/client/v4/user" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"
# Response shows: email, account_id, organizations, permissions
```

The `/user` endpoint is preferred over listing zones because it returns the authenticated user's identity (email, org membership, permissions) — confirming the key works AND which account it belongs to in one call.

**⚠️ Pages API Auth (VERIFIED Jun 2026):** The Global Key (`cfk_` prefix, X-Auth-Key header) works for User, Zones, and DNS — but the Pages API (`/accounts/:id/pages/*`) exclusively requires **Bearer tokens**. Using X-Auth-Key for Pages returns 9106 "Authentication error". Create a Pages-scoped API token at https://dash.cloudflare.com/profile/api-tokens. See `references/cf-pages-api-auth-matrix.md` for the full auth matrix. For testing all CF credentials, load `cf-credential-test-procedure` skill.

### Credential Validation (CRITICAL — Do This First)

Stored credentials can be expired, revoked, or mislabeled. ALWAYS validate before use:

```bash
# For Global API Key (cfk_ prefix)
curl -s "https://api.cloudflare.com/client/v4/user" \
  -H "X-Auth-Email: $EMAIL" \
  -H "X-Auth-Key: $KEY"
# Success: {"success":true, "result":{"email":"...","id":"..."}}
# Failure (9103): "Unknown X-Auth-Key or X-Auth-Email" → key expired/wrong

# For API Token (cfat_ prefix)
curl -s "https://api.cloudflare.com/client/v4/zones" \
  -H "Authorization: Bearer $TOKEN"
# Success: returns zone list
# Failure (1000): "Invalid API Token" → token expired/revoked
```

**The `/user` endpoint is the only reliable test for Global Keys.**
- `cfk_5s...` returned 9103 → expired/revoked Global Key (Jun 2026 session)
- `cfat_sW...` worked as Bearer token → actual credential was API Token, not Global Key

### Nested .env Discovery

If a stored credential fails, check for nested `.env` files — Hermes profiles can have duplicates at different paths:
```bash
find ~/.hermes -name '.env' | xargs grep -l 'CLOUDFLARE'
# Found credential in ~/.hermes/profiles/orchestrator/home/.hermes/profiles/orchestrator/.env
# Not in the primary ~/.hermes/profiles/orchestrator/.env
```

## Adding a Custom Domain to Pages

### Step 1: Check Current DNS

```bash
# Get zone ID
curl -s "https://api.cloudflare.com/client/v4/zones?name=example.com" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"

# List DNS records
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=CNAME" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"
```

### Step 2: Add Domain to Pages Project

**⚠️ Auth split:** The Pages domain endpoint requires a **Bearer token** (not Global Key). Use your Pages API token (prefixed `cfut_`) with `Authorization: Bearer`. The Global Key (`cfk_`) works for zones and DNS but returns 9106 on Pages endpoints.

```bash
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains" \
  -H "Authorization: Bearer $PAGES_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"example.com"}'
```

Response: `{"result": {"status": "initializing", "verification_data": {"status": "pending"}}}`

### Step 3: Update DNS to Point to Pages

The Pages project's default domain is `$PROJECT_NAME.pages.dev`. Update DNS CNAME records:

```bash
# Update apex domain
curl -s -X PATCH \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"type":"CNAME","name":"example.com","content":"project.pages.dev","proxied":true}'

# Update www subdomain (same pattern, different record ID)
```

### Step 4: Trigger Re-Verification

If the domain was added BEFORE DNS was pointed to Pages, verification stalls at `pending`. The fix is to remove and re-add the domain (DNS already points to Pages on the second attempt, so verification passes). **This is the standard pattern** — add DNS first, then add domain to Pages. If you added domain first (status stuck at `pending`), delete + re-add:

```bash
# Delete
curl -s -X DELETE \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains/example.com" \
  -H "Authorization: Bearer $PAGES_API_TOKEN"

# Re-add (now DNS points to Pages)
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains" \
  -H "Authorization: Bearer $PAGES_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"example.com"}'
```

Verification proceeds: `verification_data.status` → `active` (15-30s), then `validation_data.status` → `active` (SSL cert, 20-60s), then overall `status` → `active`.

### Step 5: Poll Until Active

```bash
# Check status (poll every 10-15s, usually takes 30-60s)
curl -s \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains/example.com" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"
```

Status progression: `initializing` → `pending` → `active`
- `verification_data.status`: `pending` → `active` (domain ownership verified)
- `validation_data.status`: `initializing` → `pending` → `active` (SSL cert provisioned)
- Overall `status`: `initializing` → `pending` → `active`

Once `status=active`, the domain serves from Pages.

## Verification

```bash
# Confirm serving from Pages (not tunnel)
curl -sI "https://example.com/" | grep -E "server|cf-ray|content-type"
# Should show: server: cloudflare (Pages serves directly, no tunnel cf-ray hop needed)

# Check specific pages
curl -sI "https://example.com/new-page/"
```

## Pitfalls

- **Wrangler `pages domain` doesn't exist:** Current wrangler versions (4.98.0) have no domain management subcommands. Use the REST API.
- **Global Key ≠ Bearer token:** `CLOUDFLARE_API_KEY` starting with `cfk_` is the Global Key. Use `X-Auth-Key` header, NOT `Authorization: Bearer`.
- **Pages API requires Bearer token (NOT Global Key):** The Pages domain endpoints (`/pages/projects/.../domains`) exclusively require Bearer tokens (prefixed `cfut_` or `cfat_`). Using the Global Key with X-Auth-Key returns 9106. Zone/DNS endpoints accept Global Key. Use both — Bearer for Pages, Global Key for zones/DNS.
- **DNS must point to Pages BEFORE verification:** If the domain still points to a tunnel when added to Pages, HTTP validation fails. Update DNS FIRST or remove/re-add the domain after DNS change.
- **`cfut_` vs `cfk_` token scope split:** `cfut_` = Pages-specific token (works for `/pages/*` only). `cfk_` = Global Key (works for zones, DNS, Workers). Neither is a superset of the other — you need both for full domain setup.
- **Account ID discovery:** `npx wrangler whoami` shows the account ID. Or extract from zone details.
- **Zone ID discovery:** Query zones by domain name to get the zone ID for DNS management.
- **Don't delete tunnel DNS records permanently:** Keep the tunnel infrastructure running for other services. Only update the specific hostname records you're migrating to Pages.
- **Path-based routing alternative (Worker route):** When you need a Pages project accessible at a sub-path of an existing domain (e.g., `play.example.com/my-game/`), use a Worker route instead of a custom domain. The Worker proxies the path prefix to the Pages project and injects a `<base>` tag so relative assets resolve correctly. See `references/pages-worker-path-routing.md`.
- **Domain stuck at `pending` after DNS fixed:** Delete the domain from the Pages project and re-add it. The remove/re-add cycle triggers fresh verification against the now-correct DNS. Poll in 15s intervals — verification becomes `active` first, then SSL validation follows 20-60s later.
