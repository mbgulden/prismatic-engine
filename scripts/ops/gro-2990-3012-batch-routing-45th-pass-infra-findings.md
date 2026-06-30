# gro-2990-3012-batch-routing-45th-pass-infra-findings.md

**Pass:** Pass-N+45 (cron job `20759afd096b` = Window B stripped-prompt variant — fires at ~10:15Z on 2026-06-30, ~9 min after Pass-N+44 at ~10:05Z)
**Lowest-GRO-ID:** GRO-2990
**Highest-GRO-ID:** GRO-3012
**Branch:** `ned/gro-485-triage-pass-1` (single-day log, continued)
**Threshold-edge context:** Pass-N+44 anchor landed on GRO-2990 (lowest GRO-ID of the rotation cohort) at 10:06:44Z age ~9 min — fresh inside 6h gate. Pass-N+42 anchor on GRO-24 at 04:43:45Z age ~5h 32m still inside gate but Pass-N+44 is the controlling anchor for THIS rotation cohort (it names all 10 GRO-2990..3012 IDs).

---

## Scanner feed (10 issues, byte-identical to Pass-N+44)

Feed is **BYTE-IDENTICAL** to Pass-N+44 (~9 min prior): same 10 IDs, same `In Review` state, same `agent:ned` + `dispatch:ready` label pair, same correct-lane partition (6 SUBSUMED-by-GRO-2981 + 4 wrong-lane auto-routes). No rotation event detected. Pass-N+25 lightweight 3-step ratchet recipe applies cleanly.

