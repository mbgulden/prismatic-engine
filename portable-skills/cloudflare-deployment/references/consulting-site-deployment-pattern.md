---
name: consulting-site-deployment
description: Deploy a new AI consulting or project website using the AGY-design → Fred-build → tunnel-deploy → CF-Access pattern developed for beyondsaas.ai.
triggers:
  - deploy new site
  - build consulting website
  - create landing page
  - beyondsaas pattern
  - AGY design site
---

# Consulting Site Deployment

When Michael needs a new consulting/project website, use the pattern proven with beyondsaas.ai.

## The Pattern

```
0. Linear task FIRST → create AGY task with labels agent:agy + pipeline:visual-design
1. Domain → Cloudflare (Michael buys, Fred configures)
2. AGY designs → visual brief (colors, fonts, layouts, copy)
3. Fred builds → static HTML/CSS based on AGY design
4. Tunnel routes → add hostname to existing Growth Web tunnel
5. Cloudflare Access → email PIN gate (optional, for private sites)
6. PDF freebie → pandoc + wkhtmltopdf, branded to match
```

**Step 0 is MANDATORY.** Michael's directive: "All UX/UI/logo/landing page/app/module design builds get assigned to AGY via Linear." Never skip creating the Linear task before starting design work. Fred builds only after AGY's task is complete.

## Step 1: Domain & Zone

Michael buys the domain on his Cloudflare account. Fred finds the zone:
```python
GET /client/v4/zones?name=<domain>
```

Record: zone ID, account ID, tunnel ID.

## Step 2: AGY Visual Design

Launch AGY in a tmux session for visibility:
```bash
tmux new-session -d -s agy-<project>-design -x 160 -y 40
tmux send-keys -t agy-<project>-design '/home/ubuntu/.local/bin/agy --print "/goal Design the visual identity for <domain>: color palette (hex), typography (Google Fonts), homepage layout (ASCII wireframe), mobile responsive plan, and 3-5 headline options. Dark mode preferred. Vibe: [describe]. Output as structured design brief." --print-timeout 180s' Enter
```

## Step 3: Fred Builds the Site

Based on AGY's design brief, build a single-file static HTML page with:
- Embedded CSS (no build step, instant deploy)
- Google Fonts via CDN link
- All sections AGY specified
- Mobile responsive with media queries
- Actual copy text, never lorem ipsum
- Freebie PDF link when applicable

Serve from a local directory on a dedicated port:
```bash
mkdir -p /home/ubuntu/work/<project>-site
# Write index.html
cd /home/ubuntu/work/<project>-site && python3 -m http.server <port> --bind 127.0.0.1 &
```

## Step 4: Tunnel Routing

Add the new hostname to the Growth Web tunnel's ingress config BEFORE the catch-all rule:
```python
# GET current config → insert new ingress rule before http_status:404 → PUT
{
    "service": "http://127.0.0.1:<port>",
    "hostname": "<domain>",
    "originRequest": {"noTLSVerify": True}
}
```

Create DNS CNAME record pointing to `{tunnel_id}.cfargotunnel.com` with `proxied: true`.

## Step 5: Cloudflare Access (For Private Sites)

If the site should be invitation-only:
```python
# POST /accounts/{id}/access/apps → self_hosted type
# POST /accounts/{id}/access/apps/{app_id}/policies → email include list
# Session duration: 720h max (30 days)
```

For public sites (like beyondsaas.ai), skip this step.

## Step 6: Freebie PDF

When the site has a lead magnet:
1. Write markdown with white background styling
2. Match website fonts (Space Grotesk, Plus Jakarta Sans etc)
3. Convert: `pandoc input.md -o output.pdf --pdf-engine=wkhtmltopdf`
4. Host alongside the site: `cp output.pdf /home/ubuntu/work/<project>-site/`

## Tone Rules (from beyondsaas.ai feedback)

- **Never**: "embedded in your systems" — sounds invasive
- **Use**: "built to work with your stack," "built alongside your team," "works the way you work"
- Frame problems gently: "falls short" not "trap"
- Positive, professional, trustworthy — not aggressive or shady

## DNS Availability Checking

DNS lookups (NXDOMAIN) do NOT confirm domain availability. Use WHOIS:
```bash
whois <domain> | grep -iE "No match for|NOT FOUND|No Data Found|Status: free"
```

## Alternative: CF Pages Deployment (no tunnel, no server)

When the site is pure static HTML/CSS (no backend API, no local services), skip the tunnel entirely and deploy to CF Pages. This is simpler and more reliable:

```bash
# 1. Create Pages project
CLOUDFLARE_EMAIL="..." CLOUDFLARE_API_KEY="..." \
  npx wrangler pages project create <project-name> --production-branch=main

# 2. Deploy
CLOUDFLARE_EMAIL="..." CLOUDFLARE_API_KEY="..." \
  npx wrangler pages deploy . --project-name=<project-name> --branch=main --commit-dirty=true

# 3. Add custom domain (requires ACCOUNT_ID in path)
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT/domains" \
  -H "X-Auth-Email: ..." -H "X-Auth-Key: ..." \
  -H "Content-Type: application/json" \
  -d '{"name":"example.com"}'

# 4. DNS CNAME → <project>.pages.dev (proxied=true)
# 5. Remove/re-add domain to trigger verification (DNS must point to Pages FIRST)
# 6. Poll until status=active
```

See `references/cf-pages-domain-management.md` for the full domain management flow.

**When to use Pages vs Tunnel:** Static HTML → Pages. Needs backend API/GPU/local services → Tunnel.

## Pitfalls

- The Pages API path is `/accounts/$ACCOUNT_ID/pages/projects/...` — NOT `/pages/projects/...`. Missing the account ID returns 7000 "No route for that URI."
- Wrangler needs `CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY` env vars set, not `CLOUDFLARE_API_TOKEN`
- DNS must point to Pages BEFORE domain verification — otherwise remove/re-add after DNS update
- AGY sessions timeout at 180-300s — for complex builds, break into design-then-build phases
- The `python3 -m http.server` process dies when the parent terminal closes — use `nohup` or systemd for persistence
- Cloudflare tunnel config is async — allow 30-60s after PUT before testing
- DNS CNAMEs must point to `{tunnel_id}.cfargotunnel.com` not the domain itself
- `localhost` resolves to IPv6 first on some systems — always use `127.0.0.1` in tunnel ingress
