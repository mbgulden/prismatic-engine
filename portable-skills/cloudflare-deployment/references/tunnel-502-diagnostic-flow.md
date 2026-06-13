# Diagnosing 502 on a Tunneled Domain

## Quick Diagnostic Flow

When a Cloudflare-tunneled domain returns 502, work through these four checks in order:

### 1. Test the service directly
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:PORT/
```
**200 = service healthy. Not 200 = fix the service first.**

### 2. Test through a quick tunnel
```bash
cloudflared tunnel --url http://localhost:PORT 2>&1 | tee /tmp/cf-quick.log
# Grab the trycloudflare.com URL and test it
curl -sI https://random.trycloudflare.com/
```
**200 = tunnel + service both work. The domain issue is external.**

### 3. Test the production domain
```bash
curl -sI https://domain.com/
```
Look at the response:
- **HTTP 302 + `location: cloudflareaccess.com/cdn-cgi/access/login/...`** → Cloudflare Access is intercepting. The 502 is from the Access application's backend, NOT the tunnel. Check Zero Trust → Access → Applications.
- **HTTP 502 + `server: cloudflare`** with no Access redirect → tunnel or origin issue. Check ingress config.
- **HTTP 1033** → hostname not in tunnel ingress. Add in dashboard.

### 4. Check response headers
```bash
curl -sI https://domain.com/ | grep -i "www-authenticate\|server\|cf-ray"
```
- `www-authenticate: Cloudflare-Access` → **Access application problem**, not tunnel
- `server: cloudflare` + 502 → tunnel reached edge but origin failed
- `cf-ray: ...-SEA` → shows which Cloudflare edge location handled the request

## The Access-Tunnel Confusion

**Access applications have their OWN backend URL** — separate from tunnel ingress. When Access is enabled:

```
Browser → CF Edge → Access Login → (authenticated) → Access Backend URL → ???
                                                                    ↑ 502 here
```

The Access backend URL is configured in **Zero Trust → Access → Applications → [app] → Settings**, not in the tunnel ingress. If it points to a wrong port or unreachable host, you get a 502 — even though the tunnel ingress is perfectly correct.

**Fix options:**
1. Correct the Access application's backend URL to match the tunnel ingress service
2. Delete the Access application entirely (the tunnel handles routing without it)

## Verified Example (hermes.growthwebdev.com, June 2026)

- Tunnel ingress: `hermes.growthwebdev.com → http://localhost:9119`, `noTLSVerify: true` ✅
- Gateway: HTTP 200 on localhost:9119 ✅
- Quick tunnel: HTTP 200 ✅
- Domain: HTTP 302 → `cloudflareaccess.com/cdn-cgi/access/login/...` ❌
- Header: `www-authenticate: Cloudflare-Access` ❌

Root cause: Cloudflare Access application intercepting before the tunnel. Tunnel and gateway both healthy.
Fix: Correct the Access application's backend URL or remove the Access app.