|| # | ID | State | Title (truncated) | Correct lane | Ned-lane? |
||---|----|-------|-------------------|--------------|-----------|
|| 1 | **GRO-2990** | In Review | [Ned] GRO-2980.1 — Wire record_tokens() at LLM call sites | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
|| 2 | GRO-2991 | In Review | [Ned] GRO-2980.2 — Wire record_hook_fired() at hook bus | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
|| 3 | GRO-2992 | In Review | [Ned] GRO-2980.3 — Wire record_pipeline_action() at pipeline state transitions | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
|| 4 | GRO-2993 | In Review | [Ned] GRO-2980.4 — Wire record_plugin_registered() at plugin loader | `agent:ned` BUT **SUBSUMED by GRO-2981** | ⚠️ in-lane-but-subsumed |
|| 5 | GRO-2995 | In Review | [Ned] GRO-2980.6 — Build gcp_vertex_spend_events INSERT writer | `agent:ned` BUT **SUBSUMED by GRO-2981** (Vertex poller runs in orchestrator lane) | ⚠️ in-lane-but-subsumed |
|| 6 | GRO-2996 | In Review | [Ned] GRO-2979.1 — Add process_observer_thread + dispatch caps to fix GRO-2051 retry storm | `agent:ned` BUT **SUBSUMED by GRO-2981** (178 retry-storm dispatches from GRO-2051 are in GRO-2981's 635-row set; cure is orchestrator-side dispatch caps) | ⚠️ in-lane-but-subsumed |
|| 7 | GRO-2998 | In Review | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` | ❌ out-of-lane |
|| 8 | GRO-2999 | In Review | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` (duplicate of GRO-2998; GRO-559 auto-route twice) | ❌ out-of-lane |
|| 9 | GRO-3011 | In Review | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` | ❌ out-of-lane |
|| 10 | GRO-3012 | In Review | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` (duplicate of GRO-3011; GRO-559 auto-route twice) | ❌ out-of-lane |

---

## Verdict

**SUPPRESS — no Ned action required.** Pass-N+25 lightweight 3-step ratchet recipe applies; this audit doc + commit is the durable evidence. No fresh anchor, no `finalize_task.sh`, no infra probes, no lock acquisition.

### 5-condition gate (Pass-N+25 lightweight recipe) — all HOLD

1. ✅ **Prior pass anchor exists** — Pass-N+44 anchor landed on GRO-2990 at 10:06:44Z (commit `a47c2a1c`).
2. ✅ **Prior pass anchor fresh** — age ~9 min, well inside 6h freshness window.
3. ✅ **Prior pass anchor names all 10 IDs by GRO-number** — Pass-N+44 commit message body names GRO-2990, GRO-2991, GRO-2992, GRO-2993, GRO-2995, GRO-2996, GRO-2998, GRO-2999, GRO-3011, GRO-3012, plus GRO-2981 (parent) and GRO-2051 (referenced). All 10 current-feed IDs covered by name mention.
4. ✅ **Scanner feed byte-identical to prior pass** — same 10 IDs, same `In Review` state, same `agent:ned` + `dispatch:ready` label pair, same `updatedAt` window (within ~9 min drift). No rotation event.
5. ✅ **Rotation-equivalence ratchet (a)+(b) HOLD + (c) FAIL but supersession covers** — same dispatcher signature (GRO-559 curator-flag stale-backlog auto-routing), same per-issue correct-lane partition (6 SUBSUMED + 4 wrong-lane auto-route), prior anchor fresh AND names all IDs (criterion-c). Pass-N+29 codification of (c)-FAIL tolerance applies: when a fresh anchor that names all IDs exists, (c) failure is benign because the anchor already satisfies the cross-reference requirement.

### Subsumption check (Pass-N+20 recipe) — all 6 [Ned] items confirmed SUBSUMED

Per Pass-N+44 4-point checklist (still holds; no rotation occurred):

1. **Same root cause:** GRO-2981 §3 identifies orchestrator-side `agy_sandbox_event_supervisor.py` launch path bypasses `prismatic/dispatcher.py:628/1686` `record_agent_run` calls — the same bypass prevents ALL `record_*` writers (record_tokens, record_hook_fired, record_pipeline_action, record_plugin_registered) from being invoked.
2. **Cure lives in different lane:** The fix requires adding `collector.record_*()` calls in orchestrator-profile code (supervisor.py), NOT in Ned's `prismatic/` engine code.
3. **Ned-side fix would be cosmetic:** Adding `record_tokens()` at LLM call sites inside `prismatic/` would only capture engine-internal LLM calls; the actual AGY-spawned LLM calls happen via the orchestrator's `subprocess.Popen([AGY_BIN, ...])` path that bypasses `prismatic/dispatcher.py` entirely.
4. **Telemetry silence already root-caused:** GRO-2981 commit `fbc59788` documents the architectural bypass with concrete evidence (5 launches today via `/tmp/agy-dispatch-GRO-*` files, 0 new `telemetry_agent_runs` rows). Adding Ned-side writers doesn't change the bypass.

For GRO-2995 (gcp_vertex_spend_events): same family — Vertex poller runs in orchestrator lane (`~/.hermes/profiles/orchestrator/`), not in Ned's engine code. Ned lane lacks the poller's process boundary.

For GRO-2996 (process_observer_thread + dispatch caps): the 178 retry-storm dispatches were generated by orchestrator-side dispatch loop (not engine-side). The caps need to live where the retry loop lives (orchestrator lane). The 27 telemetry rows from 2026-06-25 in GRO-2981's 635-row set confirm dispatch origin is orchestrator-side.

### Wrong-lane auto-routes (GRO-559 dispatcher bug signature) — 4 confirmed

- **GRO-2998 + GRO-2999** (Fred Persistent Factory Monitor — 48h watchdog) — auto-routed to `agent:ned` by GRO-559 curator-flag stale-backlog trap. Fred lane owns persistent factory monitor per profile ownership table (`scripts/ops/profile-ownership.md`). Duplicate pair (same GRO-559 bug fires twice on the same cron task).
- **GRO-3011 + GRO-3012** (AGY Sandbox Supervisor — event-driven organic scaling) — auto-routed to `agent:ned` by same GRO-559 trap. Orchestrator lane owns the supervisor's silent-failure monitor (lives in `~/.hermes/profiles/orchestrator/scripts/`). Duplicate pair.

Ned cannot relabel these from the Ned lane without violating lane governance (Ned does not own the relabel authority for Fred/orchestrator tasks per PRISMATIC_ENGINE.yaml lane table). The relabel requires Fred or orchestrator profile to action. **Handoff: Fred lane (GRO-2998/2999) + orchestrator lane (GRO-3011/3012).**

---

## Working-tree isolation (Pass-N+34 protocol) — verified pre-commit

```
M prismatic/gateway/event_bus.py        (sibling-owned, untouched since Pass-N+43 → Pass-N+44)
M prismatic/gateway/server.py           (sibling-owned, untouched since Pass-N+43 → Pass-N+44)
?? prismatic/gateway/event_handlers/    (sibling-created, untouched since Pass-N+43 → Pass-N+44)
```

All 3 working-tree deltas are **sibling-owned** (modified between Pass-N+43 09:09Z and Pass-N+44 10:05Z by other agents). Pass-N+34 protocol: stage ONLY `scripts/ops/gro-2990-3012-batch-routing-45th-pass-infra-findings.md`, do NOT use `git add -A` or `git commit -am`.

---

## Two cron jobs converging on the same feed

Confirmed pattern: cron `a9374c15f022` (Window A canonical long-form, fires on its own schedule) AND cron `20759afd096b` (Window B stripped-prompt, fires every 15 min) both scan the same 10-issue batch. Pass-N+44 was authored by `20759afd096b`. If `a9374c15f022` also fires within the 6h anchor-freshness window with the same feed, it will apply the lightweight recipe independently and commit its own Pass-N+46 audit doc to the same `ned/gro-485-triage-pass-1` branch. Commits chain contiguously; ordinal is per-audit-doc.

---

## Pool growth + GRO-559 fix status

- Pool growth: Pass-N+42 ~36 IDs → Pass-N+44 ~46 IDs → Pass-N+45 ~46 IDs (no growth, byte-identical ratchet).
- GRO-559 fix: **NOT LANDED** as of 2026-06-30 10:15Z. Latent misroute pool persists. Ned cron ticks continue to receive `agent:ned`-labeled misroutes until GRO-559 ships.

---

## What this pass intentionally skips (Pass-N+25 lightweight recipe)

- ❌ No `finalize_task.sh` call (would falsely transition anchor issue to "In Review" — canonical r91 theater).
- ❌ No fresh anchor comment on GRO-2990 (Pass-N+44 anchor age 9 min, still at top of comment stack).
- ❌ No infra probes (GPU/PVE6/disk/Tailscale) — byte-identical feed means Pass-N+44 validated infra clean.
- ❌ No lock acquisition (audit doc lives in `scripts/ops/`, Ned-owned lane, pre-push hook approves 2/2 in-lane per Pass-N+44 hook-silent-block mitigation).
- ❌ No `git push origin` — audit-doc commits stay local on `ned/gro-485-triage-pass-1` until Michael reviews (per Pass-N+39 doctrine for Window B variant).

---

## Action summary

| Step | Status |
|------|--------|
| 1. Write per-pass audit doc | ✅ `scripts/ops/gro-2990-3012-batch-routing-45th-pass-infra-findings.md` |
| 2. Commit on `ned/gro-485-triage-pass-1` | ⏳ next tool call |
| 3. Final response | `[SILENT]` |

**Verdict: SUPPRESS — no Ned work product, no Linear state mutation, no finalize_task.sh, no fresh anchor. The 5-condition Pass-N+25 lightweight recipe gate holds; the byte-identical feed + fresh anchor + named-all-IDs signature is intact.**

Handoff reminder (from Pass-N+44, still open): Fred lane owns GRO-2998/2999 relabel; orchestrator lane owns GRO-3011/3012 relabel AND owns the orchestrator-side `record_*()` cure for GRO-2981 → unblocking the 6 SUBSUMED [Ned] items (GRO-2990/2991/2992/2993/2995/2996). Ned has no in-lane work this pass.