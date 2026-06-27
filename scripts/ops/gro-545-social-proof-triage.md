# GRO-545 — Add Social Proof and Testimonials section — Triage Note (Ned)

**Issue:** GRO-545 — Add Social Proof and Testimonials section
**Triage owner:** Ned (infrastructure) — not the correct lane for execution
**Status as of 2026-06-27:** Out of lane — needs a coder/designer, not an SRE
**Branch:** `ned/GRO-545` (triage-only run, no source changes outside `scripts/ops/`)

---

## Why this issue does not belong on Ned's queue

GRO-545 is a **content + design + build** task for the Beyond SaaS — Consulting Brand marketing site. From the Linear description:

> Design and build a dynamic testimonials section with client quotes, video testimonials, logos, and case study links that can be displayed across the site.

Ned's mandate is **infrastructure monitoring** — GPU node health, disk usage, GitHub hygiene, Cloudflare deployment status, swarm agent health, prismatic-engine lane hygiene. Ned does not own marketing copy, testimonial asset collection, video testimonials, logo set curation, or the page layout for a social-proof section. The natural home for this work is the Beyond SaaS consulting site build agent (coder lane — AGY, Jules, or the project repo's build agent).

Writing an infra script or stubbing a Cloudflare Worker for a dynamic testimonials component before the testimonial copy, brand-approved logo set, video testimonial assets, and case-study link targets exist is invented work — there is nothing concrete to integrate against.

This is the **same triage pattern** that already exists in this repo:
- `scripts/ops/gro-558-landing-pages-triage.md` (commit `a4f6f52e`) — full sibling-issues map covering all 10 currently-misrouted `agent:ned` issues
- `scripts/ops/gro-559-email-capture-triage.md` (commit `bc86fc63`)
- `scripts/ops/gro-564-cpa-reengage-triage.md`

GRO-545 is item #5 on that table. The triage has been posted to the Linear comment thread 4 times already (r19 audit at 2026-06-26T16:02:08Z, then 2026-06-27T04:26:08Z, 2026-06-27T12:39:13Z, 2026-06-27T17:25:42Z) — Ned's prior runs have correctly dequeued this each time.

---

## Project context

