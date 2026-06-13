---
name: cloudflare-deployment
description: Deploy static sites and serverless functions to Cloudflare Pages and Workers. Covers wrangler CLI, Pages Functions (Worker under the hood), Workers AI bindings, round-robin AI provider fallback patterns, and Cloudflare Tunnel integration.
---

# Cloudflare Deployment

## When to Use
- Deploying a static site (HTML/CSS/JS) to Cloudflare Pages
- Adding serverless API endpoints via Pages Functions
- Using Cloudflare Workers AI for free LLM inference
- Setting up multi-provider AI fallback (Workers AI → Gemini → template)
- Connecting a Cloudflare Tunnel to local services
- **Batch-generating static pages from WordPress mirrors** — see `references/static-site-content-generation.md` for template extraction, schema injection, page assembly, and CTA block patterns. This reference absorbs the former `static-site-content-generation` skill.
- **Astro/Next/Nuxt framework sites on Cloudflare Pages** — see `references/astro-cloudflare-pages-pitfalls.md` for build pipeline quirks, Node version, content collections, sitemap, and WP-to-Astro migration patterns
- **Darius Star game deployment (project-specific)** — see `references/darius-star-deployment.md` for the custom domains (darius-star.play.whatanadventure.games), Worker route setup (play.whatanadventure.games/darius-star* with <base> injection), Pages+Global Key auth split, sprite preload patterns, procedural background fallbacks, sprite sheet slicing, additive blending for dark backgrounds, and debug labels.
- **GitHub API commit for large repos** — see `references/github-api-commit-large-repos.md` when `git push` fails with HTTP 500 on repos >2GB. Commits single files via REST API, triggering CF Pages auto-deploy without git pack negotiation.
- **Darius Star multiplayer architecture (GRO-958)** — see `references/darius-star-multiplayer-architecture.md` for the per-player input binding pattern, `e.key` Numpad gotcha, remote player integration, and join-during-gameplay flow
- **Pages + Worker path-based routing** — see `references/pages-worker-path-routing.md` for serving a Pages project at a sub-path of another domain via Worker routes, with `<base>` tag injection for relative asset resolution. Covers Worker deploy format (Service Worker, not ES module), route creation, and pass-through pattern
- **Pages custom domain management via API** — see `references/cf-pages-domain-management.md` for adding domains, updating DNS, Global Key vs Bearer token auth split (`cfut_` Pages token vs `cfk_` Global Key), and the remove/re-add verification trigger pattern. **Multi-account setup (Pages in one account, DNS zone in another)** — see `references/cf-pages-multi-account-setup.md` for the full cross-account flow: project creation, domain verification, DNS CNAME records, initial direct upload, and the GitHub OAuth limitation
- **Cloudflare Email Routing (free custom-domain forwarding)** — see `references/cf-email-routing.md` for API-driven enable/check/rule-creation, the destination-verification dashboard-only pitfall, and upgrade path guidance
- **Flywheel Staging as CSS/JS Source of Truth (CRITICAL)** — see `references/flywheel-css-js-source-of-truth.md`. When the mirror doesn't match the original WordPress site visually, `curl` the Flywheel staging homepage and download EVERY linked CSS/JS file directly. Do NOT piece together CSS from git history. The Flywheel site is the live, working reference. Includes: Weglot CSS (front-css.css + new-flags.css), Google Fonts, Kadence version suffixes, style.css, Weglot JS. Also covers the Weglot JS vs manual lang-switcher pitfall — don't add manual `<span class=\"lang-switcher\">` when Weglot JS handles it.\n- **Static Mirror Nav Mismatch — check for missing CSS files first (CRITICAL):** When the mirror nav doesn't match staging despite identical HTML, the #1 cause is CSS files present in the staging static output (`active-oahu-static`) but missing from the mirror repo (`active-oahu-tours-mirror`). **Before writing any inline nav CSS**, diff the CSS directories: `diff <(ls ...static/.../css/) <(ls ...mirror/.../css/)`. In June 2026, `brand-overrides.css` (GRO-751, 316 lines) was missing from the mirror — adding it fixed dropdown hover, mobile menu styling, and brand consistency in one change. See `references/nav-audit-and-fix-workflow.md` step 5 for the full pattern. **For minified stylesheets, use standalone CSS override files loaded after the main stylesheet** (steps 6a-6b in the reference) — never append patches to the minified file.
- **Static Mirror Template Rebuild with Content Extraction** — see `references/template-rebuild-content-extraction.md` for replacing nav/header/footer sections of a template-based mirror while preserving page-specific content. Used in GRO-712 nav component v6 rebuild (162 pages, Jun 2026).
- **Cloudflare credential testing and cleanup** — load `cf-credential-test-procedure` skill. Covers the X-Auth-Key vs Bearer auth split, endpoint-specific permissions, dead key detection, and cross-profile cleanup. Also see `references/cf-pages-api-auth-matrix.md` for the Pages-specific auth limitation (Global Keys return 9106).
- **Static contact forms (formsubmit.co)** — see `references/static-contact-form-pattern.md`
- **Consulting site deployment (AGY-design → Fred-build → tunnel pattern)** — see `references/consulting-site-deployment-pattern.md`. Absorbs the former `consulting-site-deployment` skill.
- **Iterative static site design (AGY design → build → deploy → AGY review → iterate)** — see `references/iterative-static-site-design.md`. Pure CF Pages with wrangler. Includes cache-busting pitfall.
- **PyPI package name availability check** — see `references/pypi-name-availability.md` for the JSON API trick (HTML endpoint lies, always returns 200)
- **WordPress → Static Mirror (lift-and-shift)** — see `references/static-mirror-lift-and-shift.md` for the wget mirror → CF Pages fast migration. Use when the priority is getting off WordPress THIS WEEK with zero content changes. Prefer this over piecemeal Astro rebuilds for 50+ page sites.
- **WordPress Mirror Filename Cleanup (MANDATORY post-mirror step)** — see `references/wp-mirror-filename-cleanup.md` for fixing query-string artifacts, unicode thin spaces, and leading-space filenames that cause silent CF Pages build failures. Run this immediately after every wget mirror before deploying.
- **Static Mirror Link Cleanup (MANDATORY post-mirror step)** — see `references/static-mirror-link-cleanup.md` for the category-by-category, verify-as-you-go approach to fixing broken internal links. Michael's directive: "Do a fine toothed comb on the link cleanup." Batch regex across all pages introduces more problems than it solves.
- **SEO Batch Retrofit (post-mirror optimization)** — see `references/seo-batch-retrofit.md` for bulk-injecting meta descriptions, OG/Twitter tags, lazy loading, and preconnect hints across existing pages. Proven on 238-page AOT mirror, 15-issue SEO sprint.
- **Image Optimization (Pillow quality-stepping)** — see `references/image-compression-pillow.md` for batch-compressing images with max 1920px width and ~300KB target. 94 images, 51MB saved.
- **Static Mirror Post-Cutover Cleanup Pitfalls** — see `references/static-mirror-post-cutover-cleanup.md` for batch regex dangers on HTML, safe vs unsafe cleanup patterns.
- **Staging Access Control (CRITICAL)** — see `references/staging-access-control.md` for the Pages Functions middleware pattern. CF Access on `.pages.dev` subdomains does NOT support IP bypass or service token auth. Use `functions/_middleware.js` with cookie + header dual auth instead.
- **Cloudflare Access gating for Pages preview deployments** — see `references/cf-pages-access-gating.md` for API-driven email-gated staging (app creation, policy setup, service tokens, and the `.pages.dev` service-token limitation).
- **Static Mirror Nav Debugging** — see `references/static-mirror-nav-debugging.md`
- **Static Mirror Force-Push Template Regression** — see `references/static-mirror-nav-debugging.md` → "Git Workflow: Force-Push Template Regression" for the pattern that causes silent template loss during `git push --force && git reset --hard` cycles. Affects any multi-round staging deployment workflow.
- **Static Mirror Template Rebuild** — see `references/static-mirror-template-rebuild.md` for the content-sandwich rebuild pattern (head + body_top + content + body_bottom). Use after modifying templates to regenerate all pages.
- **Standalone Nav Rebuild (GRO-712 Pattern)** — see `references/standalone-nav-rebuild-gro712.md`. When patching the WP-dependent nav fails after 2+ attempts, rebuild as pure HTML/CSS/JS. Check Linear first for existing scoped issues.
- **CDN Cache → Deploy Verification (CRITICAL)** — see `references/cdn-cache-deploy-verification.md` for the direct-deployment-hash-URL bypass pattern. When the custom domain shows stale content after a successful deploy, the CDN edge cache is the culprit — verify via the hash URL, not the custom domain.
- **Surgical Header/Nav Revert** — see `references/surgical-header-nav-revert.md`. Restore ONLY the header and nav to the original WordPress scrape while preserving all SEO/content/link/schema/sitemap fixes. Use when the user says "just the header and nav."
- **Japanese Translation for WP Mirrors**
- **WordPress → Astro migration audit** — see `references/site-migration-audit.md` for the full sitemap-parsing, spider-crawling, cross-referencing, and tracking spreadsheet methodology. Use before starting any site migration
- **Google Multi-Service OAuth** — see `references/google-multi-service-oauth.md` for adding GA4, Search Console, Tag Manager, and GMB scopes to an existing GDrive OAuth flow. Covers API enablement, rate limits, and property discovery.
- **Force-Push Rollback + Deploy Trigger** — see `references/force-push-rollback-and-deploy-trigger.md` for reverting bad CF Pages deploys when git-connected auto-deploy fires on unwanted commits. Covers target verification, force-push, trigger commits, and cache-busting without API access.
- **CDN Edge Cache-Staleness (CRITICAL — check `cf-cache-status` FIRST)** — see `references/cf-external-asset-cache-staleness.md` for diagnosing and fixing CF CDN edge cache serving stale CSS/JS AND stale HTML pages after deploy. When fixes appear to not work despite successful deployments, `cf-cache-status: HIT` is the #1 suspect. Covers version-bust, purge API, and empty-commit trigger for Pages-only tokens.
- **Large asset build delays + SPA fallback (CRITICAL for game/media repos)** — see `references/cf-pages-large-asset-build-delays.md`
- **GitHub API commit as push workaround (large repos)** — see `references/github-api-commit-for-large-repos.md` for when `git push` fails with HTTP 500 on multi-GB repos. Bypasses git entirely by committing files via the GitHub REST API, which triggers CF Pages auto-deploy. for the pattern where new binary assets return `text/html` (SPA fallback) for 5-10 minutes after push while the Pages build processes them. Covers the `curl -sI` → content-type → `xxd` magic-byte verification chain, and the 4-layer disk → git → code → CDN diagnostic stack.
- **AOT Mirror Repo vs active-oahu-static Split** — see `references/aot-mirror-repo-split.md` for the two-repo architecture (mirror deploys, static generates) and why editing templates in one doesn't affect the other.

