# Cloudflare Pages Deployment for Static Sites

## When to Use
- Static HTML/CSS/JS sites (no server-side rendering needed)
- SEO landing pages, documentation, playgrounds
- Anything that doesn't need a database or real-time computation

## The Pattern

1. **Repo structure**: All static files in a `docs/` directory
2. **Connect to Git**: Cloudflare Pages → Connect to Git → select repo
3. **Build settings**: Leave build command empty, output directory = `docs`
4. **Deploy**: Auto-deploys on every push to main

## Custom Domain

1. Cloudflare Pages project → Custom Domains → Add domain
2. Cloudflare auto-configures DNS CNAME
3. No tunnel needed for static content

## CRITICAL GOTCHA: Worker Auto-Config vs Pages

When you first connect a repo, Cloudflare may offer to create a **Worker** (not Pages). Workers auto-create a `cloudflare/workers-autoconfig` branch and wrap your HTML in a syntax-highlighting code viewer. This corrupts output — `<style>` tags get `1|`, `2|` line number prefixes, breaking CSS.

**Symptoms of Worker corruption**:
- Page loads but is completely unstyled
- Raw HTML has `1|<!DOCTYPE html>` format
- `cf-cache-status: HIT` on corrupted content

**Fix**: Delete the Worker entirely. Deploy as **Pages** (not Worker). The Pages deployment serves raw static files without transformation.

**How to tell which you have**: Check Cloudflare Dashboard → Workers & Pages. If your project appears under "Workers", delete it and recreate as Pages.

## Port Conflicts on Homelab

Port 8080 is often taken by Hermes dashboard or other services. Use `ss -tlnp | grep <port>` to check before starting servers. Common alternatives:
- 8090 for landing page dev server
- 8000 for API/payment server

## Auto-Deploy Flow

```
git add docs/
git commit -m "update site"
git push origin main
→ Cloudflare Pages auto-detects push
→ Builds (instant for static)
→ Deploys globally (~30 seconds)
```

## Why Not a Tunnel for Static Sites?
- Tunnel depends on your homelab being up
- Pages has global CDN, free bandwidth, instant cache
- Tunnel is for dynamic content (API, computation, webhooks)
