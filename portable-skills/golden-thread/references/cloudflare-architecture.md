# Cloudflare Deployment Architecture

## The Rule
**Static → Cloudflare Pages. Dynamic → Cloudflare Tunnel.**

| Content Type | Platform | Why |
|---|---|---|
| Landing pages | Pages | Free, global CDN, always online, auto-deploy on git push |
| SEO pages | Pages | Same — never put static content behind a tunnel |
| Playground (HTML/JS) | Pages | Static frontend, API calls go to Tunnel |
| API endpoints | Tunnel | Needs homelab GPU/engine, dynamic computation |
| MCP server | Tunnel | Needs Swiss Ephemeris, local hardware |

## Cloudflare Pages Setup
1. Dashboard → Workers & Pages → Create → Pages → Connect to Git
2. Select repo (e.g., `mbgulden/hd-platform`)
3. Build command: leave empty (static HTML)
4. Output directory: `docs` (or wherever static files live)
5. Deploy → auto-deploys on every `git push`
6. Custom domain: Pages project → Custom Domains → Add domain
7. Cloudflare auto-configures DNS — no manual DNS changes needed

## Cloudflare Tunnel Setup (for dynamic services)
1. `docker run cloudflare/cloudflared tunnel run --token <TOKEN>`
2. Configure public hostnames in Zero Trust dashboard:
   - `api.example.com` → `localhost:8000`
3. Use `--network host` so container can reach host localhost
4. Token tunnels don't need config.yml — hostnames are in dashboard

## Architecture Pattern
```
User → Cloudflare DNS
  ├── example.com → Pages (static, CDN)
  └── api.example.com → Tunnel → localhost:8000 (GPU server)
```