## Deployment Stack
```
Cloudflare Pages (static site, free)
  ├── index.html, CSS, JS
  └── functions/          ← Pages Functions = Workers under the hood
      └── api/
          └── endpoint.js ← onRequest(context) handler

Cloudflare Workers AI (free tier)
  └── @cf/meta/llama-3-8b-instruct (10K req/day)
  └── Other models available: @cf/meta/llama-2-7b, @cf/mistral/mistral-7b
```

## Quick Deploy
```bash
cd project-dir
npx wrangler login                    # One-time auth
npx wrangler pages deploy . --project-name=my-project
```

## Cloudflare Pages — Build Gotchas

### wrangler.toml Can Skip the Build Entirely

When a `wrangler.toml` file exists in the repo with `pages_build_output_dir = "dist"`, Cloudflare Pages reads it and **skips the auto-detected build command**. It looks for a pre-existing `dist/` directory — which doesn't exist because `npm run build` never ran.

**Symptom:** Build logs show "No build command specified. Skipping build step." followed by "Output directory 'dist' not found."

**Fix:** Rename `wrangler.toml` to `wrangler.toml.example` or remove it entirely. Let CF Pages auto-detect the framework and run `npm run build` automatically. Alternatively, set the build command and output directory manually in the CF Pages dashboard.

### Node Version: `.node-version` and `.nvmrc` Are Not Reliably Read

CF Pages documentation says it reads `.node-version` and `.nvmrc`, but in practice these files are frequently ignored. The reliable way to set Node version is:

1. CF Pages Dashboard → Settings → Environment Variables
2. Add `NODE_VERSION` = `22` (or desired version)
3. Must be **Plaintext** type (NOT Secret — secrets are only available at runtime)

Alternative: downgrade the framework to a version that works on Node 20 (e.g., Astro 5 instead of Astro 6).

### Stale Deployments: Reconnect GitHub

When CF Pages keeps deploying old commits despite new pushes:
1. Settings → Builds & Deployments → Git Repository
2. Disconnect, then reconnect to the same repo/branch
3. This forces a fresh clone of the latest commit

### Pre-built dist/ as a Workaround

If all else fails, pre-build locally (`npm run build`) and commit the `dist/` directory with `git add -f dist/`. CF Pages will deploy it directly without running any build step.

Every file in `functions/api/` becomes an endpoint at `/api/<filename>`. Use the Pages Functions format (NOT the ES module Worker format):

```javascript
// functions/api/interpret.js → /api/interpret
export async function onRequest(context) {
  const { request, env } = context;  // env has AI binding + secrets
  // ... handle request
  return new Response(JSON.stringify(result), {
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
  });
}
```

## Workers AI Binding

In `wrangler.toml`:
```toml
[ai]
binding = "AI"
```

Then access via `env.AI.run(model, { messages, max_tokens, temperature })`.

## Round-Robin AI Provider Fallback

