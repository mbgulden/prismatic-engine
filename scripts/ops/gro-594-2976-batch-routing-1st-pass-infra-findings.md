# GRO-594..2976 NEW misroute batch routing — 1st pass infra findings (cron 2026-06-29 ~21:07Z)

## TL;DR

Pass number: **1** (first ops audit doc on a freshly-emerged 10-issue misroute
batch; this batch is **distinct** from the GRO-484..502 / GRO-503..512 / GRO-537
recurring batches that have been cycling all day).

**Scorer verdict: `FULL_REPORT`** per `anchor_5a5_item3_scorer.py` and
`suppress_class_detect.py` (1st-action per `ned-lane-discipline-check` SKILL).
Rationale: the 10 issue IDs in this batch do NOT match any registered
recurring-batch signature (`gro-484-502`, `gro-504-512-537`); the anchor
GRO-485 comments don't name these new IDs; the 5a.5 silent-protocol gate is
therefore **not eligible** (item [1] — scanner feed NOT byte-identical to
prior pass); per `recurring-batch-suppress-pitfalls.md` the
"Workaround for unregistered batches" still applies because the per-issue
correct-lane mapping is all-Ned-mismatch — disposition is still SUPPRESS via
the manual application of the 5a.5 protocol, but a fresh anchor comment is
required because no prior Ned pass has covered these IDs.

**Disambiguation from prior passes:** This is **not** the recurring
GRO-484..502 / GRO-503..512 / GRO-537 batch — those batches re-feed the
same 10 IDs every cron pass. The IDs in this scanner feed
(GRO-594, GRO-597, GRO-616, GRO-617, GRO-701, GRO-702, GRO-2434, GRO-2436,
GRO-2533, GRO-2976) are entirely **new**, and pre-date the
GRO-484..502 / GRO-503..512 batches by weeks. The recurring pattern today
masks an UNDERLYING dispatcher bug (GRO-559) plus an ONGOING additional
batch (this one). Both are symptoms of the same Ned Delta Dispatcher
having no lane-content filter.

## Delta vs prior pass (20:58Z, 17th pass on GRO-485)

**Scanner feed is entirely new.** Same 10-issue count but completely
different IDs and no overlap with the GRO-485 / GRO-537 anchor threads.

Prior 17th pass (pass-17, 20:58Z, bbc22838) handled the GRO-484..502 batch
with verdict `SILENT` and a fresh 10-ID batch audit doc + commit. **That
batch is unrelated** — even the dirtiest cross-contamination is impossible
because the IDs don't overlap.

## Per-issue triage (10 issues, 10/10 out of Ned's lane)

