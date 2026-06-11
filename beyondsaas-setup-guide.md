# Hermes — beyondsaas.ai Setup Guide

**Live site:** https://beyondsaas.ai  
**Repository:** `/home/ubuntu/work/beyondsaas-site/`  
**Deployment:** Cloudflare Pages (project: `beyondsaas`)  
**Tunnel:** Growth Web → `http://localhost:8090`  
**Bot:** @Beyondsaasai_bot (standalone, NOT Hermes gateway)

---

## Architecture Overview

```
beyondsaas.ai
  │
  ├── Cloudflare DNS (proxied)
  │
  ├── Cloudflare Tunnel (Growth Web)
  │     └── localhost:8090 ← static site
  │
  └── beyondsaas-bot.service (systemd)
        └── /home/ubuntu/work/beyondsaas-bot/bot.py
```

The site is an **Astro static build**. No backend server, no database. Pure static HTML served from `/var/www/beyondsaas/` on port 8090.

---

## The Stack

| Layer | Technology |
|-------|-----------|
| Framework | Astro 5 |
| CSS | Tailwind CSS (with custom theme: rust/amber/slate) |
| Fonts | Space Grotesk (headings), Plus Jakarta Sans (body) |
| Deployment | Cloudflare Pages (primary) + rsync fallback |
| Local serve | Static files at `/var/www/beyondsaas/`, served via Python HTTP server on port 8090 |
| Bot | Standalone python-telegram-bot, systemd-managed |

---

## Brand DNA (The Three Pillars)

| Pillar | Meaning |
|--------|---------|
| **Authenticity** | Your voice. No AI pretending to be you. Direct email, no chatbot. |
| **Sovereignty** | Your data stays in your VPC. Zero data egress. |
| **Speed** | Bespoke pipelines in days, not quarters. Production code, not slide decks. |

**The Caged Lion Rule:** NO chatbot greeters, NO AI-generated faces, NO "talk to our AI." Michael is the visible expert.

**Colors:** Copper rust `#B87333`, Safety amber `#FFB300`, Obsidian background  
**Aesthetic:** Industrial, terminal-inspired, "steeped in coffee, steel, and rust"

---

## Deployment

### Primary: Cloudflare Pages
```bash
cd /home/ubuntu/work/beyondsaas-site
npm run build
npx wrangler pages deploy dist/ --project-name=beyondsaas --commit-dirty=true
```

### Fallback: rsync (use when CF credentials unavailable)
```bash
cd /home/ubuntu/work/beyondsaas-site
npm run build
rsync -av --delete dist/ /var/www/beyondsaas/
```

After rsync, verify files exist:
```bash
ls /var/www/beyondsaas/blog/<slug>/index.html
```

---

## Site Structure

| Page | URL | Component |
|------|-----|-----------|
| Homepage | `/` | Hero + section organisms |
| Origin Story | `/origin/` | PageTemplate + QuotePull |
| Pricing | `/pricing/` | PageTemplate + tier cards |
| Fractional CAIO | `/services/fractional-caio/` | ServiceDetail template |
| AI Implementation | `/services/implementation/` | ServiceDetail template |
| Agent Orchestration | `/services/orchestration/` | ServiceDetail template |
| Custom AI Plugins | `/services/plugins/` | ServiceDetail template |
| Blog (8 posts) | `/blog/` | PageTemplate + post cards |
| Agentic Systems Guide | `/agentic-systems-guide/` | PageTemplate |
| Freebie PDF | `/freebie.pdf` | 10-chapter AI Playbook |

---

## The Bot (@Beyondsaasai_bot)

**NOT public-facing** — locked to Michael's chat ID only.

- **Role:** Lead manager — tracking, follow-up drafting, call prep, reminders
- **Service:** `beyondsaas-bot.service` (systemd)
- **Code:** `/home/ubuntu/work/beyondsaas-bot/bot.py`
- **Model:** DeepSeek v4-flash
- **Architecture:** Standalone python-telegram-bot (NOT Hermes gateway — avoids port conflicts)

---

## Key Files

| Path | Purpose |
|------|---------|
| `/home/ubuntu/work/beyondsaas-site/` | Astro project root |
| `/home/ubuntu/work/beyondsaas-site/src/components/` | Atomic component library (atoms, molecules, organisms) |
| `/home/ubuntu/work/beyondsaas-site/src/pages/` | All site pages |
| `/home/ubuntu/work/beyondsaas-bot/bot.py` | Telegram bot source |
| `/var/www/beyondsaas/` | Served static site |

---

## Quick References

**Build + deploy in one command:**
```bash
cd /home/ubuntu/work/beyondsaas-site && npm run build && rsync -av --delete dist/ /var/www/beyondsaas/
```

**Add a blog post:** Create `.astro` file in `src/pages/blog/` → update `src/pages/blog/index.astro` posts array → build → deploy.

**Create a new service page:** Use `ServiceDetail.astro` template component. Pass props for title, subtitle, description, features, process steps, CTA quote.

**Verify live:** `curl -sI "https://beyondsaas.ai/<path>/"` → expect HTTP 200.

---

*For full brand details, see the beyondsaas-brand-and-content-guidelines skill.*