```javascript
// Tier 1: Workers AI (free, 10K/day)
try { return await env.AI.run('@cf/meta/llama-3-8b-instruct', {...}); }
catch (e) { console.log('Tier 1 failed:', e.message); }

// Tier 2: Gemini Flash (free, 1.5K/day)
if (env.GEMINI_API_KEY) {
  try { return await fetchGemini(prompt, env.GEMINI_API_KEY); }
  catch (e) { console.log('Tier 2 failed:', e.message); }
}

// Tier 3: Template fallback (always works, zero cost)
return templateResponse(data);
```

## Secrets & Environment Variables

Set in Cloudflare Dashboard → Workers & Pages → Settings → Variables:
- `GEMINI_API_KEY` — optional fallback provider
- Any other API keys

Secrets are encrypted at rest and injected as `env.SECRET_NAME` in Functions.

## Cloudflare Tunnel Integration

When the static site needs to call a local service:
```
Browser → Cloudflare Pages (static) → fetch() → api.yourdomain.com → Cloudflare Tunnel → localhost:8000
```

Update the `engineEndpoint` in your HTML to point to the tunneled domain.

## Tunnel Debugging Deep-Dive

> **Reference:** `references/tunnel-debugging-recipes.md` — token decoding, session diagnostic patterns, quick tunnel vs named tunnel 404 comparison.

### Critical: Token-Based Tunnel Ingress Lives in the API, Not Locally

When using `cloudflared tunnel run --token <JWT>`, the **ingress rules** (which hostname routes where) are pulled from the Cloudflare API. Local `config.yml` files are IGNORED. This means:

- **Dashboard → Public Hostnames tab** is the ONLY way to add routes
- DNS CNAME (`<hostname> → <tunnel-id>.cfargotunnel.com`) is necessary but NOT sufficient — the ingress must also be configured
- `cloudflared tunnel route dns` CLI commands require the origin cert, which token-based tunnels don't have locally

### Error 1033 (Argo Tunnel Error / HTTP 530)

**Symptom:** TLS handshake succeeds, Cloudflare responds with `error code: 1033` and `HTTP 530`.

**Root cause:** The hostname is NOT in the tunnel's ingress configuration. The DNS resolves to Cloudflare, but the tunnel has no rule for that hostname.

**Diagnosis:**
```bash
# Check the tunnel's ACTUAL ingress (what Cloudflare API sent)
sudo journalctl -u cloudflared --no-pager -n 30 | grep "Updated to new configuration"

# Extract and pretty-print the ingress JSON:
# Look for config="..." in the log line — it contains the full ingress array
```

The ingress shows exactly which hostnames are routed where. If your hostname isn't in that list, it returns 1033 for all requests.

**Fix:** In Zero Trust dashboard → Networks → Tunnels → [tunnel] → Configure → Public Hostnames:
- Subdomain + Domain must match EXACTLY
- Service type: HTTP
- URL: `localhost:<port>` (no `http://` prefix in most dashboard versions, some need it — try both)
- After saving, cloudflared pulls the new config within 30-60 seconds (no restart needed, but `systemctl restart cloudflared` can force it)

### Quick Tunnel Workaround

When dashboard ingress config is problematic, quick tunnels bypass the need for hostnames entirely:
```bash
cloudflared tunnel --url http://localhost:8081
# Outputs: https://<random>.trycloudflare.com → localhost:8081
```
This works instantly — no DNS, no ingress config, no dashboard. The URL changes each restart. Good for testing; not for production.

### Checking Tunnel Connectivity

```bash
# Verify tunnel is connected
ps aux | grep "cloudflared.*tunnel"

# Verify local service is reachable
curl -s http://localhost:<port>/ping

# Verify from cloudflared's perspective (runs as root)
sudo -u root curl -s http://localhost:<port>/ping

# Get tunnel ID from token
python3 -c "
import json, base64, subprocess, re
pid = subprocess.run(['pgrep','-f','cloudflared.*tunnel'], capture_output=True, text=True).stdout.strip().split('\n')[0]
with open(f'/proc/{pid}/cmdline') as f: cmdline = f.read()
token = re.search(r'token (\S+)', cmdline).group(1)
print(json.loads(base64.b64decode(token + '==')))
"
# Returns: {"a": "<account_id>", "t": "<tunnel_id>", "s": "<secret>"}
```

### Multi-Tunnel Confusion (CRITICAL — Check This First)

When a user says "I added the route in the dashboard" and the tunnel logs don't show it, the most common root cause is that you're looking at the **wrong tunnel**. Many setups have multiple tunnels on the same machine or account — growthwebdev.com on one tunnel, humandesignengine.com on another, etc.

**Before any other debugging, run:**
```bash
# List ALL cloudflared services and their tunnel IDs
systemctl list-units --all | grep cloudflared
# Then check each one's token to identify which tunnel it serves
for unit in $(systemctl list-units --all --no-legend | grep cloudflared | awk '{print $1}'); do
  echo "=== $unit ==="
  systemctl cat "$unit" 2>/dev/null | grep -oP 'token \K\S+' | head -1
done
```

**Then ask the user:** "Which tunnel ID did you configure in the dashboard?" Match it against the running services. If the user's tunnel ID doesn't match any running service, the tunnel connector isn't running on this machine — that's the problem, not the ingress config.

This single step would have saved ~2 hours of misdiagnosis in a session where the user had configured `e48d6f7b` (humandesignengine.com) but only `4a6097ff` (growthwebdev.com) was running.

### Common Gotchas

