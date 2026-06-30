# gro-2990-3012-batch-routing-46th-pass-infra-findings.md

**Pass:** Pass-N+46 (cron job `20759afd096b` = Window B stripped-prompt variant — fires at ~10:27Z on 2026-06-30, ~10 min after Pass-N+45 at ~10:17Z)
**Lowest-GRO-ID:** GRO-2990
**Highest-GRO-ID:** GRO-3012
**Branch:** `ned/gro-485-triage-pass-1` (single-day log, continued)
**Threshold-edge context:** Pass-N+45 anchor landed on GRO-2990 (lowest GRO-ID of the rotation cohort) at 10:17:36Z age ~10.9 min — fresh inside 6h gate. Pass-N+44 anchor on GRO-2990 at 10:06:44Z age ~22 min still inside gate. Pass-N+45 is the controlling anchor for THIS rotation cohort (it names all 10 GRO-2990..3012 IDs and is the most recent fresh anchor).

---

## Scanner feed (10 issues, byte-identical to Pass-N+44 + Pass-N+45)

Feed is **BYTE-IDENTICAL** to Pass-N+45 (~10 min prior) AND Pass-N+44 (~22 min prior): same 10 IDs, same `In Review` state, same `agent:ned` + `dispatch:ready` label pair, same correct-lane partition (6 SUBSUMED-by-GRO-2981 + 4 wrong-lane auto-routes). No rotation event detected across three consecutive passes. Pass-N+25 lightweight 3-step ratchet recipe applies cleanly.

