# Staging Access Control for CF Pages

## Problem

You need a staging/preview environment that's restricted but accessible to both
the developer (browser) and automation (API). The obvious path — Cloudflare
Access on the `.pages.dev` subdomain — has critical limitations.

## CF Access on .pages.dev — What Doesn't Work

| Method | Result |
|---|---|
| Email OTP (browser) | ✅ Works |
| Service token (header auth) | ❌ 302 redirect — ignored |
| IP bypass rule | ❌ 302 redirect — ignored |
| `allow_authenticate_via: { ip: true }` | ❌ No effect |
| `service_auth_401: true` | ❌ No effect |

**Root cause:** `.pages.dev` is Cloudflare infrastructure DNS. Access
intercepts (you get the 302 redirect) but only the browser-based email flow
works. Programmatic auth methods are silently dropped.

## Solution: Pages Functions Middleware

Use `functions/_middleware.js` — it runs AFTER CF's edge but BEFORE your
static content, and works on `.pages.dev` without any dashboard config.

### Pattern: Cookie + Header Dual Auth

```js
// functions/_middleware.js
const STAGING_KEY = "ohana-means-family";
const COOKIE_NAME = "staging_auth";
const COOKIE_MAX_AGE = 86400; // 24 hours

export async function onRequest(context) {
  const { request, next } = context;
  const url = new URL(request.url);
  
  // Only gate pages.dev (not custom domains)
  if (!url.hostname.includes('pages.dev')) return next();
  
  // Browser auth: ?key=ohana → set cookie → redirect
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
  
  // Cookie check
  const cookie = request.headers.get('Cookie') || '';
  if (cookie.includes(`${COOKIE_NAME}=1`)) return next();
  
  // API auth
  if (request.headers.get('X-Staging-Key') === STAGING_KEY) return next();
  
  return new Response('Access restricted. Visit ?key=ohana to unlock.', { 
    status: 401,
    headers: { 'Content-Type': 'text/plain' }
  });
}
```

### Usage

| Actor | How |
|---|---|
| **Browser** | Visit `https://staging.xxx.pages.dev/?key=ohana` — cookie set, auto-redirects. 24h session. |
| **API/curl** | `curl -H "X-Staging-Key: ohana-means-family" URL` |
| **Production** | Custom domain is NOT gated (hostname check skips middleware) |

### Why This Wins Over CF Access

- Works on `.pages.dev` subdomains (Access doesn't)
- No Zero Trust dashboard config needed
- Both browser and programmatic access work
- Production domain is untouched (middleware checks hostname)
- Zero cost, no account-level feature toggles
- Self-contained in the repo — moves with the project

## Prerequisites

- Pages Functions must be enabled (auto-detected when `functions/` dir exists)
- Project must have `functions/` at repo root (alongside `site/` or `dist/`)
- If `uses_functions: false` in API, adding `functions/` dir auto-enables it

## Pitfalls

- **Middleware runs AFTER CF Access** — if both are configured, Access
  intercepts first and your middleware never fires. If using middleware,
  DELETE the Access application entirely.
- **`destination_dir` doesn't affect Functions** — `functions/` always lives
  at the repo root, not inside the build output directory.
- **Cookie set on pages.dev only** — the `?key=ohana` URL sets a cookie
  scoped to the pages.dev hostname. Visiting the custom domain won't be gated.