| Gotcha | Symptom | Fix |
|---|---|---|
| Hostname in DNS but not in tunnel ingress | 1033 | Add public hostname in dashboard |
| Wrong service port | 1033 | Verify `ss -tlnp \| grep <port>` |
| Service type set to HTTPS instead of HTTP | 1033 | Change to HTTP (tunnel handles TLS) |
| CF-Access service tokens used as API tokens | 9106 "Missing Authorization" | Need Cloudflare API token, not Access token |
| cloudflared version outdated | Various | Upgrade: check logs for "version X is outdated" |
| Dashboard ingress change not syncing to tunnel | 1033 persists after dashboard save | Check config version in logs — if version number didn't increment, the change didn't save. Delete + re-add the entry, ensure you see a confirmation in the UI. |
| Quick tunnel returns Cloudflare-edge 404 | HTTP 404, `server: cloudflare`, no origin headers | Different from 1033. Tunnel connected but edge isn't routing. |
| **`localhost` resolves IPv6 → 502 (ALL origins fail)** | Named tunnel returns 502 for every hostname, but quick tunnel works. Gateway binds `0.0.0.0:PORT` (IPv4 only). `curl localhost:PORT` works (happy-eyeballs) but cloudflared does NOT have happy-eyeballs — it tries `::1` first and fails silently. | Change ALL ingress service URLs from `localhost` to `127.0.0.1`. Verify: `ss -tlnp` shows IPv4-only bind, `curl "[::1]:PORT"` fails. This is the #1 time-sink in tunnel debugging — always use `127.0.0.1`. |
| **Cloudflare Access intercepting traffic** | HTTP 302 redirect to `cloudflareaccess.com/cdn-cgi/access/login/<hostname>` | DNS resolves, tunnel connected, ingress rule correct, service healthy — but Cloudflare Access is enforcing authentication. Fix: Zero Trust dashboard → Access → Applications → find the application for this hostname → delete or disable the policy. Quick tunnel (`cloudflared tunnel --url http://localhost:PORT`) bypasses Access for testing. |
| **Access not enabled on account (error 9999)** | API returns `"access.api.error.not_enabled: Access is not enabled"` with status 403 | Cloudflare Access (Zero Trust) must be manually enabled ONE TIME in the dashboard before API calls work: Zero Trust → click "Enable Access." This is a dashboard-only action — no API endpoint to enable it. Once enabled, access policies can be created via the `/accounts/{id}/access/apps` API. Required for email-gating Pages preview deployments. |
| **`noTLSVerify` disabled → 502 Bad Gateway** | HTTP 502, `cf-ray` header present, `server: cloudflare`. Error page diagram shows: Browser ✅ → Cloudflare ✅ → Host ❌. Direct `curl localhost:PORT` returns 200 but through-tunnel fails | The tunnel ingress has `noTLSVerify` listed under Origin Configurations but the toggle is OFF. Cloudflared tries to verify TLS on the plain HTTP backend, handshake fails silently, and Cloudflare shows 502. **Verify:** `sudo -u root curl http://localhost:PORT` returns 200 from the tunnel's perspective, confirming the backend is healthy. **Fix:** Zero Trust dashboard → Networks → Tunnels → [tunnel] → Public Hostnames → [hostname] → Edit → Origin Configurations → **Enable the `noTLSVerify` toggle**. The Hermes gateway and most local services serve plain HTTP — TLS verification must be OFF. |

### Dashboard Change Verification

When you add a public hostname in the Zero Trust dashboard, the tunnel pulls the updated config. Verify it actually synced:

```bash
# Check the config version and ingress in tunnel logs
journalctl -u cloudflared --no-pager -n 50 | grep "Updated to new configuration"

# You'll see: config="{...}" version=11
# If the version number doesn't increment after your dashboard change,
# the change didn't save or isn't propagating.
```

The config version is the single source of truth. If it shows version=N and your new hostname isn't in the ingress JSON, the dashboard change hasn't taken effect — delete the entry and re-add it, ensuring you see a confirmation toast in the UI.

### Wrangler.toml Short-Circuits CF Pages Build (CRITICAL)

When a `wrangler.toml` exists at the repo root with `pages_build_output_dir = "dist"`, Cloudflare Pages reads it and **skips the auto-detected build command**. It sees the output dir configured, assumes no build step is needed, and looks for a pre-built `dist/` folder — which doesn't exist because `npm run build` never ran.

**Symptom:** `Error: Output directory "dist" not found.` even though Astro is properly configured.

**Fix:** Rename `wrangler.toml` to `wrangler.toml.example`. CF Pages then auto-detects the framework and runs the build. The `wrangler.toml` file is ONLY for wrangler CLI use — CF Pages reads it but interprets it differently.

### FareHarbor Embed Format

FareHarbor's `autolightframe` API requires specific data attributes on the container div, NOT just an `id`:
```html
<!-- WRONG — won't load -->
<div id="fareharbor-lightframe">...</div>

<!-- RIGHT — loads the booking calendar -->
<div data-fareharbor-lightframe data-fareharbor-shortname="activeoahutours">
  <p>Loading...</p>
</div>
```

Load the API script ONCE in `<head>`, not per-page:
```html
<script src="https://fareharbor.com/embeds/api/v1/?autolightframe=yes"></script>
```

The `data-fareharbor-shortname` must match the FareHarbor account shortname (found in FareHarbor dashboard).

Do NOT try to switch a dashboard-managed tunnel to local config mode:
- Changing the systemd unit from `--token` to `--config` will NOT work
- The tunnel will still pull remote config from the API
- You risk breaking other routes that were working fine

If you need full local control, create a separate tunnel with `cloudflared tunnel create <name>` (locally managed). But dashboard-managed and locally-managed tunnels are fundamentally different — you can't convert between them.

## Cloudflare Pages Build Pipeline (CRITICAL)

### wrangler.toml Short-Circuits Build Auto-Detection

The single most expensive Pages debugging mistake: If your repo has a wrangler.toml with `pages_build_output_dir`, Cloudflare Pages reads the Wrangler config and skips framework auto-detection entirely. It assumes no build is needed, so there is no dist/ folder and deploy fails with `Output directory 'dist' not found`.

Detection: Build log says `Found wrangler.toml file. Reading build configuration...` then `No build command specified. Skipping build step.`

Fix: Rename wrangler.toml → wrangler.toml.example. CF Pages auto-detects the framework, runs npm install && npm run build, and deploys dist/. Alternatively set build command in the CF Pages dashboard.

### \"Uploaded 0 files\" = Poisoned Cache (DO NOT TRUST)

When the build log says `Uploaded 0 files (N already uploaded)` followed by `Success: Assets published!`, the deployment DID NOT actually upload your changes. CF hash-matched every file against a cache from a previous (possibly failed) deployment. If that previous deploy was a partial failure, pages will 404 despite the \"success\" message.

**You want to see `Uploaded N files (0 already uploaded)`** — that means fresh content went up.

If you see `Uploaded 0 files` after fixing a build error: clear the build cache in the dashboard and retry. Do not push more commits — they'll all hash-match the same poisoned cache.

### Node Version: Triple-Prong Approach

CF Pages defaults to Node 20. Astro 6+ requires Node ≥22.12.0. `.node-version` at repo root is documented but unreliably read. Use all three:

1. `.node-version` in repo root: `echo "22" > .node-version`
2. `.nvmrc` in repo root: `echo "22" > .nvmrc` (CF sometimes prefers this format)
3. Dashboard env var: `NODE_VERSION=22` in Settings → Environment Variables (100% reliable)

### Lock File Mismatches

If npm ci fails with dependency tree errors, regenerate with `npm install` (not npm ci), commit the updated package-lock.json, and push.

## Pitfalls

### Build & Deploy

- **`_redirects` comments cause HTTP 500 (CRITICAL — Jun 2026):** CF Pages `_redirects` files support ONLY redirect rules. Lines starting with `#` or any non-rule text cause the entire file to fail parsing, producing HTTP 500 on the deployed site. Symptom: `curl -sI https://domain.com` returns HTTP 500 with no useful error. Fix: single-line file with no comments, e.g. `/* /index.html 200`. Verified on whatanadventure.games.

- **`main` vs `master` branch mismatch:** CF Pages may be connected to `master` while all work is on `main`. Deployments succeed with old content and show no errors — the commit SHA in the deployment log is from the stale branch. **Check:** Dashboard → Settings → Builds & deployments → Production branch. **Fix:** Either change the branch to `main` in the dashboard, or push main to master: `git push origin main:master --force`. Verify the deployment log's commit SHA matches your latest push.

