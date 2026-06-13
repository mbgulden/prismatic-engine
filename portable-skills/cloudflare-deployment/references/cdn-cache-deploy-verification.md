# CDN Cache → Deploy Verification

When a Cloudflare Pages deployment succeeds but the custom domain still shows old content, the CDN edge cache is the culprit — not the deployment.

## Symptoms

- CF Pages API shows deployment `stage=success` on the correct commit
- Custom domain (e.g., `activeoahutours.com`) returns old HTML
- `cf-cache-status: HIT` with `age: 133274` (37+ hours stale)
- `?nocache=` query param returns fresh content while bare URL returns stale

## Root Cause

Cloudflare's CDN caches HTML pages at the edge. A new Pages deployment updates the origin, but edge caches worldwide may serve the stale version for hours. The `_headers` file may set `Cache-Control: public, max-age=3600` on `*.html`, but edge nodes can hold longer.

## Fix: Verify via Direct Deployment Hash URL

Every CF Pages deployment gets a unique hash URL that bypasses custom-domain CDN caching:

```
https://<hash>.active-oahu-tours-mirror.pages.dev
```

**This is the single source of truth for what's actually deployed.** Always verify here before concluding a deploy failed:

```bash
# Get the hash URL from the API
curl -s -H "Authorization: Bearer $CF_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$ACCT/pages/projects/$PROJECT/deployments?environment=production&per_page=1" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['url'])"

# Then screenshot or curl that URL directly
curl -s "https://<hash>.active-oahu-tours-mirror.pages.dev/" | grep 'expected-content'
```

## Cache Purge

API token needs zone-level permissions (not just Pages). If token is Pages-only, a dummy trigger commit forces a fresh deploy which busts the cache:

```bash
git commit --allow-empty -m "chore: trigger deploy to bust CDN cache" && git push origin main
```

## Pitfall: The Cache May Be the Correct Version

**IMPORTANT:** A CDN cache HIT on production may be the USER'S INTENDED STATE. If the user says the site looked right before, the cached version might be the one they want. Don't treat every cache HIT as a bug to fix — ask first.
