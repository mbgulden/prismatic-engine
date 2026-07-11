# GRO-3514 — Merge backlog triage

Generated: 2026-07-06

## Source read

Live gateway status was re-read from `http://127.0.0.1:9000/api/gateway/merge/status`.
The endpoint source is `prismatic/gateway/server.py:get_merge_status()`; it loads
`/home/ubuntu/.prismatic/merge-pipeline/state_v6.json` through the merge pipeline
state manager.

Observed status:

| Field | Value |
|---|---:|
| `status` | `ok` |
| `pending_count` | 84 |
| `merged_count` | 266 |
| `last_scan` | `2026-07-04T03:48:55.345264+00:00` |
| `last_apply` | `null` / empty |
| `drift_detected` | `false` |

Cross-check method: the 84 pending identifiers were queried against Linear in one
batched GraphQL alias query and classified by current Linear state plus merge-state
file contention metadata.

## Executive classification

| Bucket | Count | Meaning | Next action |
|---|---:|---|---|
| Stale terminal Linear issues | 77 | Merge pending state still contains issues whose Linear state is already `Done`. | Child [GRO-3516](https://linear.app/growthwebdev/issue/GRO-3516/merge-backlog-prune-terminal-linear-issues-from-pending-state): prune terminal states from pending or mark force-kept with rationale. |
| Clean active candidates | 4 | Nonterminal Linear issues with no merge-state contention files. | Child [GRO-3518](https://linear.app/growthwebdev/issue/GRO-3518/merge-backlog-apply-or-reject-the-4-clean-active-merge-candidates): apply/reject one by one. |
| Real contention blockers | 3 | Nonterminal Linear issues with contentions on hot files. | Child [GRO-3517](https://linear.app/growthwebdev/issue/GRO-3517/merge-backlog-decide-active-contention-winners-for): choose canonical winners or reject. |

Bottom line: this is not an 84-item active merge backlog. It is a 7-item active
backlog plus a 77-item stale-state leak. The stale leak is why `pending_count=84`
looks yellow while `drift_detected=false` and `last_apply=null`.

## Active backlog table

| Issue | Linear state | Merge tier | Confidence | Diff lines | Contention | Classification | Canonical action |
|---|---|---:|---:|---:|---|---|---|
| [GRO-3168](https://linear.app/growthwebdev/issue/GRO-3168) | In Progress | 2 | 72.0 | 165 | none | Clean active candidate | Process under [GRO-3518](https://linear.app/growthwebdev/issue/GRO-3518/merge-backlog-apply-or-reject-the-4-clean-active-merge-candidates). |
| [GRO-3241](https://linear.app/growthwebdev/issue/GRO-3241) | In Progress | 2 | 78.3 | 50 | none | Clean active candidate | Process under [GRO-3518](https://linear.app/growthwebdev/issue/GRO-3518/merge-backlog-apply-or-reject-the-4-clean-active-merge-candidates). |
| [GRO-3295](https://linear.app/growthwebdev/issue/GRO-3295) | Todo | 2 | 75.3 | 167 | none | Clean active candidate | Process under [GRO-3518](https://linear.app/growthwebdev/issue/GRO-3518/merge-backlog-apply-or-reject-the-4-clean-active-merge-candidates). |
| [GRO-3297](https://linear.app/growthwebdev/issue/GRO-3297) | In Progress | 2 | 71.3 | 358 | none | Clean active candidate | Process under [GRO-3518](https://linear.app/growthwebdev/issue/GRO-3518/merge-backlog-apply-or-reject-the-4-clean-active-merge-candidates). |
| [GRO-2892](https://linear.app/growthwebdev/issue/GRO-2892) | In Progress | 4 | 56.3 | 232 | `prismatic/dispatcher.py` | Real contention blocker | Treat as canonical winner for the exact `prismatic/dispatcher.py` signature; stale duplicates are GRO-2541 and GRO-2893. Needs manual merge/reject decision under [GRO-3517](https://linear.app/growthwebdev/issue/GRO-3517/merge-backlog-decide-active-contention-winners-for). |
| [GRO-2991](https://linear.app/growthwebdev/issue/GRO-2991) | In Progress | 4 | 65.5 | 203 | `prismatic/telemetry.py` | Real contention blocker | Keep blocked until telemetry writer path is reconciled; process under [GRO-3517](https://linear.app/growthwebdev/issue/GRO-3517/merge-backlog-decide-active-contention-winners-for). |
| [GRO-3277](https://linear.app/growthwebdev/issue/GRO-3277) | In Progress | 4 | 72.0 | 1909 | `prismatic/gateway/server.py` | Real contention blocker | High-diff quota dashboard / generated UI surface. Do not merge generated `dist` output without source validation; process under [GRO-3517](https://linear.app/growthwebdev/issue/GRO-3517/merge-backlog-decide-active-contention-winners-for). |

## Canonical winners and duplicate groups

Exact file-signature duplicate groups from pending state:

| File signature | Canonical winner | Stale duplicates | Reason |
|---|---|---|---|
| `prismatic/dispatcher.py` | [GRO-2892](https://linear.app/growthwebdev/issue/GRO-2892) | [GRO-2541](https://linear.app/growthwebdev/issue/GRO-2541), [GRO-2893](https://linear.app/growthwebdev/issue/GRO-2893) | GRO-2892 is the only nonterminal issue in the exact-signature group. |
| `plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.html`; `plugins/hermes-plugin-prismatic-hub/src/index.js`; `prismatic/gateway/server.py` | [GRO-3347](https://linear.app/growthwebdev/issue/GRO-3347) | [GRO-3336](https://linear.app/growthwebdev/issue/GRO-3336) | Both are already `Done`; canonical winner chosen by higher confidence / later issue. This group should be pruned by [GRO-3516](https://linear.app/growthwebdev/issue/GRO-3516/merge-backlog-prune-terminal-linear-issues-from-pending-state), not re-merged. |
| `prismatic/journal.py` | [GRO-3399](https://linear.app/growthwebdev/issue/GRO-3399) | [GRO-3384](https://linear.app/growthwebdev/issue/GRO-3384), [GRO-3396](https://linear.app/growthwebdev/issue/GRO-3396) | All are already `Done`; canonical winner chosen by highest confidence / latest issue. Prune, do not re-merge. |

Important: broad shared-file connected components create a giant 53-issue hairball
because `server.py`, `curator/lane.py`, plugin hub files, and dispatcher files are
hot paths. That is not a useful duplicate definition. Exact file signatures plus
Linear terminal state produce the actionable split.

## Top contention files

| File | Pending entries touching it |
|---|---:|
| `prismatic/gateway/server.py` | 19 |
| `prismatic/curator/lane.py` | 11 |
| `plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.html` | 9 |
| `prismatic/dispatcher.py` | 7 |
| `plugins/hermes-plugin-prismatic-hub/src/index.js` | 6 |
| `prismatic/gateway/event_bus.py` | 6 |
| `plugins/pwp/plugin-manifest.yaml` | 5 |
| `prismatic/telemetry.py` | 4 |
| `prismatic/cli/__init__.py` | 4 |

Most of these counts are inflated by the 77 terminal stale entries. After
[GRO-3516](https://linear.app/growthwebdev/issue/GRO-3516/merge-backlog-prune-terminal-linear-issues-from-pending-state), the only active contention files left from this triage should be
`prismatic/dispatcher.py`, `prismatic/telemetry.py`, and `prismatic/gateway/server.py`.

## Full pending-state distribution

| Linear state | Count |
|---|---:|
| Done | 77 |
| In Progress | 6 |
| Todo | 1 |

## Full issue list by classification

### Clean active candidates (4)

- [GRO-3168](https://linear.app/growthwebdev/issue/GRO-3168) — In Progress — `[FOUNDATIONAL] Crash dump capture on fatal`
- [GRO-3241](https://linear.app/growthwebdev/issue/GRO-3241) — In Progress — `[DISTRIBUTION] setup.py for legacy Python or remove it`
- [GRO-3295](https://linear.app/growthwebdev/issue/GRO-3295) — Todo — `[INFRA] Guard 2: Python universal env guard (canonical per doc 2)`
- [GRO-3297](https://linear.app/growthwebdev/issue/GRO-3297) — In Progress — `[INFRA] Visual verification epic: Nano Banana 2 + BrowserMCP for prismatic-engine`

### Real contention blockers (3)

- [GRO-2892](https://linear.app/growthwebdev/issue/GRO-2892) — In Progress — `prismatic/dispatcher.py` contention.
- [GRO-2991](https://linear.app/growthwebdev/issue/GRO-2991) — In Progress — `prismatic/telemetry.py` contention.
- [GRO-3277](https://linear.app/growthwebdev/issue/GRO-3277) — In Progress — `prismatic/gateway/server.py` contention.

### Stale terminal Linear issues (77)

These entries are counted as merge-pending by state_v6, but Linear says they are
already `Done`. They should be pruned from pending state unless a later follow-up
finds a force-keep reason.

GRO-512, GRO-593, GRO-1567, GRO-1614, GRO-2091, GRO-2193, GRO-2305, GRO-2353,
GRO-2355, GRO-2471, GRO-2541, GRO-2609, GRO-2735, GRO-2747, GRO-2750, GRO-2753,
GRO-2777, GRO-2781, GRO-2782, GRO-2784, GRO-2804, GRO-2893, GRO-2943, GRO-2979,
GRO-3042, GRO-3046, GRO-3057, GRO-3058, GRO-3060, GRO-3061, GRO-3066, GRO-3069,
GRO-3070, GRO-3071, GRO-3073, GRO-3079, GRO-3092, GRO-3093, GRO-3095, GRO-3096,
GRO-3098, GRO-3113, GRO-3136, GRO-3162, GRO-3167, GRO-3174, GRO-3202, GRO-3210,
GRO-3211, GRO-3216, GRO-3280, GRO-3281, GRO-3287, GRO-3312, GRO-3327, GRO-3328,
GRO-3329, GRO-3334, GRO-3336, GRO-3346, GRO-3347, GRO-3348, GRO-3350, GRO-3351,
GRO-3364, GRO-3374, GRO-3375, GRO-3377, GRO-3378, GRO-3380, GRO-3381, GRO-3383,
GRO-3384, GRO-3396, GRO-3397, GRO-3399, GRO-3403.

## Verification artifacts

- Live endpoint capture: `/tmp/gro3514_merge_status_live.json`.
- Linear alias query response: `/tmp/gro3514_pending_linear.json`.
- Classification stdout: `/tmp/gro3514_classification.txt`.
- Follow-up child issue creation log: `/tmp/gro3514_children_created.json`.

## Conclusion

GRO-3514 is satisfied as a classification ticket: the backlog now has explicit
counts, canonical winners for the duplicate signatures, and child slices for the
work that should not be widened into this audit.
