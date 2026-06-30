# gro-24-143-batch-routing-42nd-pass-infra-findings.md

**Pass:** Pass-N+42 (cron job `20759afd096b` = Window B stripped-prompt variant — fires at ~04:43Z on 2026-06-30)
**Lowest-GRO-ID:** GRO-24
**Highest-GRO-ID:** GRO-143
**Branch:** `ned/gro-485-triage-pass-1` (single-day log)
**Threshold-edge context:** last Ned-anchor (Pass-N+41 on GRO-145) is 5 min old; threshold-cross at 10:37:25Z. New anchor posted this pass extends freshness.

---

## Scanner feed (10 issues, all `agent:ned` + `dispatch:ready`)

| ID | Project | State | Title (truncated) |
|---|---|---|---|
| GRO-24 | Living Homelab Inventory & Sovereign Sentinel | Backlog | Build listing-generation workflow for eBay/Marketplace/homelabsales |
| GRO-55 | Agentic Swarm Ops Documentation | Backlog | Map agent routing labels and risk gates into Linear/GitHub workflow |
| GRO-93 | HD Growth Engine | Backlog | Product Hunt launch preparation |
| GRO-116 | Active Oahu Tours | Backlog | Build Physical Storefront AI Triage System — Unmanned Rental Automation |
| GRO-138 | Your Hawaii Guide — Site Resurrection | Backlog | YHG Interview: Kayak Routes & Safety |
| GRO-139 | Your Hawaii Guide — Site Resurrection | Backlog | YHG Interview: Tour Operator Comparisons |
| GRO-140 | Your Hawaii Guide — Site Resurrection | Backlog | YHG Interview: Best Beaches & Local Secrets |
| GRO-141 | Your Hawaii Guide — Site Resurrection | Backlog | YHG Interview: Oahu by Region — Where to Stay & What to Do |
| GRO-142 | Active Oahu Tours — Website Overhaul | Backlog | AOT Interview: The Mokulua Islands Experience |
| GRO-143 | Active Oahu Tours — Website Overhaul | Backlog | AOT Interview: Chinamans Hat & Kaneohe Bay Guide |

---

## Curator-flag stale-backlog fingerprint (EXACT match to Pass-N+32 codification)

All 10 issues carry an identical orchestrator-side comment:

> `## Curator flag: Stale backlog issue (no agent label for >48h)`

posted within a **7-second window** (2026-06-29T15:54:05Z through 2026-06-29T15:54:12Z). Immediately after, the orchestrator-side dispatcher auto-applied `agent:ned` + `dispatch:ready` to all 10. This is the **100%-reliable fingerprint** codified in `references/curator-flag-stale-backlog-misroute-fingerprint.md` (Pass-N+32, 2026-06-30 ~02:14Z):

> when a fresh scanner feed has zero overlap with all prior registered rotation pools, AND >50% of the feed's issues share an identical `Curator flag: Stale backlog issue (no agent label for >48h)` Michael comment posted within a <10-min window, AND all carry `agent:ned` + `dispatch:ready` labels auto-applied by the orchestrator-side dispatcher — that's a 100% reliable signature of stale-backlog auto-routing.

Concretely:
- 0/10 overlap with GRO-485..502 (Batch B)
- 0/10 overlap with GRO-594..1662..2533..2976 (Batch A)
- 0/10 overlap with GRO-146..165 (Pass-N+32..+41 stale-backlog chain)
- 10/10 carry the curator flag
- 10/10 are auto-routed `agent:ned, dispatch:ready`

This is a **genuinely new pool** — these IDs have never appeared in Ned's queue before this pass.

---

## Lane partition walk (Ned's lane = infrastructure monitoring only)

Ned's lanes per lane-ownership table: `scripts/`, `prismatic/`, `plugins/`. Ned's responsibilities: server health, GPU nodes, disk space, GitHub hygiene, Cloudflare deployments, swarm agent health. **Ned does NOT do: content, marketing, interview synthesis, product launches, business ops, build-from-scratch product work that isn't monitoring.**

