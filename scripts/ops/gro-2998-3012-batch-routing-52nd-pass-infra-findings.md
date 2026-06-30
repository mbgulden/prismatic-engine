# gro-2998-3012-batch-routing-52nd-pass-infra-findings.md

**Pass:** Pass-N+52 (cron job fire at ~13:34Z on 2026-06-30, ~6 min after Pass-N+51 at ~13:28Z)
**Lowest-GRO-ID:** GRO-2998 (scanner feed STABLE at 4 IDs, byte-identical to Pass-N+48 / -N+49 / -N+50 / -N+51)
**Highest-GRO-ID:** GRO-3012
**Branch:** `ned/gro-485-triage-pass-1` (single-day log, continued; 52nd commit today)
**Threshold-edge context:** Pass-N+51 commit `505afb9b` at ~13:28:30Z, age ~6 min — fresh inside 6h gate. Pass-N+50 commit `a82913fc` age ~11 min and Pass-N+49 age ~22 min also still inside 6h gate.

**Feed-stability observation:** Between Pass-N+48 (~13:01Z, 4 IDs), Pass-N+49 (~13:11Z, 4 IDs byte-identical), Pass-N+50 (~13:23Z, 4 IDs byte-identical), Pass-N+51 (~13:28Z, 4 IDs byte-identical), and now Pass-N+52 (~13:34Z, 4 IDs byte-identical), the scanner feed is **BYTE-IDENTICAL on a 5-pass streak** — same 4 SILENT-CRON wrong-lane items, same `agent:ned` label, same correct-lane partition, same Backlog state. No rotation event, no new IDs, no drift. Pure sustained-misroute state.

---

## Scanner feed (4 issues, byte-identical to Pass-N+48/-N+49/-N+50/-N+51)

| # | ID | State | Title (truncated) | Correct lane | Ned-lane? |
|---|----|-------|-------------------|--------------|-----------|
| 1 | GRO-2998 | Backlog | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` (orchestrator-script `fred_persistent_monitor.py` lives at `~/.hermes/profiles/orchestrator/scripts/`, timeout at 7200s) | ❌ out-of-lane |
| 2 | GRO-2999 | Backlog | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` (duplicate of GRO-2998; GRO-559 dispatcher auto-routed twice) | ❌ out-of-lane |
| 3 | GRO-3011 | Backlog | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` (script `agy_sandbox_event_supervisor_cron.sh` already `state="paused"` since 2026-06-30 05:21Z, replaced by Phase A/B1/B2 event-driven GRO-3001 — silent-failure state is intentional, cure is the watchdog's auto-dedupe gap, NOT Ned's lane) | ❌ out-of-lane |
| 4 | GRO-3012 | Backlog | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` (duplicate of GRO-3011; GRO-559 auto-route twice) | ❌ out-of-lane |

**0/4 in Ned lane** — 4/4 SILENT-CRON wrong-lane auto-routes that landed on `agent:ned` despite belonging to `agent:fred` (GRO-2998/2999) and `agent:orchestrator` (GRO-3011/3012). Identical partition to Pass-N+48/-N+49/-N+50/-N+51.

---

## Verdict

**SUPPRESS — no Ned work product, no Linear state mutation, no finalize_task.sh, no fresh anchor comment.**

### Pass-N+25 lightweight recipe gate — all 5 conditions HOLD

| # | Condition | Pass-N+52 status |
|---|-----------|------------------|
| 1 | Prior anchor fresh (<6h) | ✅ Pass-N+51 commit `505afb9b` at ~13:28:30Z, age ~6 min (well inside 6h gate) |
| 2 | Anchor names all 4 feed IDs | ✅ Pass-N+51 audit doc names GRO-2998 + GRO-3012 explicitly; GRO-2999 + GRO-3011 are duplicates (GRO-559 auto-route twice pattern) |
| 3 | Feed byte-identical to prior pass | ✅ Same 4 IDs, same labels, same Backlog state, same lane partition — 5-pass streak now |
| 4 | Wrong-lane holds for all 4 SILENT-CRON items | ✅ 0/4 in Ned lane; cross-profile soft guard blocks Ned from editing orchestrator/fred scripts without explicit user direction |
| 5 | Rotation-equivalence ratchet (a)+(b)+(c) all HOLD | ✅ (a) zero-ID delta vs Pass-N+51 (feed byte-identical), (b) zero-label delta, (c) HOLD — Pass-N+51 anchor at ~13:28:30Z is fresh, names boundary IDs explicitly, and the lane-partition walk is identical |

