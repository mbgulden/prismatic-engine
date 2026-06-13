# Staging Gate Pattern for CF Pages

When staging previews need access control and the site is on a `.pages.dev` subdomain.

## The Problem

Cloudflare Access (Zero Trust) intercepts traffic on `.pages.dev` preview deployments but has critical limitations:
- **IP bypass rules don't work** — policy `include: [{ip: {ip: "x.x.x.x"}}]` is ignored
- **Service token auth doesn't work** — `CF-Access-Client-Id` + `CF-Access-Client-Secret` headers are not honored
- **Only email-based auth works** — requires browser interaction and PIN retrieval

This makes programmatic access (curl, CI, agent verification) impossible through Access on pages.dev domains.

## The Solution: Pages Functions Middleware

A `functions/_middleware.js` at the repo root provides header + cookie-based auth that works on any deployment:

```javascript
// _middleware.js — staging gate
const STAGING_KEY = "ohana-means-family";
const COOKIE_NAME = "staging_auth";
const COOKIE_MAX_AGE = 86400; // 24 hours

export async function onRequest(context) {
  const { request, next } = context;
  const url = new URL(request.url);
  
  // Only gate pages.dev deployments (not custom domains like production)
  if (!url.hostname.includes('pages.dev')) return next();
  
  // ?key=ohana → set cookie, redirect to clean URL (browser access)
  if (url.searchParams.get('key') === 'ohana') {
    url.search = '';
    return new Response(null, {
      status: 302,
      headers: {
        'Location': url.toString(),
        'Set-Cookie': `${COOKIE_NAME}=1; Max-Age=${COOKIE_MAX_AGE}; Path=/; SameSite=Lax`
      }
    });
  }
  
  // Cookie auth (persists across browser sessions)
  const cookie = request.headers.get('Cookie') || '';
  if (cookie.includes(`${COOKIE_NAME}=1`)) return next();
  
  // Header auth (for curl/programmatic access)
  if (request.headers.get('X-Staging-Key') === STAGING_KEY) return next();
  
  return new Response('Access restricted. Visit ?key=ohana to unlock.', { 
    status: 401,
    headers: { 'Content-Type': 'text/plain' }
  });
}
```

## Usage

| Actor | Method |
|---|---|
| Human (browser) | Visit `https://staging.example.pages.dev/?key=ohana` — cookie set for 24h |
| Agent (curl) | `curl -H "X-Staging-Key: ohana-means-family" https://staging.example.pages.dev/` |
| Public | Gets HTTP 401 |

## Key Properties

- **Zero-cost** — no Cloudflare Access/Zero Trust needed
- **Pages-native** — runs on the CF Workers runtime, no external dependencies
- **Production-safe** — middleware no-ops on custom domains (`.pages.dev` check)
- **SEO-safe** — `.pages.dev` deployments already have `x-robots-tag: noindex`

## Pitfalls

- **Access runs BEFORE Functions** — if you have both an Access app and this middleware, Access will intercept first (HTTP 302 to login) and the middleware never executes. Delete the Access app if using this pattern.
- **Project needs `functions/` at repo root** — not inside `site/`. CF Pages auto-detects the directory.
- **Purge middleware when pushing to production** — if the staging branch uses this middleware and you merge to main, the `functions/` directory won't harm production (the `.pages.dev` check no-ops it), but keeping staging-specific code on main is messy. Reset staging to main after production merges.
- **Local Node lint may fail** — `node --check` can abort on CF Workers syntax. Ignore; the runtime handles it correctly.

## Why Not Access?

| Feature | Cloudflare Access | Pages Middleware |
|---|---|---|
| Email auth | ✅ | ❌ |
| IP bypass | ❌ on .pages.dev | ❌ (could be added) |
| Header auth | ❌ on .pages.dev | ✅ |
| Cookie/session | ✅ (24h session) | ✅ (24h cookie) |
| Setup complexity | Dashboard + API | One JS file |
| Cost | Free (Zero Trust) | Free (Functions) |
