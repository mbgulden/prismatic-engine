# GRO-558 — Build website landing and marketing pages — Triage Note (Ned)

**Issue:** GRO-558 — Build website landing and marketing pages
**Triage owner:** Ned (infrastructure) — not the correct lane for execution
**Status as of 2026-06-27:** Out of lane — needs a coder/designer, not an SRE

---

## Why this issue does not belong on Ned's queue

GRO-558 is a **content + design + build** task for the Belief Deprogrammer marketing site. From the Linear description:

> Create the main marketing website with landing page, features overview, how-it-works section, pricing page, and SEO-optimized structure to drive organic discovery and conversions.

Ned's mandate is **infrastructure monitoring** — GPU node health, disk usage, GitHub hygiene, Cloudflare deployment status, swarm agent health, prismatic engine lane hygiene. Ned does not own copy, design, page layout, feature copy, pricing tables, or SEO meta. The natural home for this work is the `belief-deprogrammer/landing/` repo (or whichever build agent owns the marketing-site deploy pipeline).

Writing infra scripts or stub Cloudflare Workers before the page copy, brand system, design tokens, and pricing tier decisions exist is invented work — there is nothing concrete to integrate against.

This is the same pattern as the GRO-559 triage (`scripts/ops/gro-559-email-capture-triage.md`, commit `bc86fc63`) and GRO-564 (`scripts/ops/gro-564-cpa-reengage-triage.md`). Ned flags these, hands them back to the right lane, and does not stamp "complete" on work that isn't actually done.

---

## Project context

- **Project:** Belief Deprogrammer (GrowthWebDev team)
- **Branch off:** `origin/deploy-fresh` (Prismatic Engine — canonical Ned lane)
- **Why not in `belief-deprogrammer` repo?** The marketing-site landing pages live in the Belief Deprogrammer repo (`/home/ubuntu/work/belief-deprogrammer/landing/` is the existing landing surface). Ned does not own copy/design decisions for that project. Any infra-side script that supports this work (Cloudflare Pages deployment health check, form-POST worker if a contact/email-capture form is later added, DNS/SSL monitoring for the marketing domain) belongs in `prismatic-engine/scripts` so it ships with the engine + Ned's daily sweep pipeline — but only after the upstream copy/design/asset decisions exist.

---

## What unblocks the issue (right lane)

The right lane for GRO-558 is the **coder / designer / copywriter**, not Ned. Concretely:

1. **Designer lane** (Belief Deprogrammer design owner)
   - Brand system, design tokens, page layouts for landing / features / how-it-works / pricing
   - Visual hierarchy, image/video asset curation, responsive breakpoints
2. **Copywriter lane** (Fred/strategy or Michael directly)
   - Landing-page headline + subhead, feature descriptions, how-it-works narrative, pricing tier copy and CTAs, SEO title/meta/OG tags, structured-data (Organization, Product, FAQPage) for organic discovery
3. **Coder / build-agent lane** (AGY coder lane or the Belief Deprogrammer build agent)
   - Build the pages (Astro/Next/etc., whichever the repo uses), wire forms, deploy via Cloudflare Pages, set up redirects, configure sitemap + robots.txt, verify Core Web Vitals
4. **Ned's lane (downstream, only after the above ships)**
   - Add the marketing-domain URL to Ned's daily Cloudflare Pages deployment health check
   - Add DNS / SSL expiry monitoring for the marketing domain
   - Add disk / NAS watch if the asset pipeline writes large files locally
   - Verify Cloudflare Pages deployment is live after the coder's first ship

None of those Ned-side tasks are ready to execute yet — they depend on the upstream copy + design + page builds existing first.

---

## Sibling issues — same triage pattern applies

GRO-558 is one of **10 issues** currently labeled `agent:ned` in Todo state. The other 9 are all equally out of lane. Grouped by their actual correct lane:

| Issue | Title | Actual lane |
| --- | --- | --- |
| GRO-558 | Build website landing and marketing pages | Coder / designer (Belief Deprogrammer) — this issue |
| GRO-557 | Create Gumroad product page and checkout flow | Coder / copywriter (Belief Deprogrammer) |
| GRO-545 | Add Social Proof and Testimonials section | Coder / designer (Beyond SaaS) |
| GRO-543 | Create Lead Magnet and Email Capture system | Coder / copywriter (Beyond SaaS) |
| GRO-542 | Implement Contact and Booking flow | Coder / integrations (Beyond SaaS) |
| GRO-537 | Design and build brand home page | Designer / coder (Beyond SaaS) |
| GRO-512 | PHASE 2: Paid Launch — Cohort 1, $997/person | Launch ops / PM |
| GRO-511 | PHASE 2: Beta Launch — 5 Students, Free, Heavy Feedback | Launch ops / PM |
| GRO-510 | PHASE 2: Record Bootcamp Video Content | Producer / video |
| GRO-509 | PHASE 2: Build Community Platform MVP | Coder / integrations |

(Plus the two already-triaged: GRO-564 CPA call and GRO-567 CPA balance — both human-action.)

**Routing recommendation:** the Prismatic Engine scanner is using `agent:ned` as a default catch-all for any GrowthWebDev marketing/launch task that does not have a more specific agent label. Worth fixing in the scanner routing config (or the team-level labels) so:

- Marketing / copy / build → coder lane (AGY, Jules, or the project repo's build agent)
- Human action (calls, payments, sign-offs) → Michael
- Launch ops / coordination → Fred or a PM lane
- Ned stays reserved for: GPU/disk/Tailscale/CF/swarm/agent-fleet/prismatic-engine hygiene

---

## Action taken by Ned (this run)

- Acquired lock on `scripts/ops` lane (this is a triage-only run; no source/branch changes outside `scripts/ops/`)
- Created branch `ned/GRO-558` from `origin/deploy-fresh`
- Read the issue end-to-end (title, description, state, labels, project)
- Read prior triage pattern from GRO-559 (`scripts/ops/gro-559-email-capture-triage.md`) so this note matches the established format
- Catalogued the full 10-issue `agent:ned` backlog and grouped each by the correct lane so the next routing config change has a ready-made map
- Wrote this triage note (the only truthful deliverable given the issue's nature)
- Will run `finalize_task.sh GRO-558 ned/GRO-558 ned` to commit, unlock, and post the triage note as a Linear comment for Michael

No fabricated HTML, no fake Astro/Next scaffold, no "task complete" stamp on a marketing deliverable. The triage note itself is the deliverable: it routes the work to the right lane and surfaces the scanner-routing bug.

---

## Operational follow-ups Ned can pick up unprompted

While not blocking on the 10 marketing items, here are infrastructure-side items Ned *can* and should do without being asked:

1. **Cloudflare Pages health check** for `belief-deprogrammer.com` (or whatever marketing domain the build agent deploys to) — add to Ned's daily sweep once a Pages project exists
2. **DNS / SSL expiry check** for both projects' marketing domains — Cloudflare API token should already be available in Ned's profile
3. **Disk + NAS check** on Hermes VM before/after any large landing-page asset (hero image, explainer video, logo set) gets dropped into `belief-deprogrammer/landing/assets/` — flag if the asset pipeline writes outside `landing/assets/` and bloats the repo
4. **Swarm lane-lock sweep** — verify the `agent:ned` scanner routing config isn't silently blocking other agents on the same files

Will surface these as separate cron findings rather than rolling them into the GRO-558 comment thread.