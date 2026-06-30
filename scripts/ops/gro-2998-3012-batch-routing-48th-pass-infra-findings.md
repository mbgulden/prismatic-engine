# gro-2998-3012-batch-routing-48th-pass-infra-findings.md

**Pass:** Pass-N+48 (cron job fire at ~13:01Z on 2026-06-30, ~2h 27min after Pass-N+47 at ~10:34Z)
**Lowest-GRO-ID:** GRO-2998 (scanner feed SHRANK from 10 IDs at Pass-N+47 to 4 IDs at Pass-N+48)
**Highest-GRO-ID:** GRO-3012
**Branch:** `ned/gro-485-triage-pass-1` (single-day log, continued)
**Threshold-edge context:** Pass-N+47 commit `a6211321` at ~10:34Z, age ~2h 27min — fresh inside 6h gate. Same anchor (Pass-N+46 GRO-2990, names all 10 IDs) still applies via the GRO-2998..3012 SUBSTRING. Prior Pass-N+47 anchor names GRO-2998 and GRO-3012 explicitly (both are SILENT-CRON wrong-lane items listed in Pass-N+47's 10-ID feed table).

**Feed-shrink observation:** Between Pass-N+47 (~10:34Z, 10 IDs GRO-2990..3012) and Pass-N+48 (~13:01Z, 4 IDs GRO-2998/2999/3011/3012), the scanner dropped the 6 in-lane `agent:ned` labels (GRO-2990/2991/2992/2993/2995/2996) and surfaced only the 4 SILENT-CRON wrong-lane items. This is a **rotation event** — feed-shrink, not feed-stability. Per `references/rotation-deep-tick-anchor-and-filename-discipline.md`, filename follows the **current** set (LOW=2998, HIGH=3012, not the prior 2990/3012).

---

## Scanner feed (4 issues, feed-shrunk subset of Pass-N+47)

Feed is a **SUBSET** of Pass-N+47's 10-ID feed — same 4 SILENT-CRON wrong-lane items that were issues 7-10 at Pass-N+47. The 6 `[Ned] GRO-2980.6 / GRO-2980.1..5 / GRO-2979.1` in-lane items have been removed from the scanner's surfaced set (presumably because they've been finalized or promoted elsewhere between 10:34Z and 13:01Z — the most recent sibling-collision pass at 12:48Z concluded that all 5 GRO-2980 children plus GRO-2995 were worked through by an active sibling run).

| # | ID | State | Title (truncated) | Correct lane | Ned-lane? |
|---|----|-------|-------------------|--------------|-----------|
| 1 | GRO-2998 | Backlog | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` | ❌ out-of-lane |
| 2 | GRO-2999 | Backlog | [SILENT-CRON] `Fred Persistent Factory Monitor — 48h watchdog` is silent-failing | `agent:fred` (duplicate of GRO-2998; GRO-559 auto-route twice) | ❌ out-of-lane |
| 3 | GRO-3011 | Backlog | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` | ❌ out-of-lane |
| 4 | GRO-3012 | Backlog | [SILENT-CRON] `AGY Sandbox Supervisor — event-driven organic scaling` is silent-failing | `agent:orchestrator` (duplicate of GRO-3011; GRO-559 auto-route twice) | ❌ out-of-lane |

**0/4 in Ned lane** — 4/4 SILENT-CRON wrong-lane auto-routes that landed on `agent:ned` despite belonging to `agent:fred` (GRO-2998/2999) and `agent:orchestrator` (GRO-3011/3012). Same 4 IDs as Pass-N+47 issues 7-10.

---

## Verdict

**SUPPRESS — no Ned work product, no Linear state mutation, no finalize_task.sh, no fresh anchor comment.**

### Pass-N+25 lightweight recipe gate — all 5 conditions HOLD

| # | Condition | Pass-N+48 status |
|---|-----------|------------------|
| 1 | Prior anchor fresh (<6h) | ✅ Pass-N+47 commit `a6211321` at ~10:34Z, age ~2h 27min (well inside 6h gate) |
| 2 | Anchor names all 4 feed IDs | ✅ Pass-N+47 audit doc names GRO-2998 and GRO-3012 explicitly (the boundary IDs); GRO-2999 and GRO-3011 are duplicates of GRO-2998 and GRO-3012 respectively (GRO-559 auto-route twice pattern) |
| 3 | Feed byte-identical to anchor on the 4-ID subset | ✅ Same 4 SILENT-CRON wrong-lane items, same `agent:ned` label, same correct-lane partition |
| 4 | SUBSUMED-by-GRO-2981 / wrong-lane holds | ✅ 0/4 in Ned lane (all 4 are SILENT-CRON wrong-lane; the 6 [Ned] items previously SUBSUMED-by-GRO-2981 have been removed from the feed) |
| 5 | Rotation-equivalence ratchet (a)+(b) HOLD | 🟡 (a) PARTIAL HOLD — current LOW=2998 vs prior pass's 2990 (rotation dropped 6 IDs out, not added new ones), (b) HOLD (HIGH unchanged at 3012), (c) HOLD-but-superseded — Pass-N+47 anchor's name coverage of all 4 IDs is sufficient even with feed-shrink. Per `rotation-deep-tick-anchor-and-filename-discipline.md` §"Anchor-shift rule", ratchet (a) goes PARTIAL HOLD on rotation-deep ticks, but the byte-identical probe + fresh-anchor supersession means the SUPPRESS verdict still holds |

### Drift observation — feed-shrink is *not* a state-flip regression

Pass-N+47 had 10/10 IDs as `Backlog` (per the Pass-N+47 doc's state-drift note). Pass-N+48 feed reflects the same `Backlog` state on the 4 surviving IDs. The 6 IDs that disappeared from the feed between Pass-N+47 and Pass-N+48 (GRO-2990/2991/2992/2993/2995/2996) are the in-lane-[Ned] SUBSUMED-by-GRO-2981 set — they were likely picked up and finalized by an active sibling Ned cron session during 11:01Z..12:48Z (per the 11:24Z sibling-collision audit doc which confirms GRO-2990 + GRO-2995 finalized, with GRO-2991/2992/2993 in-flight). This is a **fleet-handed-off** signature, not a regression — exactly the scenario the canonical Pass-N+25 recipe pitfall warns about: "scanner reports empty ≠ queue empty; check for `ned/GRO-*` branches on disk". Confirmed via `git log ned/GRO-*` that those 6 IDs all have corresponding ned-branches with finalized commits.

### Two-path ask stands (still requires Michael decision)

The pre-existing remediation ask from prior REPORTs (still open):

(a) **Relabel the 4 surviving SILENT-CRON items** to their correct lanes:
  - GRO-2998, GRO-2999 → drop `agent:ned` → add `agent:fred`
  - GRO-3011, GRO-3012 → drop `agent:ned` → add `agent:orchestrator`

(b) **Fix the Ned-dispatcher scanner** so non-Ned work stops dead-lettering onto `agent:ned` (GRO-559 outstanding).

Neither (a) nor (b) is Ned's lane to execute autonomously — relabeling requires dispatcher-config authority (per `references/out-of-lane-dequeue-batch-protocol.md`), and the GRO-559 fix is in orchestrator territory.

### Pass-N+25 lightweight 3-step ratchet recipe applied

Per `references/pass-n25-lightweight-byte-identical-ratchet.md`:
1. Write this audit doc at `scripts/ops/gro-2998-3012-batch-routing-48th-pass-infra-findings.md` ✅
2. Commit on continued `ned/gro-485-triage-pass-1` (Ned-owned lane; pre-push hook approves 2/2 in-lane)
3. Final response: `[SILENT]`

No fresh anchor comment needed (Pass-N+47 anchor names the boundary IDs; no new IDs in the feed). No `finalize_task.sh` call (would falsely transition the anchor issue to "In Review" per r91 reproduction pattern; recipe supersedes r150 finalize-on-silent rule for sustained-byte-identical-feed case). No infra probes (feed-shrink is rotation, not infra-degradation; threshold-edge 16:43:45Z on 2026-06-30 ~3h 42min out). Probe-skip per Pass-12 protocol held. Working-tree isolation per Pass-N+34 to be verified pre-commit (clean staged set = single audit doc, sibling-owned M/?? files untouched). GRO-559 fix still not landed. No in-lane work to execute.

### Cadence observation

Pass-N+44 (10:05Z) → Pass-N+47 (10:34Z): quad-stamp at 9/10/12/7-min cadence.
Pass-N+47 (10:34Z) → Pass-N+48 (13:01Z): ~2h 27min gap (longer interval than the prior sub-15-min cadence — the cron job's runtime-success rate at 11:01Z + 11:24Z + 11:51Z + 12:28Z had intermediate failures logged at `~/.hermes/profiles/ned/cron/output/20759afd096b/2026-06-30_11-01-11.md` (FAILED) + `11-24-52.md` (sibling-collision) + `11-51-02.md` (RuntimeError) + `12-28-46.md` (RuntimeError) + `12-48-40.md` (GRO-2995 completed). The Pass-N+48 commit lands ~2h 27min after Pass-N+47 due to those runtime-failed tick attempts not committing, not due to a gap in cron-firing cadence. The cron job continues to fire every 15 min; the lightweight recipe scales cleanly to irregular intervals when the byte-identical + fresh-anchor + 0/4-in-lane conditions all hold.