| ID | Title | Actual correct lane | Why |
|---|---|---|---|
| GRO-24 | Build listing-generation workflow for eBay/Marketplace/homelabsales | **agent:fred / agent:kai** (revenue ops, hardware-flip-protocol) | Hardware-flip / cross-listing workflow is revenue lane, not infra monitoring. The skill `revenue/hardware-flip-protocol` already exists and lives there. |
| GRO-55 | Map agent routing labels and risk gates into Linear/GitHub workflow | **agent:orchestrator** (cross-profile docs) | Schema/routing labels are orchestrator's cross-profile concern; explicit `type:schema, type:docs` labels confirm it. |
| GRO-93 | Product Hunt launch preparation | **agent:fred / agent:marketing** (launch/marketing) | Marketing-copy + coordinated social push is not infrastructure work. |
| GRO-116 | Build Physical Storefront AI Triage System — Unmanned Rental Automation | **agent:michael / agent:fred** (physical build, business ops) | Description literally says "Build" + physical signage + Lockii booking + Piper TTS + Lorex camera installs. Ned does home-assistant *health* monitoring, not install/build. Even if it were Ned's, there are zero agent:ned engineers with HA + Piper TTS active-oahu storefront deploys — Michael is the right owner. |
| GRO-138 | YHG Interview: Kayak Routes & Safety | **agent:fred (content)** | The description literally begins "Michael's expert first-hand knowledge of Oahu kayak routes..." and "Michael records audio/video answers." **I cannot fabricate another human's expert kayak knowledge.** Even if I could, the comment thread names "Interviewer: Hermes (Fred)" — this is Fred's content lane by both assignment (the comment said so) and content type. |
| GRO-139 | YHG Interview: Tour Operator Comparisons | **agent:fred (content)** | Same shape as GRO-138. Comment names "Interviewer: Hermes (Fred)." Cannot fabricate. |
| GRO-140 | YHG Interview: Best Beaches & Local Secrets | **agent:fred (content)** | Same shape. |
| GRO-141 | YHG Interview: Oahu by Region | **agent:fred (content)** | Same shape. |
| GRO-142 | AOT Interview: The Mokulua Islands Experience | **agent:fred (content)** | Same shape; description even says "Ella, answer these however is easiest — voice memo is great" — assigned to a non-Michael persona. |
| GRO-143 | AOT Interview: Chinamans Hat & Kaneohe Bay Guide | **agent:fred (content)** | Same shape as GRO-142. |

**Result: 0/10 in Ned's lane. Of the 10, six require fabricating another human's voice/expertise to "execute" — a hard violation of the finishing-the-job doctrine ("NEVER substitute plausible-looking fabricated output for results you couldn't actually produce").**

---

## Rotation-equivalence ratchet verdict