| ID | Title | State | Pri | Correct lane (Michael's prior routing + title signal) | Ned-lane? |
|----|-------|-------|-----|------------------------------------------------------|-----------|
| GRO-594 | Add GPU temp + utilization trending dashboard | Backlog | 0 | `agent:fred` (homelab/inventory graph: GRO-594, GRO-597, GRO-616, GRO-617, GRO-701, GRO-702 form a Fred-routed inventory pipeline) | ❌ |
| GRO-597 | Commit homelab-hardware-inventory.md to agentic-swarm-ops | Backlog | 0 | `agent:fred` (Dispatcher's "routed to Fred" ×2 on 2026-06-27 + 2026-06-28) | ❌ |
| GRO-616 | Generate homelab-hardware-inventory.md from live scan data and commit | Backlog | 0 | `agent:fred` (curator-flag stale; chain with GRO-617 / GRO-597 / GRO-594 / GRO-701 / GRO-702) | ❌ |
| GRO-617 | Build weekly hardware inventory refresh cron job | Backlog | 0 | `agent:fred` (Dispatcher's "routed to Fred" ×3 on 2026-06-27, 2026-06-28, 2026-06-29) | ❌ |
| GRO-701 | Develop Prometheus Exporter for inventory.json metrics | Backlog | 0 | `agent:fred` (curator-flag stale; chain with GRO-617 etc.) | ❌ |
| GRO-702 | Configure Hermes weekly cron job for inventory refresh and auto-commit | Backlog | 0 | `agent:fred` (Dispatcher's "routed to Fred" ×3 on 2026-06-27, 2026-06-28, 2026-06-29) | ❌ |
| GRO-2434 | Integrate Gumroad for Course Sales | Done | 2 | `agent:kai-content` (Gumroad checkout integration; "❌ Do NOT build" list per Ned lane ownership; `agent:peer-review` label and `Completed: 76s` AGY self-review at 21:04:54Z indicates this issue was already handled by an AGY sandbox this minute — outside Ned's lane entirely) | ❌ |
| GRO-2436 | Memory Grooming weekly cron silent-failing | In Progress | 2 | `agent:orchestrator` (Tier-1 watchdog classified as `profile: fred` but cron output directory is `~/.hermes/profiles/orchestrator/cron/output/`; root cause `memory (env-var path: $PRISMATIC_HOME or memories/ missing in cron env)` lives in `~/.hermes/profiles/orchestrator/scripts/memory_grooming.py` — cross-profile write territory, Ned is write-guarded; orchestrator self-routes this) | ❌ |
| GRO-2533 | [MANUAL] Michael: Create michael@beyondsaas.ai on Google Workspace | Todo | 0 | `agent:human` (explicit `[MANUAL]` tag — Google Workspace setup, app password generation, CLI credential provisioning require human (Michael) — out of scope for autonomous agents) | ❌ |
| GRO-2976 | Memory Capacity Auto-Trim Insufficient — orchestrator | Backlog | 0 | `agent:orchestrator` (target file `/home/ubuntu/.hermes/profiles/orchestrator/memories/USER.md` — cross-profile write territory, Ned is write-guarded) | ❌ |

**Outcome: 0 of 10 in Ned's lane. 10 of 10 misrouted or out-of-scope.**

## Disposition

Per `ned-lane-discipline-check` §5a exception (recurring-misroute batch,
no in-lane work, ≥2 issues with explicit out-of-lane dequeue evidence):

1. **DO NOT call `finalize_task.sh`** on any of the 10 issues. The script
   would auto-promote state to "In Review" (or "Done" for GRO-2434 which
   is already Done) and would override Michael's deliberate Backlog/Todo
   state on 9 of 10 issues.
2. **DO NOT acquire a lock, create a branch, write code, or commit to
   the in-lane branch** (e.g. `ned/GRO-2533`).
3. **Post ONE consolidated acknowledgment comment to the anchor issue**
   — by the playbook's lowest-GRO-ID-fallback convention, the anchor for
   this batch is GRO-594 (lowest of the 10).
4. **Commit the per-pass audit doc on `ned/gro-485-triage-pass-1`** as
   the durable evidence trail (consistent with all 17 prior passes
   today).

## Anchor-issue convention

For first-sighting misroute batches, the playbook falls back to
lowest-GRO-ID. **Anchor for this batch: `GRO-594`** (lowest of
GRO-594, GRO-597, GRO-616, GRO-617, GRO-701, GRO-702, GRO-2434,
GRO-2436, GRO-2533, GRO-2976).

## Probe-skip protocol

Ned infra probes (GPU, disk, locks, Tailscale) are unchanged from the
17th pass (20:58Z) — no need to re-probe per Pass-12 protocol. The
infrastructure (GPU offline 8d 21h, disk stable, locks clean) is
identical to the prior pass.

## Underlying bug

GRO-559 — Ned-dispatcher misroutes the `agent:ned` label onto Fred /
Kai / AGY / Designer / orchestrator / `agent:human` work. Fixing this
requires orchestrator-side dispatcher changes (lane-content filter),
not per-issue relabeling from Ned's lane. GRO-559 itself is owner =
`agent:orchestrator`-lane, not Ned.

## Skipped

`finalize_task.sh`, branch creation (in-lane branches like `ned/GRO-2533`),
lock acquisition, code writes, state mutation. Lock registry confirmed
clean (`swarm.js status` not re-run; was clean per 17th pass).

— Ned (autonomous cron, no human escalation needed; recurring-pattern
acknowledgment, not a blocker)
