# GRO-3517 — Active merge contention winner decisions

Generated: 2026-07-06

## Scope

Parent: GRO-3514. This report resolves the three active contention blockers left after the terminal-state prune reduced `/api/gateway/merge/status` from 84 pending entries to the real active set.

Live status re-read from `http://127.0.0.1:9000/api/gateway/merge/status` during this pass:

| Field | Value |
|---|---:|
| `status` | `ok` |
| `pending_count` | 7 |
| `merged_count` | 266 |
| `last_scan` | `2026-07-04T03:48:55.345264+00:00` |
| `last_apply` | `null` |
| `drift_detected` | `false` |

The remaining pending set is the expected 7 active entries: 4 clean candidates for GRO-3518 plus these 3 contention blockers for GRO-3517.

## Decisions

| Issue | Hot file(s) | Merge-state signal | Live source / Linear signal | Decision | Next owner |
|---|---|---|---|---|---|
| GRO-2892 | `prismatic/dispatcher.py` | Tier 4, confidence 56.3, 232 diff lines. Sandbox patch wires a peer-review loop into `detect_origin_completions`. | Linear is `In Progress`, label `agent:jules`. Current `deploy-fresh` does **not** contain `is_high_impact`, `request_peer_review`, or `get_review_verdict`. | **Reject this sandbox as canonical merge winner.** It adds a blocking 5-minute polling loop in the dispatcher completion path, broad keyword-based high-impact detection, and label typo variants (`agent::...`) in production flow. It should not be force-merged into the dispatcher hot path. | Jules / orchestrator design pass. Keep GRO-2892 visible as active, but not auto-mergeable from this sandbox. |
| GRO-2991 | `prismatic/telemetry.py`, `prismatic/core/registry.py` | Tier 4, confidence 65.5, 203 diff lines. Sandbox patch adds hook telemetry and modifies `tests/`. | `origin/deploy-fresh` already has `TelemetryCollector.record_hook_fired()` and the `telemetry_hook_fired` table, but lacks hook-bus wiring in `PluginLoader.execute_hook()`. Local branch `ned/GRO-2991` has the lane-clean canonical diff: `prismatic/core/registry.py` plus co-located `prismatic/test_gro_2991_hook_bus_wiring.py`, 2 commits, no `tests/` lane violation. | **Pick `ned/GRO-2991` as canonical winner; reject the AGY sandbox patch as stale/noisy.** The canonical path is to merge/review `ned/GRO-2991`, not the pending sandbox that rewrites broader test files. | Peer review / merge owner for `ned/GRO-2991`. |
| GRO-3277 | `prismatic/gateway/server.py`, quota files | Tier 4, confidence 72.0, 1909 diff lines. Sandbox creates quota modules, static dashboard, and gateway API/UI routes. | Current source already has `prismatic/agy_quota.py`, `prismatic/agy_quota_state.py`, and `prismatic/gateway/static/quota.html` byte-identical to the sandbox. Live API smoke: `/api/quota` = HTTP 200, `/api/quota/history` = HTTP 200, `/api/quota/thresholds` = HTTP 200. But `/quota` is HTTP 404, so the user-facing dashboard route from the sandbox/result is **not** live. | **Intentionally retain, not apply wholesale.** Source-of-truth validation says the quota backend/static assets are present, but the public `/quota` route is missing. Do not force-merge the high-diff gateway patch or any generated/dist surface; create/apply a focused route fix after gateway owner review. | Fred / peer-review for quota dashboard route polish; Ned can verify a focused route patch later if dispatched. |

## Generated/dist guard

No active GRO-3517 decision requires merging generated `dist` output. The prior plugin-hub `dashboard/dist/index.html` entries are terminal/stale after the GRO-3516 prune path and are not part of this active 7-entry set. GRO-3277 uses `prismatic/gateway/static/quota.html` as a static source artifact; even there, the decision above is **retain** until `/quota` is fixed rather than force-merging the full server diff.

## Residual merge status after this decision

Re-read status remains `pending_count=7` by design:

- GRO-3518 owns the 4 clean active candidates: GRO-3168, GRO-3241, GRO-3295, GRO-3297.
- GRO-2892 is rejected as a canonical merge winner and should remain visible for Jules/orchestrator redesign.
- GRO-2991 has a canonical winner (`ned/GRO-2991`) and should move through peer review/merge using that branch, not the stale sandbox.
- GRO-3277 is intentionally retained until the missing `/quota` route is fixed from source-of-truth validation.

This satisfies GRO-3517 as a decision record: every active contention blocker is now either rejected, assigned a canonical winner, or intentionally retained with the next owner named.

## Verification evidence captured in this pass

- Live merge status was re-read from `/api/gateway/merge/status` and returned `pending_count=7`.
- Sandbox inspection endpoints were read for GRO-2892, GRO-2991, and GRO-3277; each had a RESULT.md plus a non-empty diff.
- Linear issue reads confirmed the current states/labels for the three blockers and their duplicate siblings.
- Live quota smoke: `/api/quota`, `/api/quota/history`, `/api/quota/thresholds` returned HTTP 200; `/quota` returned HTTP 404.
- Temporary `/tmp/gro3517_*` captures were removed after ad-hoc verification so the only durable changed path is this report.
