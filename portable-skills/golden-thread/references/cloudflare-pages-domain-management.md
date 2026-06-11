# Cloudflare Pages Custom Domain Management via API

## Auth: Global Key, NOT API Token

The env var `CLOUDFLARE_API_KEY` (prefix `cfk_`) is a **Global API Key**, not a scoped API token. It uses `X-Auth-Email` + `X-Auth-Key` headers — NOT `Authorization: Bearer`.

```bash
# Correct auth pattern for Global Key:
curl -s "https://api.cloudflare.com/client/v4/..." \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json"
```

**Pitfall:** `curl -H "Authorization: Bearer $CLOUDFLARE_API_KEY"` will fail with `6003: Invalid format for Authorization header`. The token verification endpoint (`/user/tokens/verify`) requires Bearer auth — it will reject Global Keys. Test auth by querying zones instead.

**Account ID:** `196c1798da487413b0281ccc570f05a1` (Michael's account)

## Workflow: Add Custom Domain to Pages Project

### Step 1: Verify Auth + Find Zone
```bash
curl -s "https://api.cloudflare.com/client/v4/zones?name=DOMAIN.com" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"
# Extract zone ID from result[0].id
```

### Step 2: Check Current DNS Records
```bash
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=CNAME" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"
```

### Step 3: Add Domain to Pages Project
```bash
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"DOMAIN.com"}'
# Returns: status=initializing, verification=pending
```

### Step 4: Update DNS to Point to Pages
Before verification can succeed, DNS must point to `PROJECT_NAME.pages.dev`:

```bash
# Get existing CNAME record ID
RECORD_ID=$(curl -s \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?name=DOMAIN.com&type=CNAME" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['result'][0]['id'])")

# Update to point to Pages
curl -s -X PATCH \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"type":"CNAME","name":"DOMAIN.com","content":"PROJECT_NAME.pages.dev","proxied":true}'

# Repeat for www.DOMAIN.com
```

### Step 5: Delete + Re-add Domain to Trigger Verification
The initial domain add happened while DNS still pointed to the tunnel. Verification will fail until DNS is updated AND the domain is re-added:

```bash
# Delete the pending domain
curl -s -X DELETE \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains/DOMAIN.com" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"

sleep 3

# Re-add domain (now with DNS pointing to Pages)
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"DOMAIN.com"}'
```

### Step 6: Poll Until Active
```bash
# Status transitions: initializing → pending → active
# Check every 10-15 seconds (typically takes 30-60 seconds total)
curl -s \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT_NAME/domains/DOMAIN.com" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" | python3 -c "
import json,sys
d=json.load(sys.stdin)
r=d.get('result',{})
print(f\"status={r.get('status')} verify={r.get('verification_data',{}).get('status')} valid={r.get('validation_data',{}).get('status')}\")"
# Wait for: status=active verify=active valid=active
```

### Step 7: Verify Site Serves from Pages
```bash
curl -sI "https://DOMAIN.com/" 2>&1 | head -5
# Look for Astro build markers or new content to confirm Pages is serving
```

## Wrangler CLI (Alternative for Deploy-Only)
Wrangler CLI is authenticated via Global Key (check with `npx wrangler whoami`). Use for:
- `wrangler pages project create NAME` — create project
- `wrangler pages deploy dist/ --project-name=NAME --commit-dirty=true` — deploy assets

Wrangler does NOT have domain management subcommands in v4.x. Use API for domains.

## Preview URL Validation
Newly deployed preview URLs (`*.pages.dev`) may fail SSL handshake (`sslv3 alert handshake failure`) for 30-60 seconds while Cloudflare provisions the certificate. Custom domains provision faster after DNS propagation.

## Known Pitfalls
- **DNS not pointing to Pages:** Domain verification will stay "pending" forever if DNS still points to a tunnel. Update DNS FIRST, then re-add domain.
- **CLOUDFLARE_API_KEY is Global Key:** Do not use it as a Bearer token. Use X-Auth-Email + X-Auth-Key.
- **rclone may not be installed:** If `rclone` is missing from a fresh environment, install with `sudo apt-get install -y rclone`. Previous deployments may have used rclone for CF Pages upload but wrangler is the standard method.
