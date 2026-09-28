# Pages + Worker Path-Based Routing

Serve a Cloudflare Pages project at a sub-path of another domain using a Worker route.

## When to Use
- You have an existing domain serving content (e.g., `play.example.com` → Pages project A)
- You want a second Pages project at a sub-path (e.g., `play.example.com/my-game/` → Pages project B)
- You can't use a separate subdomain (e.g., `my-game.play.example.com`) or need both

## Architecture
```
Browser → play.example.com/darius-star/
           ↓
    Worker Route (darius-star-router)
    pattern: play.example.com/darius-star*
           ↓
    Worker: strip prefix, fetch darius-star.pages.dev
    inject <base href="/darius-star/"> into HTML
           ↓
    darius-star.pages.dev (Pages project B)
```

For non-matching paths (`play.example.com/other`), the Worker fetches from the main Pages project (`whatanadventure-games.pages.dev`).

## Worker Script (Service Worker format)

```javascript
// Deploy via REST API: PUT /accounts/:id/workers/scripts/:name
// CRITICAL: Must be Service Worker format (addEventListener), not ES module (export default)
addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request));
});

async function handleRequest(request) {
  const url = new URL(request.url);

  // Handle /darius-star/* → proxy to darius-star Pages project
  if (url.pathname.startsWith('/darius-star')) {
    const targetPath = url.pathname.replace('/darius-star', '') || '/';
    const targetUrl = `https://darius-star.pages.dev${targetPath}${url.search}`;

    const response = await fetch(targetUrl, {
      method: request.method,
      headers: request.headers,
      redirect: 'follow',
    });

    // For HTML, inject <base> so relative asset URLs resolve to /darius-star/
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('text/html')) {
      let html = await response.text();
      html = html.replace('<head>', '<head>\n<base href="/darius-star/">');
      return new Response(html, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    }

    return response;
  }

  // Pass-through: fetch from the main Pages project
  const passThroughUrl = new URL(request.url);
  passThroughUrl.hostname = 'main-project.pages.dev';
  return fetch(new Request(passThroughUrl, request));
}
```

## Deploy Steps

1. **Deploy the Worker script** (Service Worker format, NOT ES module):
```bash
curl -s -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/scripts/$WORKER_NAME" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/javascript" \
  --data-binary @worker-script.js
```

2. **Add Worker route on the zone**:
```bash
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/workers/routes" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"pattern":"play.example.com/my-game*","script":"my-game-router"}'
```

3. **Verify**: `curl -sI "https://play.example.com/my-game/"` → HTTP 200 with correct content.

## Pitfalls

- **Worker format**: The REST API PUT endpoint only accepts Service Worker format (`addEventListener('fetch', ...)`). Using ES module format (`export default { async fetch(...) }`) returns error 10021 "Unexpected token 'export'". The REST API does NOT support `Content-Type: application/javascript+module`.
- **Route already exists**: If a route pattern already exists for the zone, you can't create a duplicate. Update the existing worker script instead. Use `GET /zones/:id/workers/routes` to list existing routes.
- **<base> tag injection**: Without `<base href="/sub-path/">`, relative asset URLs (`assets/sprites/boss.png`) resolve to the root domain, not the sub-path. This breaks games and SPAs with relative asset references.
- **Pass-through to Pages**: When the Worker doesn't match a path, it must explicitly fetch from the main Pages project's `.pages.dev` URL — calling `fetch(request)` on the original URL would loop back into the Worker.
- **Auth split**: Zone/DNS/Worker endpoints use Global Key (`cfk_` + `X-Auth-Key`). Pages domain endpoints use Bearer token (`cfut_` + `Authorization: Bearer`). You need both.