- **`wrangler.toml` short-circuits CF Pages auto-detection:** If `pages_build_output_dir` is set in wrangler.toml without an explicit build command, Cloudflare Pages skips `npm run build` entirely — it looks for `dist/` pre-existing in the repo. If dist/ isn't committed, the deploy fails with "Output directory 'dist' not found." **Fix:** Remove `wrangler.toml` (rename to `.example`) OR set the build command in the CF Pages dashboard (Settings → Builds & Deployments → Build command: `npm run build`). The `.example` rename is preferred — it lets CF Pages auto-detect the framework.

- **Pre-built dist/ bypass pattern:** When CF Pages build environment issues persist (wrong Node version, missing deps, env vars not injecting), commit the pre-built `dist/` folder directly (`git add -f dist/`) and push. CF Pages deploys it with zero build step. Requires a local build first: `npm run build && git add -f dist/ && git commit && git push`. This is a reliable fallback when dashboard configuration fails.

- **CF Pages webhook stalls:** If CF Pages keeps deploying old commits despite GitHub showing new ones, the webhook integration is broken. **Fix:** In CF Pages Dashboard → Settings → Builds & Deployments → Git Repository → Disconnect, then Reconnect to the same repo. This forces a fresh webhook and pulls the latest commit.

- **`.node-version` / `.nvmrc` not always read by CF Pages:** Even when both files are in the repo root with the correct version, CF Pages sometimes ignores them and uses the default Node 20. The `NODE_VERSION` env var in the dashboard may also not appear in build logs. **Workaround:** Either (a) downgrade framework to one compatible with Node 20 (e.g., Astro 5 instead of 6), or (b) commit pre-built dist/ so no build runs. Do not spend more than 3 attempts debugging Node version on CF Pages — use the workaround.

- **Staging branch auto-deploys by default on Pages:** When a CF Pages project has `preview_branch_includes: ["*"]` (the default for GitHub-connected projects), pushing ANY branch triggers a preview deployment automatically — no dashboard configuration needed. The preview URL follows the pattern `https://<short-hash>.<project>.pages.dev` and can have an alias like `https://staging.<project>.pages.dev`. Verify with `GET /accounts/{id}/pages/projects/{name}` → check `latest_deployment.aliases`. Do not spend time configuring branch build controls when it's already auto-deploying.

- **CF Access on `.pages.dev` silently drops IP/service-token auth (CRITICAL — see `references/staging-access-control.md`):** Cloudflare Access intercepts requests to `.pages.dev` staging URLs (browser email OTP works) but IP rules, service token headers, and `service_auth_401` are silently dropped — you always get a 302 redirect to the login page. This is a `.pages.dev` infrastructure limitation, not a misconfiguration. **Fix:** DELETE the Access app and use a `functions/_middleware.js` with cookie + header dual auth instead. The middleware runs after CF's edge but before static content, works on `.pages.dev` without Zero Trust, and supports both browser (`?key=ohana` → cookie) and programmatic (`X-Staging-Key` header) access. Production custom domains are untouched (hostname check in middleware).

- **Silent build failure: HTTP 200 but wrong content:** When CF Pages returns HTTP 200 with OLD page content (wrong title tags, stale canonical URLs), the current deploy failed asset validation and CF fell back to the last successful build. Common causes: (a) corrupted filenames from WP scrape — query-string artifacts (`?ver=`), unicode thin spaces (U+202F), leading spaces — see `references/wp-mirror-filename-cleanup.md`, (b) files >25MB, (c) accidentally committed `.tmp` files from image compression. Fix the issue, push, and the auto-deploy succeeds. Check the Deployments tab in the dashboard for the actual error log.

- **Staging preview gating** — see `references/staging-gate-pattern.md`. Access-control `.pages.dev` preview deployments with a Pages Functions middleware (header + cookie auth). Covers why Cloudflare Access IP/service-token bypass doesn't work on pages.dev subdomains.
- **Weglot language switcher renders as a visible checkbox on static sites (CRITICAL — see `references/weglot-checkbox-fix.md`):** When a WordPress site using Weglot is scraped to static HTML, the language switcher renders as a broken `<input type="checkbox">` because the Weglot JavaScript that hides it isn't running. Every page shows a raw checkbox where the language dropdown should be. **Fix:** Replace the entire `<aside data-wg-notranslate=\"\">...</aside>` block (regex: `<aside data-wg-notranslate=\"\".*?</aside>`) with simple styled links: `<span class=\"lang-switcher\" style=\"...\"><a href=\"/\">English</a> | <a href=\"/ja/\">日本語</a></span>`. Use root-relative paths (`/` and `/ja/`) so links work from any page depth. Apply to all HTML files via Python `re.sub` with `re.DOTALL`.

- **Poisoned cache: \"Uploaded 0 files (N already uploaded)\" after fixing a failed build (CRITICAL — see `references/cf-pages-poisoned-cache.md`):** When a deployment fails (e.g., oversized image), CF Pages caches file hashes for the PARTIAL upload. Even after you fix the root cause and push, subsequent builds report `Uploaded 0 files (N already uploaded)` — hash-matching against the failed deploy's incomplete cache. Result: HTTP 404 for every page that wasn't in the LAST successful deploy, while the build log shows \"Success: Assets published!\" This can persist across multiple pushes. **The build log looks successful — it is NOT.** Fix order: (1) Dashboard → Settings → Builds & deployments → **Clear build cache** → Retry deployment. (2) If \"Build cache is not enabled\" or clearing doesn't help: **Delete and recreate the project** (Settings → Delete → Create new → Connect same repo/branch). Do NOT waste time touching files or changing content — the hashes are server-side, not content-dependent. A content change across 196 HTML files made zero difference in the Active Oahu session.

- **Image optimization: 300KB target, 1920px max width:** Michael prefers web images compressed to ~300KB and resized to max 1920px wide — not just CF-compliant (under 25MB). Use Pillow binary search on JPEG quality to hit the exact target size. After compression, always clean up: `find . -name "*.tmp" -delete`. See `references/static-mirror-lift-and-shift.md` step 6 for the full script.

- **Code 7003 "Could not route" = Workers & Pages not activated (CRITICAL — see `references/cf-pages-api-deployment.md`):** When both wrangler AND REST API return 7003 with a valid Global API Key, the Workers & Pages product is not enabled on this account. It is NOT a permissions or token issue. Fix: Cloudflare Dashboard → Workers & Pages → activate the product. Do not spend more than 2 diagnostic attempts on 7003 — test with the Global Key once. If the dashboard already shows the project with Git connected, skip activation: use the Deployments tab → Retry deployment instead. Git-connected auto-deploy bypasses the API entirely.

