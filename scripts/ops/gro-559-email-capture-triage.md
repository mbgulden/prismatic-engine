# GRO-559 — Set up Email Capture and Lead Magnet system — Triage Note (Ned)

**Issue:** GRO-559 — Set up Email Capture and Lead Magnet system
**Triage owner:** Ned (infrastructure) — not the correct lane for execution
**Status as of 2026-06-27:** Out of lane — needs a coder/designer, not an SRE

---

## Why this issue does not belong on Ned's queue

GRO-559 is a **content + marketing build** task, not infrastructure. From the Linear description:

- Design a lead magnet (sample deprogramming session, framework PDF, or mini-course)
- Build opt-in landing pages
- Set up automated email nurture sequences for lead conversion

Ned's mandate is **infrastructure monitoring** — GPU node health, disk usage, GitHub hygiene, Cloudflare deployment status, swarm agent health, prismatic engine lane hygiene. The pieces Ned *could* plausibly touch (Cloudflare Workers for the form POST endpoint, DNS records, email delivery DNS) are downstream plumbing that depends on the upstream copy and design being decided first.

Writing a worker endpoint before the form fields, lead magnet asset, and nurture copy exist is invented work — there is nothing to integrate. There is also no design system or asset pipeline for the lead magnet itself, so anything I "built" would be a stub masquerading as a deliverable.

This is the same pattern as GRO-564 (Roberts Hart CPA re-engagement — `scripts/ops/gro-564-cpa-reengage-triage.md`, commit `5e4368c1`) and GRO-567 (CPA balance payment, prior triage). Ned flags these, hands them back to the right lane, and does not stamp "complete" on work that isn't actually done.

---

## Project context

- **Project:** Belief Deprogrammer (GrowthWebDev team)
- **Branch off:** `origin/deploy-fresh` (Prismatic Engine — the canonical Ned lane)
- **Why not in `belief-deprogrammer` repo?** The marketing-site landing pages live there but Ned does not own copy/design decisions, and any infra-side script (Cloudflare Worker for the POST, Resend/Postmark webhook handler, etc.) belongs in prismatic-engine/scripts so it ships with the engine + watcher pipeline.

---

## What unblocks the issue (right lane)

The right lane for GRO-559 is the **coder / designer / copywriter**, not Ned. Concretely:

1. **Copywriter lane** (Fred/strategy or Michael directly)
   - Decide the lead magnet format: sample deprogramming session recording, framework PDF, or short mini-course
   - Draft the opt-in landing page copy and the 5-7 step email nurture sequence
   - Decide the conversion goal (booked call? purchase? waitlist?) and the magnet-to-conversion funnel
2. **Designer / coder lane** (AGY coder lane or the Belief Deprogrammer build agent)
   - Produce the lead magnet asset (record audio, export PDF, build the mini-course module)
   - Build the opt-in landing page (the existing `landing/` repo at `/home/ubuntu/work/belief-deprogrammer/landing/` is the natural home)
   - Wire the form POST → email platform (ConvertKit, Beehiiv, Resend audience, etc.)
3. **Ned's lane (downstream, only after the above ships)**
   - Add the Cloudflare Worker / Pages function that handles form POST and validates hCaptcha or similar
   - Verify DNS records (SPF/DKIM/DMARC) on whichever domain handles the transactional emails
   - Add a Cloudflare deployment health check to Ned's daily sweep for the lead-magnet landing URL
   - Add a NAS / disk-space watch if the lead-magnet asset pipeline writes large files locally

None of those Ned-side tasks are ready to execute yet — they depend on the upstream copy + asset + form fields being decided.

---

## Sibling issues — same triage pattern applies

GRO-559 is one of **10 issues** currently labeled `agent:ned`. The other 9 are all equally out of lane. Grouped by their actual correct lane:

| Issue | Title | Actual lane |
| --- | --- | --- |
| GRO-559 | Set up Email Capture and Lead Magnet system | Coder / copywriter (Belief Deprogrammer) |
| GRO-558 | Build website landing and marketing pages | Coder / designer (Belief Deprogrammer) |
| GRO-557 | Create Gumroad product page and checkout flow | Coder / copywriter (Belief Deprogrammer) |
| GRO-545 | Add Social Proof and Testimonials section | Coder / designer (Beyond SaaS) |
| GRO-543 | Create Lead Magnet and Email Capture system | Coder / copywriter (Beyond SaaS) |
| GRO-542 | Implement Contact and Booking flow | Coder / integrations (Beyond SaaS) |
| GRO-537 | Design and build brand home page | Designer / coder (Beyond SaaS) |
| GRO-512 | PHASE 2: Paid Launch — Cohort 1, $997/person | Launch ops / PM |
| GRO-511 | PHASE 2: Beta Launch — 5 Students, Free, Heavy Feedback | Launch ops / PM |
| GRO-510 | PHASE 2: Record Bootcamp Video Content | Producer / video |

(Plus the two already-triaged: GRO-564 CPA call and GRO-567 CPA balance — both human-action.)

**Routing recommendation:** the Prismatic Engine scanner is using `agent:ned` as a default catch-all. Worth fixing in the scanner routing config so:
- Marketing / copy / build → coder lane (AGY, Jules, or the project repo's build agent)
- Human action (calls, payments, sign-offs) → Michael
- Launch ops / coordination → Fred or a PM lane
- Ned stays reserved for: GPU/disk/Tailscale/CF/swarm/agent-fleet/prismatic-engine hygiene

---

## Action taken by Ned (this run)

- Acquired lock on `scripts/ops` lane
- Created branch `ned/GRO-559` from `origin/deploy-fresh` (current HEAD on `ned/GRO-559`)
- Read the issue end-to-end (title, description, state, labels, project, parent context)
- Read prior triage pattern from GRO-564 (`scripts/ops/gro-564-cpa-reengage-triage.md`) so this note matches the established format
- Catalogued the full 10-issue `agent:ned` backlog and grouped each by the correct lane so the next routing config change has a ready-made map
- Wrote this triage note (the only truthful deliverable given the issue's nature)
- Will run `finalize_task.sh GRO-559 ned/GRO-559 ned` to commit, unlock, and post the triage note as a Linear comment for Michael

No fabricated code, no fake Cloudflare Worker stub, no "task complete" stamp on a marketing deliverable. The triage note itself is the deliverable: it routes the work to the right lane and surfaces the scanner-routing bug.

---

## Operational follow-ups Ned can pick up unprompted

While not blocking on the 10 marketing items, here are infrastructure-side items Ned *can* and should do without being asked:

1. **Cloudflare tunnel + Pages health check** for `belief-deprogrammer.com` (if a Pages deployment exists) and the Beyond SaaS consulting domain — add to Ned's daily sweep
2. **DNS / SSL expiry check** for both projects' domains — Cloudflare API token should already be available in Ned's profile
3. **Disk + NAS check** on Hermes VM before/after any large lead-magnet asset (PDF, video, audio) gets dropped into `belief-deprogrammer/landing/assets/` — flag if the asset pipeline writes outside `landing/assets/` and bloats the repo
4. **Swarm lane-lock sweep** — verify the `agent:ned` scanner routing config isn't silently blocking other agents on the same files

Will surface these as separate cron findings rather than rolling them into the GRO-559 comment thread.