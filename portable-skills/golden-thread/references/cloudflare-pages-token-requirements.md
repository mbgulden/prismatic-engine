# Cloudflare Pages API Token Requirements

## The 7003 Error: "Could not route"

When `wrangler pages deploy` or any CF Pages API call returns:

```
ERROR 7003: Could not route to /client/v4/accounts/ACCOUNT_ID/pages/projects/...
perhaps your object identifier is invalid?
```

**This is NOT a wrong account ID or project name.** The token is missing required permissions.

## Required Permissions

A CF Pages deploy token **must** have BOTH:

| Permission | Why |
|-----------|-----|
| **Account** → **Cloudflare Pages** → **Edit** | Required for deploy, project list, assets |
| **Account** → **Account Settings** → **Read** | Required for wrangler to route API calls to the correct account |

Without Account Settings:Read, wrangler can't discover accounts. With only Pages:Edit, the token can't list accounts (`wrangler pages project list` returns "Failed to automatically retrieve account IDs"). With only Account Settings:Read, the token can verify but has zero permissions for Pages operations.

## Verification

```bash
curl -s "https://api.cloudflare.com/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['status'])"
```

Returns `active` if the token is valid. Check permissions in the Cloudflare Dashboard under the token's settings.

## Token Creation Steps

1. https://dash.cloudflare.com/profile/api-tokens → **Create Token** → **Custom Token**
2. **Account Resources:** Select the correct account
3. **Permissions:**
   - Account → Cloudflare Pages → Edit
   - Account → Account Settings → Read
4. Copy token (starts with `cfut_` for API token, or `cfat_` for older tokens)

## Setting Up

```bash
export CLOUDFLARE_API_TOKEN=cfut_YOUR_TOKEN_HERE
export CLOUDFLARE_ACCOUNT_ID=YOUR_ACCOUNT_ID_HERE
```

Then deploy:
```bash
npx wrangler pages deploy site --project-name PROJECT_NAME --branch main --commit-dirty=true
```

## GitHub Auto-Deploy Alternative

If CF Pages is connected to the GitHub repo (Dashboard → Workers & Pages → Project → Settings → Builds & deployments → Connect Git repository), every push to the configured branch auto-deploys — no token needed for deployments.