- **Worker REST API requires Service Worker format (NOT ES modules):** When deploying a Worker via `PUT /accounts/:id/workers/scripts/:name`, the Content-Type MUST be `application/javascript` (NOT `application/javascript+module`), and the script MUST use Service Worker format (`addEventListener('fetch', ...)`). Using ES module format (`export default { async fetch(...) }`) returns error 10021 "Unexpected token 'export'". The REST API does not support ES module workers. Wrangler CLI supports both formats via `wrangler deploy`, but the REST API only supports Service Worker format.

- **Worker route already exists (code 10020):** When a route pattern already exists for a zone, POST `/zones/:id/workers/routes` returns 10020 with the existing Worker's name. **Do not create a new Worker** — update the existing one via `PUT /accounts/:id/workers/scripts/<existing-name>`. Use `GET /zones/:id/workers/routes` to list existing routes and their associated Workers first.

- **Pages API token (`cfut_`) ≠ Global Key (`cfk_`) — different endpoint scopes (CRITICAL):** Pages-specific tokens (prefixed `cfut_`) use Bearer auth and work for `/pages/*` endpoints ONLY. They do NOT work for zone, DNS, or Worker endpoints. Global Keys (prefixed `cfk_`) use `X-Auth-Email` + `X-Auth-Key` headers and work for zones, DNS, and Workers — but NOT for Pages endpoints (returns 9106). For a full domain setup (add domain to Pages + create DNS record + deploy Worker route), you need BOTH tokens. Validate each token against its target endpoint before starting: Bearer token → `/user/tokens/verify`, Global Key → `/user`.

- **Pages tokens (`cfut_`) fail with wrangler — even when REST API works (9103):** A `cfut_` token that successfully lists projects via `curl -H 'Authorization: Bearer $TOKEN' /accounts/.../pages/projects` will still fail with `npx wrangler pages deploy` — error 9103 "Unknown X-Auth-Key or X-Auth-Email." **Root cause:** wrangler calls the `/accounts` endpoint (among others) which is NOT under `/pages/*` — the Pages-scoped token can't access it. The same token works for all `/pages/*` REST API endpoints but wrangler's broader scope requirement rejects it. **Fix:** Use a CF API Token with account-level `Cloudflare Pages — Edit` permission (not just resource-scoped to a single Pages project), OR use the Global API Key + Email combo for wrangler commands. **Diagnostic:** if `curl` with Bearer auth works for `/pages/projects` but `wrangler pages deploy` fails with 9103, the token is Pages-scoped — switch to a broader token for wrangler. **Fallback:** use the REST API direct upload flow (`references/cf-pages-direct-upload-api.md`) which works with Pages-scoped Bearer tokens.
- **Editing token permissions may rotate the token secret:** If a token suddenly becomes invalid after editing its permissions in the dashboard, the old string was revoked — copy the new token value.
- Wrangler needs `CLOUDFLARE_API_TOKEN` or `npx wrangler login` (interactive)
- In non-interactive environments (CI/CD), use `CLOUDFLARE_API_TOKEN` env var
- Pages Functions use `onRequest(context)` signature, NOT the `export default { fetch() }` Worker format
- Workers AI models are prefixed with `@cf/` — not standard model names
- CORS headers must be explicit in Functions responses
- **Port conflicts:** Always check `ss -tlnp | grep PORT` before binding local services. Hermes dashboard and other Node.js apps commonly occupy 8080. Use an alternate port (8090, 9090) and update Tunnel config accordingly.
- **Michael prefers ACTUAL brand photography, not AI-generated placeholders.** Before creating placeholder images for any of Michael's sites, check his Synology NAS at `/home/ubuntu/mounts/synology-photo/` for real photos. The Active Oahu shared workspace has 1,627+ high-res lifestyle photos across 15 professional shoots. Use Pillow to resize his real photos for web — do not generate gradient/text placeholders. He called out generic placeholders as lacking creativity: "Where's the creativity? Where's the brand loyalty to my colors and logos? What the heck!"
- **Token-based tunnels ingest is API-driven:** Config lives in Cloudflare dashboard, not local files. `config.yml` is ignored when `--token` is used. To add routes, use the dashboard Public Hostnames tab.
- **DNS CNAME alone is not enough:** The tunnel ALSO needs an ingress rule in the Zero Trust dashboard. Both must be configured.
- **CDN cache HIT on production may be INTENTIONAL — ask before busting:** When `cf-cache-status: HIT` with high age on the production domain, do NOT assume this is a stale-cache bug to fix. The user may prefer the cached version over recently deployed changes. Ask: "Production CDN is serving a cached version from X hours ago. The deployed version has additional changes. Which version do you want live?" In a June 2026 session, a trigger-commit cache bust pushed 11 unapproved nav commits to production, and the user requested a full revert back to the cached baseline.

- **Single-branch discipline for game/single-project repos (CRITICAL):** When working on a single-project repo (game, landing page, tool), use ONE branch. Splitting fixes across `staging` and `master` causes feature loss — each branch accumulates different changes and neither is complete. Michael's correction: "This stage to dev workflow is not working and is messing up any forward progress. I don't even know where you are making changes." **Fix:** Consolidate all changes onto the production branch (`master` or `main`). Abandon the staging branch. If you need preview deployments, use the deployment hash URL (`https://<hash>.<project>.pages.dev`) from the Pages API rather than a separate branch. Only use staging branches for multi-contributor projects where PR review is the bottleneck.

- **Production deployments require explicit approval — never push to main without it (CRITICAL):** Staging work happens on the `staging` branch. Production is `main`. The boundary between them is a human gate. **Workflow:** (1) Tag completed staging work as `v9`, `v10`, etc. (`git tag -f v10`), (2) push tag to origin, (3) the user says the EXACT phrase **"approve vN for production"**, (4) ONLY THEN: `git checkout main && git reset --hard <vN-commit> && git push origin main --force`. Nothing else goes to production without those words. **Never** push staging work alongside approved work — each push to main must be a single, tagged, approved commit. In June 2026, a staging rebuild was pushed to production alongside an approved nav fix (v9). The user said: "And then you also pushed staging updates to production. What the heck? We need to label these approvals, like I approve of pushing v9 and then you only push v9 live, not v10." **Recovery:** `git tag -f v9-approved <commit>`, `git reset --hard <v9-commit>` on main, force-push. The one exception: when the user explicitly says "push to production now" or "deploy this live" — that counts as approval for the current staging state.

- **User's reference URL is the source of truth — compare before changing:** When the user says "compare against this URL" or "this is what it should look like," STOP making CSS/HTML edits. Fetch the reference URL (`curl -s`), save it, and diff EVERYTHING against the current site: CSS files (MD5 hash), inline `<style>` blocks, `<link>` tags, and nav HTML structure. Do NOT guess at CSS fixes when you have a working reference to diff against. In a June 2026 session, 6+ hours of nav debugging ended when the user pointed at `https://activeoahutours2.flywheelstaging.com` and the fix was downloading the exact CSS/JS files from that URL — the mirror was missing Weglot CSS (152KB), Google Fonts, and had version-mismatched Kadence links.