- **Project:** Beyond SaaS — Consulting Brand (GrowthWebDev team)
- **Branch off:** `origin/deploy-fresh` (Prismatic Engine — canonical Ned lane)
- **Why not in `prismatic-engine` source?** The marketing site for Beyond SaaS lives in its own build-agent repo (or the consultant's deployment surface). Ned does not own copy/design/page-build decisions for that project. Any infra-side script that supports this work — Cloudflare Pages deployment health check, form-POST worker if a future testimonial-submission form is added, DNS/SSL monitoring for the marketing domain — belongs in `prismatic-engine/scripts` so it ships with the engine + Ned's daily sweep pipeline. But only after the upstream copy/design/asset decisions exist.

---

## What unblocks the issue (right lane)

The right lane for GRO-545 is the **coder / designer / copywriter**, not Ned. Concretely:

1. **Designer lane** (Beyond SaaS design owner)
   - Testimonials section visual hierarchy, layout, responsive breakpoints
   - Logo wall design system, video testimonial player UX
   - Carousel/grid pagination and accessibility pattern
2. **Copywriter lane** (Fred/strategy or Michael directly)
   - Testimonial copy — short quote (≤25 words) and long-form pull-quote versions
   - Case-study link targets (specific published case studies with URLs to point to)
   - Video testimonial scripts / talking points if any new testimonials need to be recorded
3. **Asset collector lane** (marketing ops or Michael)
   - Client logos (SVG/PNG with brand-approval), case study PDF/links
   - Video testimonial files (recorded, captioned, muxed)
   - Permissions/releases for each testimonial use
4. **Coder / build-agent lane** (AGY coder lane or the Beyond SaaS build agent)
   - Build the dynamic testimonials component (carousel or grid), wire the data source (CMS, JSON, or MDX), deploy via Cloudflare Pages, verify Core Web Vitals and lazy-loaded video playback
5. **Ned's lane (downstream, only after the above ships)**
   - Add the Beyond SaaS marketing URL to Ned's daily Cloudflare Pages deployment health check
   - Add DNS / SSL expiry monitoring for the marketing domain
   - Disk / NAS watch if video testimonial assets get dropped into a local build pipeline
   - Verify Cloudflare Pages deployment is live after the coder's first ship

None of those Ned-side tasks are ready to execute yet — they depend on the upstream copy + design + asset + page-build existing first.

---

## Sibling issues — same triage pattern applies

GRO-545 is one of **10 issues** currently labeled `agent:ned` in Todo state. The other 9 are all equally out of lane. Grouped by their actual correct lane (full table in `gro-558-landing-pages-triage.md`):

| Issue | Title | Actual lane |
| --- | --- | --- |
| GRO-545 | Add Social Proof and Testimonials section | Coder / designer (Beyond SaaS) — this issue |
| GRO-543 | Create Lead Magnet and Email Capture system | Coder / copywriter (Beyond SaaS) |
| GRO-542 | Implement Contact and Booking flow | Coder / integrations (Beyond SaaS) |
| GRO-537 | Design and build brand home page | Designer / coder (Beyond SaaS) |
| GRO-512 | PHASE 2: Paid Launch — Cohort 1, $997/person | Launch ops / PM (AI Consultant Bootcamp) |
| GRO-511 | PHASE 2: Beta Launch — 5 Students, Free, Heavy Feedback | Launch ops / PM (AI Consultant Bootcamp) |
| GRO-510 | PHASE 2: Record Bootcamp Video Content | Producer / video (AI Consultant Bootcamp) |
| GRO-509 | PHASE 2: Build Community Platform MVP | Coder / integrations (AI Consultant Bootcamp) |
| GRO-508 | PHASE 2: Build HD Personalization Engine | Product / data (AI Consultant Bootcamp) |
| GRO-507 | PHASE 2: Design Multi-Type Curriculum Architecture | Curriculum / design (AI Consultant Bootcamp) |

**Routing recommendation** (re-stated from GRO-558): the Prismatic Engine scanner is using `agent:ned` as a default catch-all for any GrowthWebDev marketing/launch task that does not have a more specific agent label. Worth fixing in the scanner routing config (or the team-level labels) so:

- Marketing / copy / build → coder lane (AGY, Jules, or the project repo's build agent)
- Human action (calls, payments, sign-offs) → Michael
- Launch ops / coordination → Fred or a PM lane
- Ned stays reserved for: GPU/disk/Tailscale/CF/swarm/agent-fleet/prismatic-engine hygiene

---

## Action taken by Ned (this run)

- Acquired lock on `scripts/ops` lane (this is a triage-only run; no source/branch changes outside `scripts/ops/`)
- Created branch `ned/GRO-545` from `origin/deploy-fresh`
- Read the issue end-to-end (title, description, state, labels, project) and reviewed the 4 prior triage comments already on the thread (this is the 5th Ned triage of the same issue — the routing recommendation has been stable)
- Reused the established triage note format from `gro-558-landing-pages-triage.md` so this note matches the sibling triage set
- Catalogued the full 10-issue `agent:ned` backlog and confirmed the lane mapping is unchanged since the GRO-558 triage
- Wrote this triage note (the only truthful deliverable given the issue's nature)
- Will run `finalize_task.sh GRO-545 ned/GRO-545 ned` to commit, unlock, and post the triage note as a Linear comment for Michael

No fabricated Astro/Next scaffold, no fake dynamic component, no "task complete" stamp on a marketing deliverable. The triage note itself is the deliverable: it routes the work to the right lane and re-surfaces the scanner-routing bug (now the 5th time).

---

## Operational follow-ups Ned can pick up unprompted

While not blocking on the 10 marketing items, here are infrastructure-side items Ned *can* and should do without being asked:

1. **Cloudflare Pages health check** for the Beyond SaaS marketing domain — add to Ned's daily sweep once a Pages project exists
2. **DNS / SSL expiry check** for both projects' marketing domains — Cloudflare API token should already be available in Ned's profile
3. **Disk + NAS check** on Hermes VM before/after any large landing-page asset (hero image, explainer video, logo set, video testimonial) gets dropped into the build repo — flag if the asset pipeline writes outside the canonical assets dir and bloats the repo
4. **Swarm lane-lock sweep** — verify the `agent:ned` scanner routing config isn't silently blocking other agents on the same files. The fact that Ned keeps getting dispatched against the same 10 marketing issues every ~12h suggests the dequeue signal isn't propagating back to the scanner

Will surface these as separate cron findings rather than rolling them into the GRO-545 comment thread (which is already long enough).
