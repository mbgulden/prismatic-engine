# Memory Consolidation Under Pressure

**Sessions:** June 9, 2026 — Darius Star

## The Problem

When memory is near the 2,200-char limit and the user provides a directive that MUST be preserved (story directive, gameplay mechanic, creative vision), you need to free space quickly.

## The Pattern

1. **Audit existing entries** — which ones are verbose? Look for long entries that can be trimmed without losing meaning.

2. **Consolidate, don't delete** — compress verbose entries to their essentials. Remove adjectives, explanatory phrases, and redundant qualifiers. Keep the facts.

3. **Priority order for consolidation:**
   - Infrastructure entries (NAS paths, Linear IDs, tool configs) — trimmer is better
   - Profile entries (user details) — keep preferences, drop fluff
   - Project entries — keep directives, drop session artifacts

4. **Proven consolidations (June 9, 2026):**
   - GCP entry: 507→200 chars (removed credit amounts, explanatory phrases)
   - NAS entry: 423→98 chars (removed Synology search tips, re-auth instructions)
   - Michael profile: 277→138 chars (removed burnout context, consulting thesis detail)

5. **Adding new entries:** After each consolidation, immediately add the critical entry before the freed space is consumed by other operations.

6. **What NOT to trim:**
   - User corrections and preferences
   - Active directives ("pull-out mechanic", "3 endings")
   - Chat IDs and credentials
   - Tool quirks that cause silent failures

## Session Example

User gave a massive story directive. Memory at 2,077/2,200 (123 chars free). Directive needed ~640 chars.

Steps:
1. Compressed GCP entry (507→200, freed 307)
2. Compressed NAS entry (423→98, freed 325)
3. Compressed Michael entry (277→138, freed 139)
4. Total freed: 771 chars → added directive (645 chars)

**Result:** 8 entries preserved, story directive saved without losing any critical facts.