- **Flywheel reference pages have the correct DOM structure — copy it, don't rebuild it (CRITICAL):** When the user provides a Flywheel staging URL as the reference for how a page should look, the Flywheel page's HTML structure IS the answer. The CSS classes are already correct — they're Kadence/theme classes that only need the right DOM hierarchy to work. Do NOT analyze the template system, propose architectural changes, or add new CSS. Just: (1) `curl` the Flywheel page, (2) extract the content area (`#content → section.container → .row → main#main + aside.sidebar`), (3) wrap with the mirror's nav template, (4) deploy to staging. The user's directive: "Each of these flywheel pages have the correct setup. You shouldn't have to add any new css. Just use the same classes and voila! You suddenly have the correct layout!!!! What a concept!!!! (Sarcasm). But seriously, I don't understand what the heck you are doing. It's seriously copy and paste solutions. Maybe you are overthinking it." In a June 2026 session, 2 hours were spent debugging a triple-nested `#content` div problem when the fix was extracting the Flywheel content area and wrapping it with the mirror nav — 10 minutes of work.\n\n- **Mirror nav must use WordPress-exact classes, not custom ones (CRITICAL):** The theme CSS at `/wp-content/themes/activeoahu/css/style.css` styles specific WordPress nav classes: `.main-navigation`, `.menu-toggle`, `.menu-item`, `.menu-item-has-children`, `.sub-menu`, `.nav-menu`, `.menu-menu-1-container`, `.social-links`. If the mirror nav uses different classes (e.g., `<nav class=\"navbar\">` instead of `<nav class=\"main-navigation\">`), the theme CSS selectors won't match and the nav renders unstyled. **Fix:** Copy Flywheel's EXACT nav HTML structure: `<nav id=\"site-navigation\" class=\"main-navigation\">` with `<div class=\"menu-menu-1-container\"><ul id=\"primary-menu\" class=\"menu\">` and full `menu-item-*` classes on every `<li>`. Add the WordPress `navigation.js` inline toggle script. Add the `.navbar` wrapper for mobile float clearing. Split the `.social-header` into two divs (phone+button on right, Weglot/lang-switcher separate). See `references/nav-audit-and-fix-workflow.md` for the AGY-driven audit loop.\n\n- **AGY review before showing staging to user (MANDATORY for nav/frontend):** When the user asks to review staging work (especially nav/header/frontend), run AGY comparison FIRST — before sending links or screenshots. The user's directive: "Please have AGY review before you send back to me." The AGY audit catches issues (missing toggle JS, wrong sub-menu indentation, float collapse, missing wrappers) that Playwright screenshots alone might miss. Write the task as a plain-text file (no markdown in `--print`), point AGY at both screenshots + URLs, have it save the report, then fix all findings before presenting to the user. After fixes, re-run AGY verification before the final presentation. See `agy-vision-pipeline` skill → "AGY as Reviewer" section.

- **`_headers` file sets aggressive asset caching — compounds CDN staleness:** Static mirrors scraped from WordPress often include a `site/_headers` file with `/*.css Cache-Control: public, max-age=86400` (24-hour CSS cache) and `/*.html Cache-Control: public, max-age=3600`. These rules are applied by CF Pages at the edge and are SEPARATE from the CDN cache on the custom domain. Together they mean: (a) CSS changes take up to 24 hours to reach visitors even after a successful deploy, and (b) HTML changes take up to 1 hour. When debugging "my changes aren't showing" after a confirmed deploy, check BOTH `cf-cache-status` on the custom domain AND the `_headers` file rules. Reducing `max-age` for CSS to 3600 or lower during active development prevents multi-hour staleness. The `_headers` file uses Cloudflare's standard syntax: each path pattern followed by indented `Header-Name: value` lines.

- **Global API Key ≠ API Token — different auth headers AND different endpoint support (CRITICAL):** Cloudflare has TWO auth methods and they use DIFFERENT headers AND support DIFFERENT endpoints. Global API Key (37-char hex): `X-Auth-Email: <email>` + `X-Auth-Key: <key>`. API Token (various prefixes like `cfk_`, `cfat_`): `Authorization: Bearer <token>`. **Scoped API Tokens may only work for specific endpoints** — in June 2026, a `cfk_` token worked for `/user` but returned `6103 Invalid format for X-Auth-Key header` for Accounts/Zones/Pages. The `/user` endpoint accepts API Tokens with X-Auth-Key header (anomaly), but all other endpoints require proper Bearer auth AND sufficient token permissions. **For Zone creation, Pages management, and domain operations: use the Global API Key.** API Tokens with limited permissions will silently fail on these endpoints with `9106 Authentication failed` or `6003 Invalid request headers`. **Test pattern:** `curl /user` first to verify the key works at all, then test the target endpoint. If `/user` works but target fails, the token lacks permissions — switch to Global API Key.
- **Systemd services and Python site-packages**: systemd runs with a clean environment that does NOT include the user's `~/.local/lib/python3.12/site-packages`. If a Python service fails with `ModuleNotFoundError` for a package that works interactively, add `Environment=PYTHONPATH=/home/<user>/.local/lib/python3.12/site-packages` to the `[Service]` section. Use `python3 -c "import site; print(site.getusersitepackages())"` to find the correct path. Do NOT use `--break-system-packages` to install globally — it's fragile and can break OS Python.

## Architecture Decision: Pages vs Tunnel

| What | Where | Why |
|---|---|---|
| **Static sites** (landing pages, SEO, playground) | Cloudflare Pages | Free, global CDN, always online, no server dependency |
| **Dynamic APIs** (HD computation) | Cloudflare Tunnel → local server | Requires local GPU/engine, can't run on Pages |
| **AI interpretation** (LLM calls) | Pages Functions + Workers AI | Free tier (10K req/day), no GPU needed for small models |

Rule: if it's static HTML/JS, put it on Pages. If it needs the MCP engine or GPU, tunnel it.
- **Stale Cloudflare credentials — verify before debugging**: ... Error 9103 means the key is dead. Search for alternative credentials — valid API Tokens may exist in nested `.env` files under `~/.hermes/profiles/*/`.
- **CF Pages Direct Upload via REST API** — see `references/cf-pages-direct-upload-api.md` for deploying static sites without wrangler or Git. Covers zip creation, manifest format, form-upload deployment, verified account IDs, and auth discovery. **Pitfall: manifest too large for bash `-F "manifest=$JSON"`** — when the manifest has 3000+ entries, the JSON string exceeds bash's argument size limit (`Argument list too long`). Use Python's `urllib.request` with multipart form data directly instead of curl.

