# Cloudflare Pages via REST API

When `wrangler` CLI fails (permission errors, version conflicts, or API routing issues), deploy CF Pages via the REST API directly.

## Quick Deploy Pattern

```bash
export CLOUDFLARE_API_TOKEN=cfat_...
export CF_ACCT=3e13f120ec7532f0bc8ac0bc9bfc7108
```

### 1. Create Project

```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${CF_ACCT}/pages/projects" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project", "production_branch": "main"}'
```

Returns the `.pages.dev` subdomain.

### 2. Connect GitHub

```bash
curl -s -X PATCH "https://api.cloudflare.com/client/v4/accounts/${CF_ACCT}/pages/projects/my-project" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "github",
      "config": {
        "owner": "mbgulden",
        "repo_name": "my-repo",
        "production_branch": "main",
        "deployments_enabled": true
      }
    }
  }'
```

Note: May require the user to authorize GitHub OAuth in the CF dashboard. If the API call succeeds silently but no deployment triggers, the user needs to do the initial GitHub connection in the dashboard.

### 3. Check Deployments

```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/${CF_ACCT}/pages/projects/my-project/deployments" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
```

Returns deployment list with `url`, `latest_stage.name`, and commit info.

## Diagnostic Commands

Quick verification of account, token, and API health before attempting deployment.

```bash
# 1. Verify API token is valid
curl -s https://api.cloudflare.com/client/v4/user/tokens/verify \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" | python3 -m json.tool

# 2. List accounts this token/key can access
curl -s https://api.cloudflare.com/client/v4/accounts \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" | python3 -m json.tool

# For Global API Key (cfk_...):
curl -s https://api.cloudflare.com/client/v4/accounts \
  -H "X-Auth-Email: user@example.com" \
  -H "X-Auth-Key: cfk_..." | python3 -m json.tool

# 3. List zones (confirms DNS access works)
curl -s https://api.cloudflare.com/client/v4/zones \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" | python3 -m json.tool

# 4. Test Pages API — the critical check
curl -s "https://api.cloudflare.com/client/v4/accounts/${CF_ACCT}/pages/projects" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" | python3 -m json.tool

# 5. wrangler whoami (confirms wrangler sees the account)
npx wrangler@3 whoami
```

**Decision tree when Pages API fails with 7003:**
1. Try with Global API Key (`cfk_...`) — if it also fails, Workers & Pages is not activated → user must enable in dashboard
2. If Global Key works but token fails → token permissions issue → add "Cloudflare Pages — Read" + "Edit"
3. If token was working before and is now rejected → token was rotated when permissions were edited → copy new token

- **25MB File Size Limit:** CF Pages hard-rejects any file over 25 MiB. Build fails with `Error: Pages only supports files up to 25 MiB in size` and lists the offending file. High-res photography (DSLR, NAS uploads) routinely exceeds this. Fix: compress all images >20MB with Pillow `save(optimize=True, quality=80)` and resize to max 2000px wide. See `references/static-mirror-lift-and-shift.md` step 6 for the compression script. After pushing the fix, the GitHub webhook auto-triggers a new deployment.

- **Build failure debugging via logs:** When a deployment fails, pull the build logs to see the exact error:
  ```
  DEPLOY_ID=$(curl -s .../deployments | python3 -c "import json,sys; print(json.load(sys.stdin)['result'][0]['id'])")
  curl -s ".../deployments/${DEPLOY_ID}/history/logs" \
    -H "Authorization: Bearer ${TOKEN}"
  ```
  Logs are structured as `[{ts: "...", line: "..."}]`. Check for the specific file that violated the size limit or the error message from the build step.

- **Code 7003 "Could not route" = Workers & Pages not activated (CRITICAL):** When BOTH wrangler AND the REST API return `code: 7003` "Could not route to /client/v4/accounts/.../pages/..." — even with a valid Global API Key — the Workers & Pages product is NOT enabled on this Cloudflare account. It is NOT a permissions issue, NOT a wrangler bug, and NOT a wrong account ID. The account may have DNS/zones working fine but lacks the serverless compute platform. **Fix:** Go to Cloudflare Dashboard → Workers & Pages → follow the "Get started" / activation flow. Once activated, both wrangler and REST API work immediately. **Diagnostic:** If `GET /client/v4/accounts` returns the account but `GET .../pages/projects` returns 7003 with BOTH an API token AND a Global API Key (`cfk_...`), it's a product activation issue — stop debugging permissions.
- **API Token Permissions:** The token needs BOTH "Cloudflare Pages — Read" AND "Cloudflare Pages — Edit". "Account Settings — Read" is also needed for `wrangler whoami`. If the Pages API returns 7003 with an API token but NOT with a Global API Key, it's a token permissions issue. If it fails with BOTH, it's product activation (see above).
- **Editing API token permissions may rotate the token secret:** When you modify an API token's permissions in the dashboard, Cloudflare may invalidate the old token string and issue a new one. If a previously-working token suddenly returns "Invalid API Token" after a permissions edit, go back to the dashboard and copy the new token value.
- **Global API Key vs API Token:** When token scoping is ambiguous, use the Global API Key (`cfk_...`) as a diagnostic tool — it has full account access and bypasses all permission scoping. Header format: `X-Auth-Email: <email>` + `X-Auth-Key: <key>`. NOT `Authorization: Bearer`. If the Global Key also fails with 7003, the problem is product activation, not permissions. Global Keys are found at dash.cloudflare.com/profile/api-tokens (scroll past the API tokens table).
- **Account ID:** Always verify with `GET /client/v4/accounts` before using. The account ID appears in the Cloudflare dashboard URL: `dash.cloudflare.com/<ACCOUNT_ID>/...`.
- **GitHub OAuth:** The initial GitHub connection often requires dashboard authorization. After the first connection, subsequent pushes auto-deploy.
- **Build config:** Set via API or dashboard. For static HTML sites: `build_command: ""`, `destination_dir: "/"`, `root_dir: "/"`.

- **Dashboard shows project but API returns 7003 (IMPORTANT):** The dashboard may display Workers & Pages with a connected Git repo while the REST API is still blocked with 7003. This happens on older accounts (created 2022 or earlier) where Workers & Pages was never fully activated — the dashboard UI is partially available but API endpoints are unprovisioned. DO NOT keep trying API calls — use the dashboard directly. If Git is already connected: Deployments tab → find latest deployment → ⋮ → Retry deployment. This bypasses the API and pulls the latest commit. Also verify Settings → Builds & deployments → Build output directory (e.g., `site` for repos where content lives in a subdirectory). If pages are returning 200 but showing old content (old title tags, old canonical URLs), the deployment is stale — retry or trigger a new build from the dashboard, not the API.

- **Git-connected auto-deploy bypasses API entirely:** When the dashboard shows Git is connected, every push to the configured branch triggers a deployment automatically. No API access is needed. The API is only needed for the initial project creation and GitHub connection; after that, all deployments happen via the Git webhook. If you're troubleshooting deployment issues and the API is blocked with 7003, skip the API entirely — check the Deployments tab, verify the build output directory, and manually retry the deployment.