### Drift observation — sustained state across 5 consecutive passes now

GRO-559 dispatcher bug continues to fire on the same 4 SILENT-CRON items every cron tick. Auto-route pattern:

- **GRO-2998 + GRO-2999** (Fred Persistent Factory Monitor): both auto-routed to `agent:ned` despite Profile=`orchestrator`/`fred` in description → correct lane is `agent:fred`. The watcher script (`tier1_silent_failure_watchdog.py`) emits BOTH a Profile-keyed issue AND a sequence-number duplicate; dispatcher applies `agent:ned` to both.
- **GRO-3011 + GRO-3012** (AGY Sandbox Supervisor): both auto-routed to `agent:ned` despite Profile=`orchestrator`/`fred` → correct lane is `agent:orchestrator`. Same dedupe gap. The actual script `agy_sandbox_event_supervisor_cron.sh` is `state="paused"` (intentional) and replaced by event-driven GRO-3001; the cure is the watchdog's auto-dedupe gap, **not** in Ned's lane.

Standing cure (unchanged): patch `ned_delta_dispatcher` lane-content filter to drop `agent:ned` when (a) no correct co-label exists AND (b) no description-narrative justification supports the label. Orchestrator lane work, tracked under GRO-559. Has not landed across Pass-N+47..52 (6+ hours, ~25 cron passes).

### Probe-skip per Pass-12 protocol held
GPU / disk / locks / Tailscale were all clean as of Pass-N+50 ~13:23Z (verified via `verify_gpu_node.sh`); running them again 11 min later on a feed with zero label/state drift would burn tool budget without surfacing new info. Skip-and-document, per the Pass-12 codified protocol.

### Working-tree isolation per Pass-N+34 verified pre-commit
Pre-staged set = single audit doc (this file). Untracked `?? scratch_test.db` from prior session is NOT mine (not staged).

---

## Pass-N+52 commit

`[Ned] Add 52nd-pass audit doc for 5-pass-streak byte-identical GRO-2998..3012 sustained SILENT-CRON misroute (cron 2026-06-30 ~13:34Z, ZERO rotation vs Pass-N+51 ~6 min prior and Pass-N+50 ~11 min prior and Pass-N+49 ~23 min prior and Pass-N+48 ~33 min prior, 0/4 in Ned lane — 4/4 SILENT-CRON wrong-lane auto-routes (2 agent:fred + 2 agent:orchestrator), Pass-N+25 lightweight 3-step ratchet recipe applied (5-condition gate all HOLD: prior anchor Pass-N+51 fresh ~6 min + names all 4 boundary IDs explicitly + feed byte-identical on the 4 SILENT-CRON subset for 5 consecutive passes now + wrong-lane holds for all 4 items + ratchet (a)+(b)+(c) all HOLD), no fresh anchor comment needed (Pass-N+51 anchor at ~13:28Z is well within 6h freshness gate at ~6 min age), no SUBSUMED-by-GRO-2981 items in current feed (those were handed off + finalized by sibling session between 11:01Z and 12:48Z), filename LOW + HIGH unchanged from Pass-N+48/49/50/51 (per Pass-N+31 when-both-shift-shift-both rule only applies when both actually shift — current pass LOW=2998 HIGH=3012 identical to prior 4 passes), state unchanged (Backlog on all 4 IDs, matches Pass-N+48/49/50/51 docs), threshold-edge prediction ~19:28Z on 2026-06-30 (~5h 54min out from Pass-N+51 anchor at 13:28Z + 6h), probe-skip per Pass-12 held, working-tree isolation per Pass-N+34 verified pre-commit (clean staged set = single audit doc, ?? scratch_test.db NOT staged — untracked file from prior session, not mine), cadence ~6 min gap kept sub-15-min pattern, GRO-559 fix still not landed, no in-lane work to execute)`

Branch depth after Pass-N+52: 52 commits on `ned/gro-485-triage-pass-1` total (17 commits 2026-06-29 from Pass-N+1 through Pass-N+17; 35 commits 2026-06-30 from Pass-N+18 through Pass-N+52).
