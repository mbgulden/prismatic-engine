# Cloudflare Pages — Static Site Deployment Pattern

## When to Use
For ANY static marketing site, landing page, documentation site, or SEO content. Cloudflare Pages is free, globally CDN-distributed, and auto-deploys on git push. It does NOT depend on the homelab being up.

## When NOT to Use
Dynamic services that need compute (API endpoints, MCP engine, databases). Those stay on the homelab behind Cloudflare Tunnel.

## Deployment Steps

### 1. Structure Your Repo
```
repo/
├── docs/                  ← output directory
│   ├── index.html
│   ├── *.html
│   └── subdirs/
```

### 2. Push to GitHub
```bash
cd repo && git add -A && git commit -m "deploy" && git push origin main
```

### 3. Cloudflare Dashboard
- Workers & Pages → Create application → Pages tab → Connect to Git
- Select repository
- **Framework preset**: Set to **None** (this reveals the build output directory field — hidden otherwise)
- **Build command**: Leave blank (static HTML, no build step)
- **Build output directory**: `docs` (or wherever your HTML lives, no trailing slash)
- Save and Deploy

### 4. Connect Domain
- In the Pages project → Custom Domains → Add `yourdomain.com`
- Cloudflare auto-configures DNS

## Pitfall: Stale `data-api` / Widget Configuration Attributes

When HTML pages include hardcoded configuration attributes on widget divs (e.g., `<div class="widget" data-api="https://old-url.com">`), those attributes **override the widget's built-in defaults**. If the underlying service URL changes (domain switch, tunnel migration), every page with a stale `data-api` attribute breaks silently — the widget's JS default is correct, but the attribute overrides it.

**Fix pattern**: Remove the `data-api` override entirely and let the widget fall back to its built-in default. The widget's default is maintained in one place (the widget JS file); duplicating it in HTML creates a second place that must be updated.

**Detection**: `curl -s https://domain.com/page | grep "data-api"` — any results are overrides that will go stale.

## Pitfall: CDN Cache After Deploy

Cloudflare Pages auto-deploys on git push, but the CDN edge cache may serve stale pages for 1–10 minutes after deployment. A deploy that "didn't work" may actually be live but cached.

**Verification**: `curl -s "https://domain.com/page?nocache=$(date +%s)" | grep "expected-content"` — the cache-bust parameter bypasses CDN cache and shows the real deployed version.

**Fix**: Wait. Cloudflare CDN clears on its own within minutes. No manual purge needed.

## Branch Preview URLs (Staging Verification)

Every branch pushed to a CF Pages-connected repo gets its own preview URL automatically:

```
https://<branch-name>.<project-name>.pages.dev/
```

**Pattern**: The branch name becomes a subdomain of `<project>.pages.dev`. Dashes in branch names are preserved (e.g., `deploy-fresh` → `https://deploy-fresh.active-oahu-tours-mirror.pages.dev/`). This is the fastest way to verify staging changes — no API key needed, just curl the preview URL.

**Use this for**: Verifying staging deployments when pushing to non-main branches. No need to fight Cloudflare API auth — the preview URL works immediately after the git push triggers the deploy.

**Fallback chain for finding the preview URL**: (1) Try `<branch>.<project>.pages.dev` first, (2) try `<branch>.<alt-project-name>.pages.dev`, (3) fall back to `https://<commit-sha>.<project>.pages.dev` (commit-based URL).

## Pitfall: Branch Name Mismatch (main vs master)

CF Pages auto-deploys from the repo's **default branch** only. If you `git init` and push to `master` but the GitHub repo's default branch is `main`, the deploy never triggers — CF Pages watches `main` and sees no new commits.

**Symptoms**: Repo has the right files, `git push` succeeds, but `curl https://<commit>.project.pages.dev/` returns 404 "Deployment Not Found" — no deploy was created for that commit.

**Fix**: 
```bash
# Option A: Rename local branch to match repo default
git branch -m master main && git push origin main --force

# Option B: Change the repo's default branch on GitHub (Settings → Branches)
```

**Prevention**: After creating a new repo, always check: `curl -s "https://api.github.com/repos/$OWNER/$REPO" | python3 -c "import sys,json; print(json.load(sys.stdin).get('default_branch'))"` — match your local branch name before pushing.

## Pitfall: Worker Auto-Config Corruption
When you first connect a repo, Cloudflare may auto-create a Worker (the `cloudflare/workers-autoconfig` branch). This Worker wraps HTML output in a code viewer, adding line numbers like `1|<!DOCTYPE html>`. This breaks CSS completely — the site looks unstyled.

**Fix**: Deploy as Pages (not Workers). Delete the Worker or remove its route binding. Pages serves raw HTML without transformation.

**Detection**: `curl -s https://domain.com | od -c | head -1` — if you see digits and pipes before `<`, the Worker is corrupting output.

## Architecture Decision: Pages vs Tunnel
```
Static (Cloudflare Pages — FREE):
├── Marketing site (index.html, landing pages)
├── SEO pages (gates, channels, profiles)
├── Playground (static HTML + Pages Functions for AI)
└── Docs / blog

Dynamic (Cloudflare Tunnel — needs server):
├── API endpoints (FastAPI, MCP engine)
├── Report generation (PDF pipeline)
└── Database-backed services
```

The tunnel forwards to localhost ports. Pages serves from Cloudflare's edge. NEVER serve static HTML through a tunnel — it's slower, less reliable, and ties uptime to your homelab.