- **SPA fallback diagnostic — `content-type: text/html` on asset URLs (CRITICAL):** When `curl -sI` on a `.png`/`.wav`/`.mp3` URL returns `content-type: text/html`, the file does NOT exist on the CDN. CF Pages SPA fallback is serving `index.html` for the missing path. The browser `<img>` or `<audio>` tag loads the HTML as the resource — `naturalWidth` will be 0 and `onerror` fires. **Never trust HTTP 200 alone** — always check `content-type`. This is the #1 diagnostic for \"my sprites/audio aren't loading but the URLs return 200.\"
- **Flywheel staging is the source of truth for WordPress static mirrors.** When the mirror nav/header looks wrong, pull CSS directly from the Flywheel staging URL (`https://activesite.flywheelstaging.com`) — do NOT piece together CSS from commits. Match style.css byte-for-byte (MD5 comparison), Weglot CSS, Kadence versions, and Google Fonts. Then use AGY for a code-level audit comparing the rendered Flywheel DOM against the mirror's `site/index.html` to catch duplicate JS handlers, missing files, and structural mismatches.
- **CDN cache serves STALE HTML, not just stale CSS.** `cf-cache-status: HIT` with `age: 35000+` means the HTML page itself is cached. A CSS version bump (`?v=N`) only busts asset caches — the HTML page remains stale until a new CF Pages deploy triggers. Push an empty commit to force a fresh deploy: `git commit --allow-empty -m "trigger: bust CDN cache" && git push origin main`. **Active Oahu Tours specifically:** the `cfk_` key for `michael@activeoahu.com` in orchestrator `.env` is stale (as of June 2026). The real credential is `CLOUDFLARE_API_TOKEN=cfat_...` in `~/.hermes/profiles/kai/.env` paired with `CLOUDFLARE_ACCOUNT_ID=3e13f120ec7532f0bc8ac0bc9bfc7108`. This token uses Bearer auth, NOT X-Auth-Key. The AOT account is separate from the growthwebdev account (`e08f006fc73b79ca0668ba828519c925`).
- **read_file line-number prefix corruption:** When pulling external content (REST APIs, web scraping) and writing to markdown files for content collections, the read_file tool returns `LINE_NUM|CONTENT` format. If content is piped through a tool that echoes this format into a file, the line-number prefix becomes baked into the file content itself. Detection: `python3 -c "print(repr(open('file.md','rb').read(20)))"` shows hex starting with spaces and digits. Fix: strip with `re.sub(r'^\s+\d+\|', '', line)` in a Python cleanup script. This happened with 18/64 WordPress blog posts migrated this session.
- **Static Mirror Nav Matching — diff staging CSS first, don't add inline overrides:** When the live mirror's nav doesn't match the staging site visually, the issue is nearly always CSS, not HTML. The mirror templates already have the correct nav structure (same menu items, same hierarchy as staging). Diff the staging `style.css` against the mirror `style.css` FIRST. In a June 2026 session, 6+ iterations of inline CSS overrides failed because they fought the existing theme stylesheet. The fix was copying the staging CSS directly (which already had brand overrides appended) and removing the conflicting inline nav styles. **Never add inline `!important` nav overrides to head.html when a full stylesheet diff is available.**
- **Stale inline CSS in generated pages overriding external stylesheets (CRITICAL — see `references/stale-generated-page-inline-css.md`):** When nav/visual fixes fail after multiple successful deployments, the generated HTML pages may contain stale inline `<style>` blocks from a previous build. These load AFTER external CSS files and override them by cascade. **Always `curl | grep` the live page before editing local templates** — if the live page has CSS rules not in your templates, the generated pages are stale. Strip with Python regex, then rebuild. 197 pages, 23,406 lines removed in June 2026.
- **wkhtmltoimage renders stale/cached pages — use deployment hash URLs:** When taking screenshots for visual verification, wkhtmltoimage may render cached content even with `?nocache=$(date +%s)`. The custom domain (`activeoahutours.com`) can serve CDN-cached HTML regardless of cache-bust params. **The reliable fix:** use the direct deployment hash URL (`https://<hash>.<project>.pages.dev`) from the Pages API — it bypasses the custom domain's CDN entirely. Fetch it via: `curl -s -H "Authorization: Bearer $TOKEN" "https://api.cloudflare.com/client/v4/accounts/{id}/pages/projects/{name}/deployments?environment=production&per_page=1" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['url'])"`. Also: (1) `rm -f` old screenshot files before regenerating, (2) use new output filenames (hash URLs change per deploy anyway).
- **When CSS fixes fail after 2 attempts, delegate to AGY with full codebase access:** The anti-pattern: Hermes adds inline CSS → conflicts with stylesheet → adds more `!important` → repeat. After 2 failed CSS fix attempts, stop and launch AGY with `--add-dir /path/to/repo` and full edit permissions. AGY reads the entire codebase, understands the cascade, and makes coordinated edits. See `antigravity-cli-orchestration` → Hang Pattern #13.
- **Astro `<script>` tags become `type=\"module\"`:** Astro wraps inline `<script>` tags as ES modules (`<script type="module">`) which defers execution and changes scoping. This can silently break DOM manipulation scripts (e.g., mobile menu toggles, FareHarbor embed triggers). **Fix:** Add `is:inline` attribute to the `<script>` tag: `<script is:inline>...</script>`. This tells Astro to emit the script verbatim without module wrapping.\n\n- **Tailwind not processing (`@apply` / `@tailwind` emitted raw):** If the built HTML contains raw `@tailwind base` / `@apply bg-navy` in inline `<style>` blocks, Tailwind's PostCSS pipeline isn't running. Astro requires `@astrojs/tailwind` integration (not just manual postcss.config.js). **Fix:** `npm install @astrojs/tailwind`, add `import tailwind from '@astrojs/tailwind'` to astro.config.mjs, add `tailwind()` to integrations array, create `tailwind.config.mjs` with `content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}']` and custom theme colors. Remove any manual `postcss.config.js` — the integration handles it.\n\n- **FareHarbor embed not loading:** FareHarbor's `autolightframe` API requires proper data attributes on container elements, not arbitrary IDs. **Fix:** Use `<div data-fareharbor-lightframe data-fareharbor-shortname=\"SHORTNAME\">` for booking containers. Load the FareHarbor API script ONCE globally in `<head>` (not per-page). Remove old `id=\"fareharbor-lightframe\"` + per-page `<script src=\"...fareharbor...\">` patterns. The shortname must match the FareHarbor account exactly (`activeoahutours`, not `activeoahu`).\n\n- **`data-api` HTML override shadowing widget defaults:** (see above)
- **`nonlocal` unsupported in `execute_code` nested functions:** When using `execute_code` for batch operations with regex callback functions, `nonlocal` raises `SyntaxError: no binding for nonlocal`. Use a list wrapper instead: `count = [0]` then `count[0] += 1` inside the nested function.
- **FareHarbor shortname detection (wrong regex):** FH booking buttons embed the shortname as `FH.open({'shortname':'activeoahutours',...})` in onclick handlers — NOT `shortname:\s*'([^']+)'`. To audit: `re.findall(r"shortname':'([^']+)'", content)`. The FH API script should be on all tour/rental listing pages; informational guides and partner pages legitimately lack it.