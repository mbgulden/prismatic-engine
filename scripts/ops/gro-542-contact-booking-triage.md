# GRO-542 — Implement Contact and Booking flow — Triage Note (Ned)

**Issue:** GRO-542 — Implement Contact and Booking flow
**Triage owner:** Ned (infrastructure) — not the correct lane for execution
**Status as of 2026-06-27:** Out of lane — needs a coder/integrations (calendar + email automation), not an SRE
**Branch:** `ned/GRO-542` (triage-only run, no source changes outside `scripts/ops/`)

---

## Why this issue does not belong on Ned's queue

GRO-542 is a **form UX + calendar integration + email automation** task for the Beyond SaaS — Consulting Brand marketing site. From the Linear description:

> Build the contact form and booking/scheduling flow with calendar integration, intake questionnaire, and automated confirmation and follow-up email sequences.

Ned's mandate is **infrastructure monitoring** — GPU node health, disk usage, GitHub hygiene, Cloudflare deployment status, swarm agent health, prismatic-engine lane hygiene. Ned does not own marketing copy, contact-form UX design, intake questionnaire field design, calendar provider selection, or the email sequence copy. The natural home for this work is the Beyond SaaS consulting site build agent (coder lane — AGY, Jules, or the project repo's build agent).

Writing an infra script or stubbing a Cloudflare Worker for a contact-form POST before the form fields, intake questionnaire copy, calendar provider decision, and email-sequence copy exist is invented work — there is nothing concrete to integrate against. The previous Ned runs (r54, r55, today's run at ~22:15 UTC) already correctly dequeued this with the same disposition.

This is the **same triage pattern** that already exists in this repo for sibling issues:

- `scripts/ops/gro-545-social-proof-triage.md` (commit `4a349797`)
- `scripts/ops/gro-558-landing-pages-triage.md` (commit `a4f6f52e`) — full sibling-issues map covering the 10-issue `agent:ned` backlog
- `scripts/ops/gro-559-email-capture-triage.md` (commit `bc86fc63`)
- `scripts/ops/gro-564-cpa-reengage-triage.md`

GRO-542 sits in the same Beyond SaaS marketing/launch epic as GRO-509, GRO-510, GRO-511, GRO-512, GRO-537, GRO-538, GRO-543, GRO-545, GRO-558, GRO-559. All 10 have been previously triaged as out-of-lane for Ned.

---

## What unblocks the issue (right lane)

The right lane for GRO-542 is the **coder / designer / integrations engineer**, not Ned. Concretely:

1. **Designer lane** (Beyond SaaS design owner)
   - Contact form field set, validation UX, error-state messaging
   - Intake questionnaire flow + branching logic
   - Booking confirmation page layout, calendar embed vs. native scheduler decision
2. **Copywriter lane** (Fred/strategy or Michael directly)
   - Form labels, placeholder text, success-page copy
   - Intake questionnaire prompts (the *content* of what to ask, not the form fields themselves)
   - Confirmation email copy + follow-up sequence cadence (Day 0, Day 1, Day 3, Day 7 templates)
3. **Integrations lane** (coder — calendar provider + ESP wiring)
   - Calendar provider choice (Cal.com, Calendly, SavvyCal, etc.) + API key provisioning
   - ESP choice for confirmations/follow-ups (ConvertKit, Resend, Postmark, etc.) — note GRO-2307 already covers the ConvertKit setup, which is a partial dependency
   - Webhook handler that books → writes CRM row → fires confirmation email
4. **Asset/data lane** (Michael)
   - Decision on which calendar provider the brand uses (impacts embed code + styling)
   - Decision on timezone handling + business-hours policy
   - Decision on what CRM the booking rows write to

Only after those four lanes converge is there actual work Ned could help with on the infra side:

- **Cloudflare Worker health check** for the form-POST endpoint (Ned writes, designer copies the field contract)
- **DNS / SPF / DKIM monitoring** for the ESP sending domain (Ned writes, once ESP is picked)
- **Deploy health check** for the new contact/booking page once it ships (Ned daily sweep)

But none of those infra hooks are executable *yet* — they require the upstream form/calendar/ESP decisions to exist first.

---

## Project context

- **Project:** Beyond SaaS — Consulting Brand (GrowthWebDev team)
- **Issue state:** Todo (per Ned's prior r55 triage; scanner keeps re-dispatching because the label hasn't been removed)
- **Branch off:** `origin/deploy-fresh` (Prismatic Engine — canonical Ned lane)
- **Why not in `prismatic-engine` source?** The marketing site for Beyond SaaS lives in its own build-agent repo (or the consultant's deployment surface). Ned does not own form-field design, calendar provider selection, or ESP wiring decisions for that project. Any infra-side script that supports this work — Cloudflare Pages deployment health check, form-POST worker if a future testimonial-submission form is added, DNS/SSL monitoring for the marketing domain — belongs in `prismatic-engine/scripts` so it ships with the engine + Ned's daily sweep pipeline. But only after the upstream copy/design/integration decisions exist.

---

## Full `agent:ned` backlog — current 10-issue set

This is the same 10-issue backlog already mapped on GRO-558 and GRO-545. Repeating it here for the Linear audit trail:

| Issue | Title | Correct lane |
|---|---|---|
| GRO-509 | PHASE 2: Build Community Platform MVP | Coder / PM (Community platform build) |
| GRO-510 | PHASE 2: Record Bootcamp Video Content | Video production / Michael (human action) |
| GRO-511 | PHASE 2: Beta Launch — 5 Students | Launch ops / Fred + Michael (human coordination) |
| GRO-512 | PHASE 2: Paid Launch — Cohort 1 | Launch ops / Fred + Michael (revenue-critical, human action) |
| GRO-537 | Design and build brand home page | Designer / coder (landing page) |
| **GRO-542** | **Implement Contact and Booking flow** | **Coder / integrations (calendar + email automation)** |
| GRO-543 | (sibling — TBD) | TBD |
| GRO-545 | Add Social Proof and Testimonials section | Designer / copywriter |
| GRO-558 | Build website landing and marketing pages | Designer / coder |
| GRO-559 | Set up Email Capture and Lead Magnet system | Coder / ESP integration |

**Routing recommendation** (re-stated from GRO-558 / GRO-545): the Prismatic Engine scanner is using `agent:ned` as a default catch-all for any GrowthWebDev marketing/launch task that does not have a more specific agent label. Worth fixing in the scanner routing config (or the team-level labels) so:

- Marketing / copy / build → coder lane (AGY, Jules, or the project repo's build agent)
- Human action (calls, payments, sign-offs) → Michael
- Launch ops / coordination → Fred or a PM lane
- Ned stays reserved for: GPU/disk/Tailscale/CF/swarm/agent-fleet/prismatic-engine hygiene

---

## Action taken by Ned (this run)

- Acquired lock on `scripts/ops` lane (this is a triage-only run; no source/branch changes outside `scripts/ops/`)
- Created branch `ned/GRO-542` from `origin/deploy-fresh`
- Read the issue end-to-end (title, description, state, labels, project) and reviewed the prior triage comments already on the thread (this is the 6th Ned triage of the same issue — the routing recommendation has been stable since r33)
- Reused the established triage note format from `gro-545-social-proof-triage.md` so this note matches the sibling triage set
- Catalogued the full 10-issue `agent:ned` backlog and confirmed the lane mapping is unchanged since the GRO-558 / GRO-545 triages
- Wrote this triage note (the only truthful deliverable given the issue's nature)
- Will run `finalize_task.sh GRO-542 ned/GRO-542 ned` to commit, unlock, and post the triage note as a Linear comment for Michael

No fabricated form scaffold, no fake calendar webhook handler, no "task complete" stamp on a marketing deliverable. The triage note itself is the deliverable: it routes the work to the right lane and re-surfaces the scanner-routing bug (now the 6th time on this same issue).

---

## Operational follow-ups Ned can pick up unprompted

While not blocking on the 10 marketing items, here are infrastructure-side items Ned *can* and should do without being asked:

1. **Cloudflare Pages health check** for the Beyond SaaS marketing domain — add to Ned's daily sweep once a Pages project exists
2. **DNS / SSL expiry check** for both projects' marketing domains — Cloudflare API token should already be available in Ned's profile
3. **Disk + NAS check** on Hermes VM before/after any large landing-page asset (hero image, explainer video, logo set, video testimonial) gets dropped into the build repo — flag if the asset pipeline writes outside the canonical assets dir and bloats the repo
4. **Swarm lane-lock sweep** — verify the `agent:ned` scanner routing config isn't silently blocking other agents on the same files. The fact that Ned keeps getting dispatched against the same 10 marketing issues every ~12h suggests the dequeue signal isn't propagating back to the scanner
5. **Open GRO-2307 (ConvertKit setup)** if it actually does the email-side plumbing that GRO-542's confirmation/follow-up sequence will need — that's a legit Ned lane if ConvertKit integration lands in `prismatic-engine/plugins/`. Worth checking if GRO-2307 is still actionable.

Will surface these as separate cron findings rather than rolling them into the same triage doc.

---

## Escalation

None. This is a normal-priority marketing/integration task in another agent's lane, not revenue-critical or time-blocked. Michael has already triaged this issue multiple times himself (per the prior comments). The right action is for Michael (or the orchestrator) to drop the `agent:ned` label and re-label to `agent:fred` (strategy) or `agent:agy` (code-heavy build) so the issue reaches the right worker on its next scanner pass.