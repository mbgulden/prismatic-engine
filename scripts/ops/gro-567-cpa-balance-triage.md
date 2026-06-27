# GRO-567 — Roberts Hart CPA balance — Triage Note (Ned)

**Issue:** GRO-567 — Pay outstanding Roberts Hart CPA balance
**Triage owner:** Ned (infrastructure) — not the correct lane for execution
**Status as of 2026-06-27:** Blocked — requires human financial action

---

## Why this issue does not belong on Ned's queue

GRO-567 is a **revenue / human action** task:

- Pay a CPA firm ~$1,000+ (financial transaction; requires bank access)
- Contact Roberts Hart & Company to confirm filing-readiness vs. payment (external comms)
- Possibly negotiate a payment plan (negotiation requires human authority)

Ned's mandate is infrastructure monitoring (GPU/disk/GitHub/Cloudflare/swarm).
None of those surfaces can resolve this. I am flagging it and returning it to
the human lane, not fabricating code to "complete" the issue.

This is a hard "silence if you can't actually move it" moment per the autonomous
task doctrine: a no-op cron must not invent deliverables to look busy.

---

## What unblocks the issue (Michael's call)

1. **Locate the most recent Roberts Hart invoice / balance statement**
   - Source candidates: `~/Documents/Tax/`, `~/Documents/2025-Tax/`, Roberts Hart portal login, bank/CC statement search
   - Confirm the exact outstanding amount (the issue says "~$1,000+"; the exact figure matters)
2. **Contact Roberts Hart** (email/phone; this is from the issue description)
   - Ask: "Will you file 2025 returns before I pay the balance, or do you need payment / retainer first?"
   - If they need payment: pay the balance (or arrange a payment plan) and capture the confirmation number
3. **Close the issue** with: amount paid, payment method, confirmation, and the filing date they committed to

The downstream items in the queue (GRO-565 Q2 estimated taxes, GRO-564 re-engage / reconcile) are
all blocked on this one. Resolving GRO-567 unblocks the entire tax-vertical for the swarm.

---

## Operational observations (in case useful)

- This is issue #1 of 10 in the current Ned scan. The other 9 are marketing-site work
  (GRO-559 lead magnet, GRO-558 landing pages, GRO-557 Gumroad, GRO-545 social proof,
  GRO-543 lead magnet, GRO-542 contact flow, GRO-537 brand home) plus the two tax-adjacent
  items (GRO-565, GRO-564) and this one. The marketing work is a better lane fit for the
  Prismatic Engine coder agent (or the AGY coder lane); Ned is the wrong agent for all 10.
- If the scanner keeps routing these to Ned, the `agent:ned` label is being used as a
  default catch-all. Worth fixing in the prismatic scanner routing config so each issue
  goes to the right lane (Fred for strategy/spec, AGY/coder for build work, Michael for
  human-action items like this CPA payment).

---

## Action taken by Ned (this run)

- Acquired lock on `scripts/` lane
- Created branch `ned/GRO-567` from `origin/deploy-fresh`
- Read the issue end-to-end
- Wrote this triage note (the only deliverable that is truthful given the issue's nature)
- Will mark Linear as "needs human" and post a comment explaining the blocker

No fabricated code, no fake "task complete" stamp. Issue is genuinely unresolved and
needs Michael.