| # | ID | State | Title (truncated) | Correct lane | Ned-lane? |
|---|----|-------|-------------------|--------------|-----------|
| 1 | **GRO-2990** | In Review | [Ned] GRO-2980.1 — Wire record_tokens() at LLM call sites | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
| 2 | GRO-2991 | In Review | [Ned] GRO-2980.2 — Wire record_hook_fired() at hook bus | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
| 3 | GRO-2992 | In Review | [Ned] GRO-2980.3 — Wire record_pipeline_action() at pipeline state transitions | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
| 4 | GRO-2993 | In Review | [Ned] GRO-2980.4 — Wire record_plugin_registered() at plugin loader | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
| 5 | GRO-2995 | In Review | [Ned] GRO-2980.6 — Build gcp_vertex_spend_events INSERT writer | `agent:ned` BUT **SUBSUMED by GRO-2981** (Vertex poller runs in orchestrator lane) | ⚠️ in-lane-but-subsumed |
| 6 | GRO-2996 | In Review | [Ned] GRO-2979.1 — Add process_observer_thread + dispatch caps to fix GRO-2051 retry storm | `agent:ned` BUT **SUBSUMED by GRO-2981** (178 retry-storm dispatches from GRO-2051 are in GRO-2981's 635-row set; cure is orchestrator-side dispatch caps) | ⚠️ in-lane-but-subsumed |
| 7 | GRO-2998 | In Review | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` | ❌ out-of-lane |
| 8 | GRO-2999 | In Review | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` (duplicate of GRO-2998; GRO-559 auto-route twice) | ❌ out-of-lane |
| 9 | GRO-3011 | In Review | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` | ❌ out-of-lane |
| 10 | GRO-3012 | In Review | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` (duplicate of GRO-3011; GRO-559 auto-route twice) | ❌ out-of-lane |

---

## Verdict

**SUPPRESS — no Ned work product, no Linear state mutation, no finalize_task.sh, no fresh anchor comment.**

### Pass-N+25 lightweight recipe gate — all 5 conditions HOLD

| # | Condition | Pass-N+46 status |
|---|-----------|------------------|
| 1 | Prior anchor fresh (<6h) | ✅ Pass-N+45 anchor at 10:17:36Z, age 10.9 min |
| 2 | Anchor names all 10 feed IDs | ✅ Pass-N+45 names GRO-2990..3012 explicitly |
| 3 | Feed byte-identical to anchor | ✅ Same 10 IDs, same labels, same states |
| 4 | SUBSUMED-by-GRO-2981 holds for all 6 [Ned] items | ✅ All 6 root-caused to orchestrator-bypass (Pass-N+20 4-point checklist holds: same root cause orchestrator-bypass + cure lives in orchestrator lane + Ned-side fix cosmetic + GRO-2981 root-caused) |
| 5 | Rotation-equivalence ratchet (a)+(b) HOLD | ✅ (a) zero-ID delta vs Pass-N+45, (b) zero-state delta vs Pass-N+45, (c) FAIL-but-benign per Pass-N+29 codification — fresh anchor naming all IDs supersedes (c) |

### Pass-N+20 subsumption recipe — 4-point checklist verified for all 6 [Ned] items

1. **Same root cause:** all 6 [Ned] items trace to orchestrator-side `agy_sandbox_event_supervisor.py` launch path bypassing `record_*()` writers (GRO-2981 §3 — "5-always-zero tables")
2. **Cure lives in orchestrator lane:** orchestrator owns the launch path; Ned-side wiring is cosmetic
3. **Ned-side fix would be cosmetic only:** writing the unwired record_*() calls in Ned-owned modules does NOT populate the tables while dispatch bypass persists
4. **GRO-2981 root-caused:** commit `fbc59788` documents the bypass with 635-row dispatch set showing the failure mode

---

## Working-tree state (verified pre-write per Pass-N+34 isolation protocol)

```
 M prismatic/gateway/event_bus.py        (sibling-owned, untouched)
 M prismatic/gateway/server.py           (sibling-owned, untouched)
?? prismatic/gateway/event_handlers/     (sibling-owned, untouched)
```

All 3 working-tree deltas are **sibling-owned** (modified between Pass-N+43 09:09Z and Pass-N+44 10:05Z by other agents; same state persists through Pass-N+46). Pass-N+34 protocol: stage ONLY `scripts/ops/gro-2990-3012-batch-routing-46th-pass-infra-findings.md`, do NOT use `git add -A` or `git commit -am`.

---

## Two cron jobs converging on the same feed

Confirmed pattern: cron `a9374c15f022` (Window A canonical long-form, fires on its own schedule) AND cron `20759afd096b` (Window B stripped-prompt, fires every 15 min) both scan the same 10-issue batch. Pass-N+44 was authored by `20759afd096b`. Pass-N+45 authored by `20759afd096b`. Pass-N+46 (this) authored by `20759afd096b`. If `a9374c15f022` fires within the 6h anchor-freshness window with the same feed, it will apply the lightweight recipe independently and commit its own Pass-N+47 audit doc to the same `ned/gro-485-triage-pass-1` branch. Commits chain contiguously; ordinal is per-audit-doc.

**Cadence observation:** Pass-N+44 → Pass-N+45 = 9 min gap. Pass-N+45 → Pass-N+46 = 10 min gap. Pass-N+25 lightweight recipe holds at sub-15-min cadence; no actual-execution recipe re-run needed.

---

## Pool growth + GRO-559 fix status

- Pool growth: Pass-N+42 ~36 IDs → Pass-N+44 ~46 IDs → Pass-N+45 ~46 IDs → Pass-N+46 ~46 IDs (no growth across 3 consecutive passes; byte-identical ratchet confirmed at triple-stamp).
- GRO-559 fix: **NOT LANDED** as of 2026-06-30 10:27Z. Latent misroute pool persists. Ned cron ticks continue to receive `agent:ned`-labeled misroutes until GRO-559 ships.

---

## What this pass intentionally skips (Pass-N+25 lightweight recipe)

- ❌ No `finalize_task.sh` call (would falsely transition anchor issue to "In Review" — canonical r91 theater).
- ❌ No fresh anchor comment on GRO-2990 (Pass-N+45 anchor age 10.9 min, still at top of comment stack).
- ❌ No infra probes (GPU/PVE6/disk/Tailscale) — byte-identical feed means Pass-N+45 validated infra clean.
- ❌ No lock acquisition (audit doc lives in `scripts/ops/`, Ned-owned lane, pre-push hook approves 2/2 in-lane per Pass-N+44 hook-silent-block mitigation).
- ❌ No `git push origin` — audit-doc commits stay local on `ned/gro-485-triage-pass-1` until Michael reviews (per Pass-N+39 doctrine for Window B variant).

---

## Action summary

| Step | Status |
|------|--------|
| 1. Write per-pass audit doc | ✅ `scripts/ops/gro-2990-3012-batch-routing-46th-pass-infra-findings.md` |
| 2. Commit on `ned/gro-485-triage-pass-1` | ⏳ next tool call |
| 3. Final response | `[SILENT]` |

**Verdict: SUPPRESS — no Ned work product, no Linear state mutation, no finalize_task.sh, no fresh anchor. The 5-condition Pass-N+25 lightweight recipe gate holds; the byte-identical feed (3 consecutive passes confirmed) + fresh anchor + named-all-IDs signature is intact.**

Handoff reminder (from Pass-N+45, still open): Fred lane owns GRO-2998/2999 relabel; orchestrator lane owns GRO-3011/3012 relabel AND owns the orchestrator-side `record_*()` cure for GRO-2981 → unblocking the 6 SUBSUMED [Ned] items (GRO-2990/2991/2992/2993/2995/2996). Ned has no in-lane work this pass.