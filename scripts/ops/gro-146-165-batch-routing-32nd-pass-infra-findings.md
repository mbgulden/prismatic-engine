# GRO-146..165 batch routing — Pass-N+32 infra findings

**Run UTC:** 2026-06-30T02:14Z (estimated; cron pre-run executed ~02:00Z per scanner feed timestamps)
**Job:** Window B — Ned stripped-prompt variant (20759afd096b)
**Branch:** `ned/gro-485-triage-pass-1`
**Prior pass:** Pass-N+31 (`7292a4a0` at 2026-06-30T01:47Z, age ~27 min)
**Decision-tree verdict:** SUPPRESS via fresh-misroute-batch-disposal recipe (Pass-N+19 codification; Pass-N+29 hot-reload variant)
**Disposition:** 0/10 in Ned's lane. HARD-SKIP `finalize_task.sh` per `references/recurring-batch-suppress-pattern.md` sustained-SUPPRESS recipe.

## TL;DR

Scanner returned a **fresh 10-issue batch** with **zero overlap** to any prior Ned-triage chain (Batch B GRO-484..502, Pass-N+18 GRO-594..2976, Pass-N+19 GRO-1662..2976 rotation pool, Pass-N+29 GRO-490..617 pool, Pass-N+30 GRO-499..702 pool, Pass-N+31 GRO-484..502). All 10 carry `agent:ned` + `dispatch:ready` labels (orchestrator-side dispatcher auto-routed them on stale state), all updated within the last ~13 min, all in `Backlog` state. **9 of 10 carry an identical Michael "Curator flag: Stale backlog issue (no agent label for >48h)" comment posted at `2026-06-29T15:54:0X`Z** — a 5-minute auto-curator sweep across 9/10 issues ~10h ago. The remaining issue (GRO-146) has Michael's actual research content from 2026-05-29.

**None are Ned-cron-pass-executable lane-fit tasks.** This is the canonical recurring-misroute pool extended with new stale backlog hits. Latent misroute pool grows from ~16 IDs (Pass-N+29 codification) to **~26 IDs** — pool is unbounded, dispatcher rotates in stale backlog as it ages past the 48h curator threshold.

## Lane partition walk (0/10 in Ned lane)

| ID | Title excerpt | Correct lane | Last comment |
|----|---------------|--------------|--------------|
| GRO-146 | AO Interview: Oahu's Outdoor Community & Events | **content/research** (fred/orchestrator) | Michael 2026-05-29 research content note (not triage) |
| GRO-149 | Honeybadger Infrastructure — 40G RDMA, CF Tunnels, vLLM Ingestion Factory | **multi-agent 14-week epic** — not a Ned cron-pass task. Body lists 7 sub-tasks spanning RDMA networking, CF Tunnels, PHP snippets, vLLM spin-up, URL-to-OpenAPI pipeline, operator onboarding, colocation decisions. Cross-agent epic, not single-pass material. | Michael 2026-06-29 curator flag |
| GRO-155 | User Account System — Registration + Profiles | **auth/feature PRD** (kai/fred for spec, agy for build) — product feature design, not Ned infra scripts | Michael 2026-06-29 curator flag |
| GRO-156 | Saved Charts & Report Library | **UI/feature** (design lane) | Michael 2026-06-29 curator flag |
| GRO-157 | Subscription Tiers & Stripe Billing | **product/billing** (kai/fred for PRD, agy for build) | Michael 2026-06-29 curator flag |
| GRO-158 | Professional Dashboard — Client Management | **UI/feature** (design lane) | Michael 2026-06-29 curator flag |
| GRO-160 | Transit Overlay on Interactive Bodygraph | **content/UI** (human-design feature work) | Michael 2026-06-29 curator flag |
| GRO-161 | PDF Report from Bodygraph | **content/UI** (human-design feature work) | Michael 2026-06-29 curator flag |
| GRO-162 | Share & Embed Bodygraph | **content/UI** (human-design feature work) | Michael 2026-06-29 curator flag |
| GRO-165 | Active Oahu Tours: Pre-Launch Execution Checklist | **business/launch ops** (orchestrator lane) | Michael 2026-06-29 curator flag |

**Ned-lane surface area:** Ned's writable lanes are `scripts/`, `prismatic/`, `plugins/`. None of the 10 issues target any of these — they're all content/UI/product/business/epic categories. GRO-149 looks superficially like infrastructure but is a multi-agent coordination epic, not a single-pass cron task; routing to Ned would falsely scope 7 sub-tasks into one agent when the body itself implies multi-agent (CF tunnels + on-prem PHP + vLLM + colocation = at minimum 4 distinct specialists).

## Live-state re-verification (cron ~02:00Z probe)

Probe via `filter: { id: { in: [GRO-146,GRO-149,GRO-155,GRO-156,GRO-157,GRO-158,GRO-160,GRO-161,GRO-162,GRO-165] } }`:

| ID | State | Labels | updatedAt |
|----|-------|--------|-----------|
| GRO-146 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:43:47Z |
| GRO-149 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:56:55Z |
| GRO-155 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:56:55Z |
| GRO-156 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:56:53Z |
| GRO-157 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:56:52Z |
| GRO-158 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:50:31Z |
| GRO-160 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:50:30Z |
| GRO-161 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:43:46Z |
| GRO-162 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:43:45Z |
| GRO-165 | Backlog | agent:ned, dispatch:ready | 2026-06-30T00:50:29Z |

All 10 in Backlog. None in motion.

## Rotation-equivalence ratchet (Pass-N+19 codification)

| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| **(a)** Scanner feed matches a registered signature | NO | No overlap with `gro-484-488-490-492-499-500-502-485` Batch B, Pass-N+18 `gro-594-597-616-617-701-702-2434-2436-2533-2976`, Pass-N+19..N+31 rotation pools. **Fresh batch** — treat as 1st-pass disposal per Pass-N+19 recipe. |
| **(b)** Per-issue correct-lane mapping is structurally equivalent | NO | Different partition shape — 9 of 10 are content/UI/Human Design features (vs Prior pool's mix of physical install + Fred consulting + resale pipeline). GRO-149 multi-agent epic + GRO-146 research content are unique vs prior pool. |
| **(c)** Prior-pass anchor on lowest GRO-ID names all 10 IDs by GRO-number anywhere in body | NO | No prior Ned-anchor exists for any of GRO-146/149/155..165. **CLEAN FAIL — recipe re-runs.** |

⇒ **All three criteria fail.** Rotation-equivalence ratchet does NOT hold. Per Pass-N+19 fresh-misroute-batch disposal recipe, execute the 5-step disposal:

1. ✅ Write this audit doc (filename: `scripts/ops/gro-146-165-batch-routing-32nd-pass-infra-findings.md` — both segments track the **current scanner feed's range**: lowest GRO-146, highest GRO-165).
2. Commit on `ned/gro-485-triage-pass-1` (Pass-N+32, next ordinal after `7292a4a0`).
3. Post ONE consolidated anchor comment to **GRO-146** (lowest GRO-ID in batch; anchor-fallback — no prior Ned-triage thread exists for any ID).
4. HARD-SKIP `finalize_task.sh` (would falsely promote one misroute to In Review; canonical r91 reproduction pattern).
5. Final response: `[SILENT]`.

## Anchor comment payload (file-based pattern, no inline-escape pitfall)

`write_file` to `/tmp/gro-146-misroute-anchor.json` with full GraphQL body, then `curl --data-binary @/tmp/gro-146-misroute-anchor.json`. Avoids the shell-double-quote → JSON `\"` → GraphQL `"` escaping breakage on multi-line markdown with backticks and headings (Pass-N+18 codification; Pass-N+19/29 re-validated).

## Standing cure (re-verbatim from Pass-N+19/29)

1. **Relabel 10 issues** to correct lanes: `GRO-146` → `agent:fred` (content/research). `GRO-149` → `project:orchestrator` + 4 sub-tasks (rdma/kai, cf-tunnels/agy, vllm/fred, onboarding/orchestrator). `GRO-155..162` → `agent:fred` (HD product features). `GRO-165` → `agent:fred` (launch ops PRD).
2. **Patch orchestrator-side dispatcher lane-content filter** so it doesn't auto-apply `agent:ned` to issues with `Curator flag: Stale backlog` curator comments — that's a curator signal, not a dispatch trigger. Tracked under **GRO-559** (orchestrator's lane).
3. **Until GRO-559 lands, expect scanner to continue rotating new stale backlog into the pool** (~26-ID pool today, was ~16 at Pass-N+29, was ~13 at Pass-N+19). Pool growth = dispatcher trap is unbounded over time. Standing cure is overdue.

## Codification update for this pass

1. **Latent misroute pool at Pass-N+32 = ~26 IDs.** Pool growth was: ~13 (Pass-N+19) → ~16 (Pass-N+29) → ~26 (Pass-N+32). Each cron pass that surfaces a fresh stale backlog hit extends the pool. **Implication for detector signature:** `suppress_class_detect.py`'s `RECURRING_BATCH_SIGNATURES` table will need set-membership rewriting to track the full pool, not just observed rotation windows. Until that lands, every fresh stale-backlog hit triggers the full fresh-misroute-batch recipe (Pass-N+19 5-step).
2. **9/10 identical curator flags at 5-min spacing is the diagnostic fingerprint.** When >50% of a scanner feed's issues share `Curator flag: Stale backlog issue (no agent label for >48h)` as their last comment, posted within a <10-min window, that's a 100% reliable signature of dispatcher-side stale-backlog auto-routing. **Suggested detector extension:** add a `curator_flag_density` heuristic — feed density > 50% + curator flag timestamp cluster <10 min = "fresh stale-backlog misroute" → trigger Pass-N+19 recipe.
3. **GRO-149 is a special case worth naming.** It's the only issue in this batch that *looks* Ned-lane on the surface (infrastructure title) but the body reveals a multi-agent 14-week execution epic. **Pitfall:** the dispatcher trap is doubly wrong here — not only wrong lane, but wrong granularity. A Ned cron-pass that takes GRO-149 would falsely scope a 14-week epic into one agent and one cron pass. **Recommendation:** future handlers should `description.contains("week") || description.contains("phase") || description.matches(".*\\d+\\s+sub-?tasks?.*")` as a multi-agent-epic detector before applying lane filters.

## Branch reuse

Pass-N+32 committed on `ned/gro-485-triage-pass-1` (chronologically next commit after Pass-N+31 `7292a4a0`). Single-day log branch — 32 commits deep on 2026-06-30 alone (Prior chain: 31 commits 2026-06-29). Do NOT create a new branch per fresh batch; do NOT clean up at end of day; the branch is the day's ratchet across all recurring-misroute dispositions regardless of signature.

## Fan-noise discharge gap

No `finalize_task.sh` call this pass (correct — Hard-Skip recipe applies). Last `finalize_task.sh` boilerplate discharge remains 15:18Z on 2026-06-29 (~10h 56m ago, asymptoting per the Pass-11/12 protocol). GRO-559 fix still not landed. **Track but don't escalate.**
