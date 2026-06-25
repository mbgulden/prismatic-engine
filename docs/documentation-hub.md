# Documentation Hub — Where Each Doc Lives

**Status:** Tier 2 decision (GRO-2036). Locked Jun 19 2026.
**Audience:** Anyone wondering "where should this doc go?" or "why can't I find that doc?"

## TL;DR decision rule

| Doc type | Canonical home | Why |
|----------|----------------|-----|
| **Canonical architecture** (cross-module, file paths, ports, secrets, "do NOT" rules) | **Linear Documents** | Linkable from issues; survives across machines; one place for source-of-truth |
| **Runbooks / recovery procedures** | `prismatic-engine/docs/runbook.md` and siblings | Lives with the code it documents; versioned together |
| **API / module references** | Docstrings + `prismatic-engine/docs/architecture.md` | Generated from source, never out of date |
| **Research journals / retrospective analysis** | `/home/ubuntu/work/Hermes-Research/` | Time-series, append-only; written by dedicated agents |
| **In-flight inventory / working state** | `prismatic-engine/in-flight-engine-inventory.md` | Hand-maintained; auto-update is a future concern |
| **Public-facing / hiring collateral** | Cloudflare Pages (`files.growthwebdev.com` via prismatic publisher) | CDN, linkable, doesn't require Linear access |
| **Per-profile runtime state** (chat IDs, active cron IDs, recent errors) | `~/.hermes/profiles/<name>/memories/MEMORY.md` | Read every turn; <2200 char cap; pointers only |
| **Per-task implementation notes** | Linear issue description + comments | Ephemeral; lives with the work |

## Why this split

The failure mode we're solving is **re-deriving architecture from source every time someone debugs**. We re-discovered the Linear webhook flow three separate times in one session (GRO-2033 brief). Each rediscovery spent 10-20 minutes on file spelunking.

The cap on `MEMORY.md` (2200 chars/profile, injected as system prompt) means we **cannot** stuff architecture there — it eats budget that should track current state. The right move is:

1. **Architecture** → Linear Documents (shareable, linkable, full-text searchable)
2. **Runtime state** → `MEMORY.md` (one-line pointers + chat IDs + active cron IDs)
3. **Runbooks + module refs** → `prismatic-engine/docs/` (versioned with the code)
4. **Working state** → Linear issues (with the work itself)
5. **Time-series / research** → Hermes-Research journals
6. **Public-facing** → Cloudflare Pages

## Decision: Linear Documents vs Notion vs Cloudflare Pages

**Linear Documents wins** for canonical architecture because:

- Already the team's primary workspace (every issue references them)
- Linear search indexes them
- Comments on issues can deep-link to docs
- `documentCreate(input: {issueId: ...})` ties docs to the work that produced them
- No new vendor relationship (Notion would add one)
- Cloudflare Pages is for *publishing* — architecture docs are internal references, not public

Cloudflare Pages is still right for **public-facing** material (architecture diagrams for hiring, blog posts, etc.). Use `prismatic-artifact-publisher` to push from `prismatic-engine/docs/public/` to `files.growthwebdev.com`.

## Decision: same-PR doc updates vs follow-up issue

**Same PR, when practical.** The rule:

- Bug fix that changes a flag, file path, or cron schedule → update the corresponding Linear Doc in the same change.
- New module / new architecture → file the Linear Doc in the original change.
- Doc clarification / typo / link rot → file a separate PR; doesn't need an issue.

**Exception:** Cross-team coordination (e.g. dispatcher changes that affect multiple docs) — open a tracking issue, link all touched docs.

## Sync mechanism (Tier 3, partially shipped)

**Current state:** Most docs are hand-maintained. Drift detection is informal (someone notices).

**Tier 3 plan** (filed in GRO-2033, not yet scheduled):

- Lint script `prismatic-engine/scripts/check_docs_fresh.sh` that:
  - For each doc claiming "X is at path Y", verifies path Y still exists
  - For each doc referencing a cron ID, verifies the cron is still active (`hermes cron list`)
  - For each doc with version numbers or model names, verifies the version is current
- Run in CI (`.github/workflows/docs-fresh.yml` if/when the engine repo gets GitHub Actions)
- On failure, opens a Linear issue `agent:ned` with the diff

**Until Tier 3 ships:** Doc authors must run a manual smoke check after editing. The webhook doc has a copy-pasteable test recipe at the bottom for exactly this reason.

## Failure modes

| When you can't find a doc, look here | Why |
|---------------------------------------|-----|
| Architecture / "how does X work" | Linear Documents first, then `prismatic-engine/docs/architecture.md` |
| "How do I recover from Y" | `prismatic-engine/docs/runbook.md` |
| "What was I working on?" | `prismatic-engine/in-flight-engine-inventory.md`, then Linear (assigned issues) |
| "What changed recently in module Z?" | Git log on `prismatic/` |
| "Why did we decide to do it this way?" | Hermes-Research journals + Linear issue threads |
| "What does agent N know?" | `/home/ubuntu/.hermes/profiles/<name>/memories/` (MEMORY.md + USER.md) |

## Open questions (for future-me)

1. Should `in-flight-engine-inventory.md` migrate to a Linear project (auto-update via API)?
2. Are the four Hermes-Research subdirs (`docs/`, `journals/`, `reports/`, `inbox/`) discoverable enough? Should there be an index doc?
3. The architecture doc is at 224 lines and growing. When does it split into per-module refs vs stay consolidated?
4. Cloudflare Pages docs site — do we need a search index, or is Linear good enough?

## Related

- **GRO-2033** — Documentation hub initiative (parent)
- **GRO-2035** — Memory vs docs boundary (companion; trimmed MEMORY.md to ≤1500)
- `prismatic-engine/docs/architecture.md` — Tier 4 engine map (links out, doesn't restate)
- `prismatic-engine/in-flight-engine-inventory.md` — Tier 3 inventory (hand-maintained)
- `/tmp/handoff_fred/brief.md` — Ned → Orchestrator handoff brief (Jun 19)