| Criterion | Test | Result |
|---|---|---|
| (a) | GRO-559 dispatcher bug signature match | ✅ HOLD — curator-flag-stale-backlog fingerprint is the exact signature codified in Pass-N+32; all 10 auto-labeled in 7-sec window by orchestrator dispatcher |
| (b) | Per-issue partition — same partition (same wrong-lane targets) | ✅ HOLD — partition is: 4 content (Fred's lane), 2 AOT content (Fred's lane), 1 marketing (Fred's), 1 docs-schema (Orchestrator), 1 revenue-listings (Fred/Kai), 1 physical-build (Michael/Fred). Zero in Ned's lane. |
| (c) | Prior-pass anchor exists, age <6h, names all 10 IDs | ❌ **FAIL** — Pass-N+41's anchor on GRO-145 (id `82add25f-c704-4e12-a472-344648e3e2a9`, posted 2026-06-30T04:37:25Z) names GRO-145/146/149/150/155/156/157/161/162/163. **None of GRO-24/55/93/116/138-143 appear** anywhere in that anchor body. Genuinely-new-IDs failure mode per Pass-N+29. |

**Verdict: CRITERION (c) FAILS** — recipe re-runs.

---

## Disposal recipe (Pass-N+19 actual-execution, rotated-lowest-ID variant)

Per `references/fresh-misroute-batch-detector-gap.md`:

1. ✅ Audit doc (this file) written to `scripts/ops/gro-24-143-batch-routing-42nd-pass-infra-findings.md` (filename tracks **current** scanner feed range; both segments shift from Pass-N+41's `gro-145-162`)
2. ⏭ Commit on `ned/gro-485-triage-pass-1` with `[Ned]` prefix (single-day log branch)
3. ⏭ Post ONE consolidated anchor comment to **GRO-24** (new lowest-GRO-ID — overrides Pass-N+41's GRO-145 anchor for freshness purposes for this feed)
4. ⏭ Final response: `[SILENT]`

---

## Why this is the first observation of this pool

Today's 41 prior passes all hit `agent:ned` items from registered rotation pools (Batch B `GRO-484..502`, Batch A `GRO-594..1662..2533..2976`, and the Pass-N+32..+41 GRO-146..165 stale-backlog chain). **GRO-24/55/93/116/138-143 have not appeared in any Ned pass before today.**

Pool size analysis:
- These 10 IDs are the oldest in the Linear workspace (`createdAt` = 2026-05-22 through 2026-05-29). They've been sitting Backlog since.
- They were auto-routed by the curator flag 2026-06-29T15:54Z — same fingerprint as the GRO-146..165 chain (which hit the curator 2026-06-29T15:54Z too — same window).
- Implication: **the dispatcher fired a second wave of stale-backlog auto-routing** between 2026-06-29T15:54Z and the chains already observed. There's likely MORE items in this wave sitting in the orchestrator side waiting for the next rotation window.

**Pool growth observation:** Pass-N+19 codification estimated ~13-ID latent pool. Pass-N+29 revised to ~16. Pass-N+32 to ~26. **This pass: +10 (GRO-24/55/93/116/138-143) = ~36 IDs total.** Pool is unbounded and growing each rotation as the orchestrator-side stale-backlog auto-routing expands its target set.

---

## Anchor comment placement rationale

Posted to **GRO-24** because:
- It's the new lowest-GRO-ID (Pass-N+21 stable-lowest-ID rule: when the lowest rotates, shift the lowest segment of the filename AND the anchor target. Both shifted this pass: GRO-145 → GRO-24).
- Michael's standard triage ordering is lowest-GRO-ID-first per the skill codification.
- GRO-24 has no comments yet (queries showed `comments=1` and it's the curator flag) — clean thread, no prior Ned anchors to thread onto.

---

## Standing cure (verbatim from Pass-N+19, restated for traceability)

**Relabel 10 issues.** For each, drop `agent:ned` and add the correct lane label per the partition walk table above:

1. **GRO-24** → drop `agent:ned`, add `agent:fred`
2. **GRO-55** → drop `agent:ned`, add `agent:orchestrator`
3. **GRO-93** → drop `agent:fred`, add `agent:fred` (already in lane after relabeling off `agent:ned` — verify)
4. **GRO-116** → drop `agent:ned`, leave unassigned or add `agent:michael` (Michael is the right owner for physical build)
5. **GRO-138** → drop `agent:ned`, add `agent:fred`
6. **GRO-139** → drop `agent:ned`, add `agent:fred`
7. **GRO-140** → drop `agent:ned`, add `agent:fred`
8. **GRO-141** → drop `agent:ned`, add `agent:fred`
9. **GRO-142** → drop `agent:ned`, add `agent:fred`
10. **GRO-143** → drop `agent:ned`, add `agent:fred`

**HARD-SKIP `finalize_task.sh`.** Final response: `[SILENT]`.

**Long-term cure** (orchestrator-side, not Ned's lane): patch the dispatcher lane-content filter in `~/.hermes/profiles/orchestrator/scripts/post_publish_audit_v2.py` (or equivalent) to NOT auto-apply `agent:ned` to issues whose `createdAt` > N days AND project is in the content/marketing/physical-build/revenue-listing space. This is GRO-559 territory.

---

## Probe-skip / scope notes

- Probe-skip per Pass-12 protocol: GPU/disk/locks/Tailscale were clean as of Pass-N+41 (~5 min ago). No infra probes re-run this pass.
- Working-tree isolation per Pass-N+34: pre-commit `git status --short` verification — see commit message below.
- No in-lane work to execute.
- GRO-559 fix not landed (consistent across all 42 passes today).
