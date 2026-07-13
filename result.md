# Post-Publish Audit Report: Unintegrated Work and Stale Branches

This report presents the findings of a repository-wide audit for `mbgulden/prismatic-engine` to identify unintegrated work and stale branches with unmerged commits.

## Executive Summary
- **Total Branches Audited**: 236
- **Active Branches (<= 7 days old)**: 91
- **Stale Branches (> 7 days old)**: 145
- **Audit Finding Confirmation**: The warning trigger of '15 commits since last audit but only 1 merged PRs' is confirmed. It was driven by multiple feature branches (primarily associated with Ned's automated/cron triage tasks and development) that accumulated unmerged commits without being integrated into `origin/main`.

## Audit Context & Sample Commits
The audit request cited the following sample commits:
- `1d249054`, `7e2fd6ed`, `54683942`, `7213ec73`, `31bf0a49` (with reference to PR #33).

Investigation shows these commits belong to **`origin/ned/GRO-506`** (with `origin/ned/phase2-failure-classification` and `origin/ned/phase2-smoke-test` also containing them).
- **PR #33** corresponds to the commit `[Ned] Phase 1 Quality Gates: VerificationVerdict + DriftGate + label split (#33)` (SHA `ad811265`). This commit has been merged into the `ned/phase2-smoke-test` branch but is **not** integrated into `origin/main`.

## Top Unmerged Branches (By Commit Count)
Here are the top unmerged branches with their commit count, age, and latest commit info:

| Branch Name | Unmerged Commits | Last Commit Date | Age (Days) | Author | Latest Commit Subject |
| :--- | :---: | :--- | :---: | :--- | :--- |
| `origin/deploy-fresh` | 154 | Sat Jul 11 16:20:08 2026 -0600 | 1 | Michael Gulden | [Ned] Land observer wiring in live dispatcher (#GRO-3617) |
| `origin/feature/phase-d-fixes-2026-06-30` | 106 | Tue Jun 30 22:04:38 2026 +0000 | 12 | Ned | [Fred] Lane policy: Fred owns entire repo for orchestration (resolves GRO-3029) |
| `origin/ned/gro-485-triage-pass-1` | 96 | Tue Jun 30 13:06:14 2026 +0000 | 13 | Ned | [Ned] Add 48th-pass audit doc for feed-shrunk GRO-2998..3012 SILENT-CRON subset (cron 2026-06-30 ~13:01Z, PARTIAL rotation vs Pass-N+47 ~2h 27min prior — feed SHRANK from 10 IDs to 4 IDs dropping the 6 [Ned] SUBSUMED-by-GRO-2981 in-lane set that an active sibling Ned cron session finalized between 11:01Z and 12:48Z, 0/4 in Ned lane — 4/4 SILENT-CRON wrong-lane auto-routes, Pass-N+25 lightweight 3-step ratchet recipe applied (5-condition gate all HOLD: prior anchor GRO-2990 fresh ~2h 27min + names all 4 boundary IDs + feed-subset byte-identical on the 4 SILENT-CRON items + ratchet (a) PARTIAL HOLD per rotation-deep-tick rule supersedes (c) FAIL-but-benign per Pass-N+29), rotation-equivalence ratchet (a) PARTIAL HOLD + (b) HOLD + (c) HOLD-but-superseded, NO SUBSUMED-by-GRO-2981 items in current feed (the 6 [Ned] SUBSUMED ones were handed off + finalized by sibling session, confirmed via git log ned/GRO-* on GRO-2990/2991/2992/2993/2995/2996 branches), filename LOW + HIGH both shifted per Pass-N+31 'When both shift, shift both' sub-case rule (prior LOW=2990 → current LOW=2998, HIGH unchanged at 3012), state unchanged (Backlog on all 4 IDs, matches Pass-N+47 doc), no fresh anchor comment needed (Pass-N+47 anchor names boundary IDs GRO-2998 + GRO-3012 explicitly), threshold-edge 16:43:45Z on 2026-06-30 ~3h 42min out, probe-skip per Pass-12 held, working-tree isolation per Pass-N+34 verified pre-commit (clean staged set = single audit doc, no sibling-owned M/?? files), cadence observation ~2h 27min gap (longer than sub-15-min Pass-N+44..47 cadence due to 11:01Z + 11:24Z + 11:51Z + 12:28Z + 12:48Z runtime-failed tick attempts not committing — recipe scales cleanly to irregular intervals when conditions hold), GRO-559 fix still not landed, no in-lane work to execute) |
| `origin/ned/GRO-2391-verification` | 68 | Fri Jul 3 13:18:40 2026 +0000 | 10 | Ned | [Ned] Verify GRO-2391 webhook drain state (#GRO-2391) |
| `origin/feature/tier-5a-okf-pilot` | 66 | Thu Jun 25 10:09:20 2026 +0000 | 18 | Ned | [Fred] Normalize swarm lock heartbeat checks (#GRO-2401) |
| `origin/ned/GRO-3707` | 49 | Fri Jul 10 10:48:23 2026 +0000 | 3 | Ned | [Ned] Add theme validator dev dependency (#GRO-3707) |
| `origin/ned/GRO-2264-verify-2026-07-03` | 47 | Fri Jul 3 03:58:07 2026 +0000 | 10 | Ned | [Ned] Verify Morning Digest cron recovery (#GRO-2264) |
| `origin/ned/phase2-smoke-test` | 46 | Sun Jun 28 16:53:00 2026 +0000 | 14 | Fred | Address PR #36 review findings (Gap 5 smoke test) |
| `origin/ned/GRO-3533` | 45 | Sat Jul 11 14:42:28 2026 -0600 | 1 | Michael Gulden | Merge pull request #162 from mbgulden/ned/GRO-3534 |
| `origin/ned/GRO-3697` | 45 | Thu Jul 9 22:21:12 2026 +0000 | 3 | Ned | [Ned] WIP GRO-3697: fix removed override fixture |
| `origin/ned/GRO-3534` | 44 | Mon Jul 6 17:31:05 2026 +0000 | 6 | Ned | [Ned] Record quota freshness verification (#GRO-3534) |
| `origin/ned/GRO-3695` | 44 | Thu Jul 9 22:08:43 2026 +0000 | 3 | Ned | [Ned] WIP GRO-3695: format PWP theme installer |
| `origin/ned/phase2-failure-classification` | 44 | Sun Jun 28 16:38:51 2026 +0000 | 14 | Fred | Phase 2 / Gap 7: Address PR #35 review findings |
| `origin/ned/GRO-3121` | 43 | Sat Jul 4 14:33:20 2026 +0000 | 8 | Ned | [Ned] GRO-3121: lane-compliant tests and metric docs (#GRO-3121) |
| `origin/ned/GRO-2995` | 42 | Tue Jun 30 12:47:35 2026 +0000 | 13 | Ned | [Ned] GRO-2995: implement record_vertex_spend writer + drain handler + Phase B wiring (#GRO-2995) |
| `origin/ned/GRO-3274` | 42 | Wed Jul 8 13:42:05 2026 +0000 | 5 | Ned | [Ned] Clarify GRO-3274 live watchdog follow-up (#GRO-3274) |
| `origin/ned/GRO-3526` | 42 | Sat Jul 11 13:50:32 2026 -0600 | 2 | Michael Gulden | fix: add dev dependencies to pyproject.toml to resolve pytest import failure in plugin load gate (#GRO-3802) |
| `origin/ned/GRO-506` | 42 | Sun Jun 28 16:08:00 2026 +0000 | 14 | Ned | [Ned] GRO-506: triage note — 21st pass on 10-issue agent:ned batch, zero new infra deltas vs. 20th pass, growthwebdev.com 530 + 7d-cluster outage still unresolved (#GRO-506) |
| `origin/ned/GRO-2445-okf-drive-drift-check-push` | 41 | Sat Jul 11 09:32:51 2026 +0000 | 2 | Ned (Hermes Swarm) | [Ned] Refresh OKF Drive drift cron alert handling (#GRO-2445) |
| `origin/ned/GRO-2445-okf-drive-drift-refresh-push` | 41 | Sat Jul 11 09:32:51 2026 +0000 | 2 | Ned (Hermes Swarm) | [Ned] Refresh OKF Drive drift cron alert handling (#GRO-2445) |
| `origin/ned/GRO-3035` | 41 | Wed Jul 1 01:34:00 2026 +0000 | 12 | Ned | [Fred] Add factory_monitor.py + factory-monitor systemd timer (every 15 min) |
| `origin/ned/GRO-3557` | 41 | Tue Jul 7 09:28:29 2026 +0000 | 6 | Ned | [Ned] Add GRO-3557 verification report (#GRO-3557) |
| `origin/ned/GRO-2981` | 40 | Thu Jul 9 22:18:32 2026 +0000 | 3 | Ned | [Ned] Investigate dispatcher telemetry silence (#GRO-2981) |
| `origin/ned/GRO-3124` | 40 | Sat Jul 4 14:27:05 2026 +0000 | 8 | Ned | [Ned] GRO-3124: fix bak sweep verification harness |
| `origin/ned/GRO-3363` | 40 | Mon Jul 6 20:58:36 2026 +0000 | 6 | Ned | [Ned] Verify mounted plugin routes through FastAPI (#GRO-3363) |

## Detailed Analysis of Key Stale Branches

### 1. `origin/ned/GRO-506` (Stale)
- **Unmerged Commits**: 42
- **Last Commit Date**: Sun Jun 28 16:08:00 2026
- **Age**: 14 Days
- **Author**: Ned
- **Description**: Contains triage notes and automated health checks from Ned's 10-issue agent batch triage runs. Since the latest commit was made 14 days ago, this branch is considered stale.

### 2. `origin/ned/phase2-failure-classification` (Stale)
- **Unmerged Commits**: 44
- **Last Commit Date**: Sun Jun 28 16:38:51 2026
- **Age**: 14 Days
- **Author**: Fred
- **Description**: Part of the Phase 2 / Gap 7 work addressing PR #35 review findings. This branch is stale and should be reviewed for integration or cleanup.

### 3. `origin/ned/phase2-smoke-test` (Stale)
- **Unmerged Commits**: 46
- **Last Commit Date**: Sun Jun 28 16:53:00 2026
- **Age**: 14 Days
- **Author**: Fred
- **Description**: Addressing PR #36 review findings for Gap 5 smoke tests. Stale branch.

### 4. `origin/deploy-fresh` (Active)
- **Unmerged Commits**: 154
- **Last Commit Date**: Sat Jul 11 16:20:08 2026
- **Age**: 1 Day
- **Author**: Michael Gulden
- **Description**: This is the primary integration staging branch (`deploy-fresh`). It is active and has diverged from `main` by 154 commits due to ongoing feature validation and staging governance rules (where only Fred merges to `deploy-fresh` and promotions to `main` are batched).

### 5. `origin/feature/phase-d-fixes-2026-06-30` (Stale)
- **Unmerged Commits**: 106
- **Last Commit Date**: Tue Jun 30 22:04:38 2026
- **Age**: 12 Days
- **Author**: Ned
- **Description**: Contains orchestration lane policies (Fred owning the entire repo to resolve GRO-3029). Stale feature branch.

## Stale vs Active Branch Classification

### Stale Branches (> 7 days old)
The following branches have unmerged commits and have not been modified for more than 7 days:

| Branch Name | Unmerged Commits | Last Commit Date | Age (Days) | Subject |
| :--- | :---: | :--- | :---: | :--- |
| `origin/feature/phase-d-fixes-2026-06-30` | 106 | Tue Jun 30 22:04:38 2026 +0000 | 12 | [Fred] Lane policy: Fred owns entire repo for orchestration (resolves GRO-3029) |
| `origin/ned/gro-485-triage-pass-1` | 96 | Tue Jun 30 13:06:14 2026 +0000 | 13 | [Ned] Add 48th-pass audit doc for feed-shrunk GRO-2998..3012 SILENT-CRON subset (cron 2026-06-30 ~13:01Z, PARTIAL rotation vs Pass-N+47 ~2h 27min prior — feed SHRANK from 10 IDs to 4 IDs dropping the 6 [Ned] SUBSUMED-by-GRO-2981 in-lane set that an active sibling Ned cron session finalized between 11:01Z and 12:48Z, 0/4 in Ned lane — 4/4 SILENT-CRON wrong-lane auto-routes, Pass-N+25 lightweight 3-step ratchet recipe applied (5-condition gate all HOLD: prior anchor GRO-2990 fresh ~2h 27min + names all 4 boundary IDs + feed-subset byte-identical on the 4 SILENT-CRON items + ratchet (a) PARTIAL HOLD per rotation-deep-tick rule supersedes (c) FAIL-but-benign per Pass-N+29), rotation-equivalence ratchet (a) PARTIAL HOLD + (b) HOLD + (c) HOLD-but-superseded, NO SUBSUMED-by-GRO-2981 items in current feed (the 6 [Ned] SUBSUMED ones were handed off + finalized by sibling session, confirmed via git log ned/GRO-* on GRO-2990/2991/2992/2993/2995/2996 branches), filename LOW + HIGH both shifted per Pass-N+31 'When both shift, shift both' sub-case rule (prior LOW=2990 → current LOW=2998, HIGH unchanged at 3012), state unchanged (Backlog on all 4 IDs, matches Pass-N+47 doc), no fresh anchor comment needed (Pass-N+47 anchor names boundary IDs GRO-2998 + GRO-3012 explicitly), threshold-edge 16:43:45Z on 2026-06-30 ~3h 42min out, probe-skip per Pass-12 held, working-tree isolation per Pass-N+34 verified pre-commit (clean staged set = single audit doc, no sibling-owned M/?? files), cadence observation ~2h 27min gap (longer than sub-15-min Pass-N+44..47 cadence due to 11:01Z + 11:24Z + 11:51Z + 12:28Z + 12:48Z runtime-failed tick attempts not committing — recipe scales cleanly to irregular intervals when conditions hold), GRO-559 fix still not landed, no in-lane work to execute) |
| `origin/ned/GRO-2391-verification` | 68 | Fri Jul 3 13:18:40 2026 +0000 | 10 | [Ned] Verify GRO-2391 webhook drain state (#GRO-2391) |
| `origin/feature/tier-5a-okf-pilot` | 66 | Thu Jun 25 10:09:20 2026 +0000 | 18 | [Fred] Normalize swarm lock heartbeat checks (#GRO-2401) |
| `origin/ned/GRO-2264-verify-2026-07-03` | 47 | Fri Jul 3 03:58:07 2026 +0000 | 10 | [Ned] Verify Morning Digest cron recovery (#GRO-2264) |
| `origin/ned/phase2-smoke-test` | 46 | Sun Jun 28 16:53:00 2026 +0000 | 14 | Address PR #36 review findings (Gap 5 smoke test) |
| `origin/ned/phase2-failure-classification` | 44 | Sun Jun 28 16:38:51 2026 +0000 | 14 | Phase 2 / Gap 7: Address PR #35 review findings |
| `origin/ned/GRO-3121` | 43 | Sat Jul 4 14:33:20 2026 +0000 | 8 | [Ned] GRO-3121: lane-compliant tests and metric docs (#GRO-3121) |
| `origin/ned/GRO-2995` | 42 | Tue Jun 30 12:47:35 2026 +0000 | 13 | [Ned] GRO-2995: implement record_vertex_spend writer + drain handler + Phase B wiring (#GRO-2995) |
| `origin/ned/GRO-506` | 42 | Sun Jun 28 16:08:00 2026 +0000 | 14 | [Ned] GRO-506: triage note — 21st pass on 10-issue agent:ned batch, zero new infra deltas vs. 20th pass, growthwebdev.com 530 + 7d-cluster outage still unresolved (#GRO-506) |
| `origin/ned/GRO-3035` | 41 | Wed Jul 1 01:34:00 2026 +0000 | 12 | [Fred] Add factory_monitor.py + factory-monitor systemd timer (every 15 min) |
| `origin/ned/GRO-3124` | 40 | Sat Jul 4 14:27:05 2026 +0000 | 8 | [Ned] GRO-3124: fix bak sweep verification harness |
| `origin/ned/GRO-2391` | 39 | Sat Jul 4 17:34:46 2026 +0000 | 8 | [Ned] Stabilize webhook drain runtime path (#GRO-2391) |
| `origin/ned/GRO-3385` | 39 | Sat Jul 4 16:00:13 2026 +0000 | 8 | [Ned] verify GRO-3385 watchdog health (#GRO-3385) |
| `origin/ned/GRO-3396` | 39 | Sat Jul 4 16:16:57 2026 +0000 | 8 | [Ned] Add GRO-3396 verification report (#GRO-3396) |
| `origin/ned/GRO-3399` | 39 | Sat Jul 4 16:26:28 2026 +0000 | 8 | [Ned] Add GRO-3399 journal snapshot verification (#GRO-3399) |
| `origin/ned/GRO-3407` | 39 | Sun Jul 5 11:24:41 2026 +0000 | 8 | [Ned] Document GRO-3407 backup verification (#GRO-3407) |
| `origin/ned/GRO-3410` | 39 | Sun Jul 5 12:15:55 2026 +0000 | 8 | [Ned] Document GRO-3410 backup verification (#GRO-3410) |
| `origin/ned/GRO-2312` | 38 | Sat Jul 4 17:38:11 2026 +0000 | 8 | [Ned] Verify Ned cron scheduling recovered (#GRO-2312) |
| `origin/ned/GRO-2437` | 38 | Sat Jul 4 17:16:47 2026 +0000 | 8 | [Ned] Refresh GRO-2437 cron recovery verification (#GRO-2437) |
| `origin/ned/GRO-2438-verification-2026-07-03-clean` | 38 | Fri Jul 3 05:49:00 2026 +0000 | 10 | [Ned] Verify GRO-2438 cron recovery (#GRO-2438) |
| `origin/ned/GRO-2827` | 38 | Sat Jul 4 15:08:12 2026 +0000 | 8 | [Ned] Verify GRO-2827 log rotation cron cleanup (#GRO-2827) |
| `origin/ned/GRO-2979` | 38 | Mon Jun 29 22:21:55 2026 +0000 | 13 | [Ned] WIP GRO-2979: dispatcher process-observer + dedup dispatch caps |
| `origin/ned/GRO-3106` | 38 | Sat Jul 4 14:50:00 2026 +0000 | 8 | [Ned] Fix event router cleanup retention (#GRO-3106) |
| `origin/ned/GRO-3405` | 38 | Sun Jul 5 11:47:14 2026 +0000 | 8 | [Ned] Add GRO-3405 journal snapshot verification (#GRO-3405) |
| `origin/ned/GRO-3406` | 38 | Sun Jul 5 12:34:14 2026 +0000 | 8 | [Ned] Verify GRO-3406 quota cron successor (#GRO-3406) |
| `origin/ned/GRO-3408` | 38 | Sun Jul 5 11:10:51 2026 +0000 | 8 | [Ned] Fix GRO-3408 journal snapshot race (#GRO-3408) |
| `origin/ned/GRO-3409` | 38 | Sun Jul 5 12:24:50 2026 +0000 | 8 | [Ned] Verify GRO-3409 quota cron successor (#GRO-3409) |
| `origin/ned/GRO-72bc51` | 38 | Wed Jul 1 04:16:43 2026 +0000 | 12 | [Ned] GRO-72bc51: Empty commit after scratch_test.db removal |
| `origin/backup/pre-jules-ned-pr34-close-20260703T212849Z` | 37 | Sat Jun 27 19:45:01 2026 +0000 | 15 | [Fred] journal.py: handle _last_sync as timestamp string (GRO-XXXX) |
| `origin/backup/pre-jules-ned-pr35-close-20260703T212450Z` | 37 | Sat Jun 27 19:45:01 2026 +0000 | 15 | [Fred] journal.py: handle _last_sync as timestamp string (GRO-XXXX) |
| `origin/backup/pre-jules-ned-pr36-close-20260703T212035Z` | 37 | Sat Jun 27 19:45:01 2026 +0000 | 15 | [Fred] journal.py: handle _last_sync as timestamp string (GRO-XXXX) |
| `origin/backup/pre-jules-ned-pr44-close-20260703T211745Z` | 37 | Sat Jun 27 19:45:01 2026 +0000 | 15 | [Fred] journal.py: handle _last_sync as timestamp string (GRO-XXXX) |
| `origin/feature/gap13-plugin-load-gate` | 36 | Mon Jun 29 00:37:16 2026 +0000 | 14 | [Fred] Gap 13: ship-time plugin load verification gate (#GRO-1220) |
| `origin/feature/gap10-hello-world` | 35 | Sun Jun 28 23:56:04 2026 +0000 | 14 | [Ned] Gap 10: prismatic-hello-world reference plugin + PluginLoader hardening (#GRO-1218) |
| `origin/ned/gap11-wire-deferrals` | 35 | Sun Jun 28 22:32:22 2026 +0000 | 14 | [Ned] Gap 11 Fix 2: action_rules separated from impact_rules (#GRO-1219) |
| `origin/ned/gap9-qualityfinding-export` | 33 | Sun Jun 28 19:38:57 2026 +0000 | 14 | [Ned] Gap 9 follow-up: export QualityFinding from prismatic.review |
| `origin/ned/GRO-2876` | 31 | Sun Jun 28 04:41:45 2026 +0000 | 15 | Revert "[ned] GRO-2876: finalize (auto-commit on budget exhaustion)" |
| `origin/ned/GRO-572` | 30 | Fri Jun 26 15:59:50 2026 +0000 | 16 | [Ned] GRO-572: move social tests into prismatic/social/tests/ (lane compliance) |
| `origin/ned/phase1-quality-gates` | 30 | Sun Jun 28 04:16:22 2026 +0000 | 15 | Fix peer-review findings: path traversal, false tests, side-effects |
| `origin/ned/GRO-2264` | 29 | Sat Jul 4 17:43:57 2026 +0000 | 8 | [Ned] Verify Morning Digest cron recovery (#GRO-2264) |
| `origin/ned/GRO-555` | 29 | Fri Jun 26 09:13:20 2026 +0000 | 17 | [Ned] GRO-555: Fix APIRouter prefix so paths land under /api/v1/router (#GRO-555) |
| `origin/feature/engine-v0.1-stack` | 28 | Thu Jun 18 11:25:39 2026 +0000 | 25 | [Fred] Add websockets to gateway test extra (#GRO-1968) |
| `origin/ned/GRO-2907` | 28 | Sun Jun 28 12:23:31 2026 +0000 | 15 | [Ned] GRO-2907: move test_loop_branch_detector.py into prismatic/quality/ (Ned-lane compliant) |
| `origin/ned/GRO-547` | 28 | Fri Jun 26 14:56:30 2026 +0000 | 16 | [ned] GRO-547: finalize (auto-commit on budget exhaustion) |
| `origin/ned/GRO-548` | 28 | Fri Jun 26 14:31:48 2026 +0000 | 16 | [Ned] GRO-548: move intake tests into prismatic/api/tests/ to comply with ned lane |
| `origin/ned/GRO-2351` | 27 | Sat Jun 27 06:38:47 2026 +0000 | 16 | [Ned] GRO-2351: complete inventory — 5 missing PWP files (rows 26-30) |
| `origin/ned/GRO-549` | 27 | Fri Jun 26 13:21:01 2026 +0000 | 17 | [Ned] Move GRO-549 test to prismatic/core/tests/ (Ned lane compliance) |
| `origin/ned/GRO-550` | 27 | Fri Jun 26 15:50:51 2026 +0000 | 16 | [Ned] GRO-550: move dispatcher integration test into prismatic/core/tests/ (lane compliance) |
| `origin/ned/GRO-551` | 27 | Fri Jun 26 11:39:40 2026 +0000 | 17 | [Ned] GRO-551: Fix half-open probe serialization and first-attempt context attach |
| `origin/ned/GRO-653` | 27 | Fri Jun 26 01:12:03 2026 +0000 | 17 | [Ned] GRO-653: V2 screenshot-ready format + validator + Becca-feedback checklist |
| `origin/ned/gro-1783-path-parameterization` | 27 | Mon Jun 15 22:54:56 2026 +0000 | 27 | [Fred] Integrate Jules CLI GRO-1517: systemd service parameterization + install.sh interactive setup (#GRO-1517) |
| `origin/ned/gro-1814-identity-registry` | 27 | Tue Jun 16 04:57:37 2026 +0000 | 27 | [Ned] GRO-1814: Implement agent identity registry scaffolding (#GRO-1814) |
| `origin/ned/GRO-2506` | 26 | Fri Jun 26 04:29:14 2026 +0000 | 17 | [Ned] GRO-2506: defer workspace/OKF path resolution to call time (fix monkeypatch in tests) |
| `origin/ned/GRO-570` | 26 | Fri Jun 26 08:31:54 2026 +0000 | 17 | [Ned] GRO-570: record inventory results and findings |
| `origin/ned/GRO-571` | 26 | Fri Jun 26 20:21:40 2026 +0000 | 16 | [Ned] GRO-571: tagging results report (#GRO-571) |
| `origin/ned/gro-1812-path-fallback` | 26 | Tue Jun 16 04:52:24 2026 +0000 | 27 | [Ned] GRO-1812: Replace /home/ubuntu fallback with '.' in prismatic source (#GRO-1812) |
| `origin/ned/gro-1813-workspace-router` | 26 | Tue Jun 16 04:55:49 2026 +0000 | 27 | [Ned] GRO-1813: Implement workspace router config and path resolver (#GRO-1813) |
| `origin/ned/phase2-smoke-test-v2` | 26 | Sun Jun 28 16:55:50 2026 +0000 | 14 | Phase 2 / Gap 5: Smoke test layer (rebased on deploy-fresh) |
| `origin/ned/GRO-2313-clean` | 25 | Fri Jun 26 03:26:51 2026 +0000 | 17 | [Ned] GRO-2313: webhook verification script (clean rebase off deploy-fresh) |
| `origin/ned/GRO-508` | 25 | Sat Jun 27 23:36:20 2026 +0000 | 15 | [Ned] GRO-508: triage note — 10-issue agent:ned backlog is scanner routing bug, not infra work (#GRO-508) |
| `origin/ned/GRO-509` | 25 | Sun Jun 28 01:25:39 2026 +0000 | 15 | [Ned] GRO-509: triage note — 10-issue agent:ned backlog is recurring scanner routing bug, not infra work (#GRO-509) |
| `origin/ned/GRO-542` | 25 | Sat Jun 27 22:16:06 2026 +0000 | 15 | [Ned] GRO-542: triage note — contact/booking flow is coder/integrations lane, not infra (#GRO-542) |
| `origin/ned/GRO-545` | 25 | Sat Jun 27 20:56:23 2026 +0000 | 15 | [Ned] GRO-545: triage note — social-proof testimonials is marketing/design, not infra (#GRO-545) |
| `origin/ned/GRO-558` | 25 | Sat Jun 27 19:16:43 2026 +0000 | 15 | [Ned] GRO-558: triage note — landing pages is marketing/design, not infra (#GRO-558) |
| `origin/ned/GRO-559` | 25 | Sat Jun 27 13:54:47 2026 +0000 | 16 | [Ned] GRO-559: triage note — email capture/lead magnet is marketing, not infra (#GRO-559) |
| `origin/ned/gro-1774-gitignore` | 25 | Mon Jun 15 18:35:50 2026 +0000 | 27 | [Ned-Code] GRO-1774: Add .gitignore entries for .env and .venv_dev/ + untrack venv |
| `origin/feature/gro-1623-master-build-orchestrator` | 24 | Mon Jun 15 16:35:50 2026 +0000 | 27 | [Fred] Integrate Jules GRO-1623: master_build_orchestrator.py (#GRO-1623) |
| `origin/feature/gro-1673-merge-tests` | 22 | Mon Jun 15 08:18:39 2026 +0000 | 28 | [Fred] Add fallback router and loop integration tests (#GRO-1673) |
| `origin/feature/chat-agy-capability` | 21 | Thu Jun 18 10:48:35 2026 +0000 | 25 | [Fred] GRO-1969: Add chat.agy capability + read-only gateway endpoints |
| `origin/feature/capability-vcs-github` | 20 | Thu Jun 18 10:45:45 2026 +0000 | 25 | [Fred] GRO-1972 research: Linear API rate-limit audit + optimization spec + implementation plan |
| `origin/feature/doctor-module-extraction` | 20 | Thu Jun 18 10:42:26 2026 +0000 | 25 | [Fred] GRO-1971: Extract prismatic-engine doctor into prismatic/cli/doctor.py |
| `origin/feature/real-schedule-adapters` | 20 | Thu Jun 18 10:45:34 2026 +0000 | 25 | [Fred] GRO-1970: Real agy + jules schedule adapters with explicit fallback paths |
| `origin/feature/gro-1993` | 13 | Wed Jun 24 11:41:41 2026 +0000 | 19 | [AGENT] Tier 1a: Wire agent:ned-code/infra/audit/review into AGENT_L (#GRO-1993) |
| `origin/ned/gro-1592-hd-synthesis-agent` | 12 | Sun Jun 14 02:43:55 2026 +0000 | 29 | [Ned] GRO-1592: Scaffold HD Synthesis Backend Agent |
| `origin/ned/gro-1648-breaker-remediation` | 9 | Mon Jun 15 13:11:46 2026 +0000 | 28 | [Fred] Remediate breaker auth and audit logging (#GRO-1648) |
| `origin/ned/gro-1671-integrate` | 9 | Mon Jun 15 06:41:25 2026 +0000 | 28 | [Ned] Add Integrate phase (Step 7) — merge, assemble, test, manifest (#GRO-1671) |
| `origin/ned/gro-1648-breaker` | 8 | Mon Jun 15 06:20:21 2026 +0000 | 28 | [Ned] Build prismatic-breaker HITL intervention engine (#GRO-1648) |
| `origin/ned/gro-1675-agy-model-routing` | 8 | Mon Jun 15 06:54:30 2026 +0000 | 28 | [Ned] Implement AGY label-based model routing in dispatcher (#GRO-1675) |
| `origin/fix/gro-1591-vertex-ai-telemetry-monitor` | 7 | Sun Jun 14 00:59:51 2026 +0000 | 29 | [Jules] Vertex AI Credit and Quota Telemetry Monitor (#GRO-1591) |
| `origin/ned/gro-1620-sandbox` | 7 | Mon Jun 15 06:33:28 2026 +0000 | 28 | [Ned-Code] GRO-1620: Implement containerized worker sandbox via k3s pods |
| `origin/ned/gro-1674-agent-cards` | 7 | Mon Jun 15 06:18:49 2026 +0000 | 28 | [Ned] GRO-1674: Add Agent Cards + Telegram inline controls for mode switch (#GRO-1674) |
| `origin/feature/gro-1561-hook-deployment` | 6 | Sun Jun 14 00:38:05 2026 +0000 | 29 | [Jules] Set Fred's lane to * (orchestrator owns all) (#GRO-1561) |
| `origin/ned/gro-1670-rfr-loop` | 6 | Mon Jun 15 05:20:39 2026 +0000 | 28 | [Ned] Add ReviewFeedbackRefineLoopEngine — RFR cycle with AGY integration, quality gates, retry limits (#GRO-1670) |
| `origin/ned/gro-1672-mode-switch-clean` | 6 | Mon Jun 15 05:20:39 2026 +0000 | 28 | [Ned] Add ReviewFeedbackRefineLoopEngine — RFR cycle with AGY integration, quality gates, retry limits (#GRO-1670) |
| `origin/ned/gro-1618-api-gateway` | 5 | Mon Jun 15 05:46:49 2026 +0000 | 28 | [Ned-Code] GRO-1618: Fix health endpoint — add auth dependency (cherry-pick fix) |
| `origin/feature/linear-budget-engine-module` | 4 | Fri Jun 19 05:09:20 2026 +0000 | 24 | linear-budget: move module into prismatic-engine, add lint script |
| `origin/ned/GRO-1223` | 4 | Fri Jun 12 16:02:40 2026 +0000 | 30 | [AGY] Sync dashboard build outputs and routing handlers (#GRO-1223) |
| `origin/ned/gro-1669-state-machine` | 4 | Mon Jun 15 04:24:08 2026 +0000 | 28 | [Ned] Add 7-step PipelineStateMachine + ModeSwitch — core state machine for Epic 6 (#GRO-1669) |
| `origin/feature/phase1-specs` | 3 | Sat Jun 13 05:22:37 2026 +0000 | 30 | [Fred] Add Jules session tracker for Phase 1 parallel swarm (#GRO-1517) |
| `origin/fix/hardware-profiles` | 3 | Mon Jun 15 04:04:30 2026 +0000 | 28 | [Jules] Add hardware execution profiles schema + PluginLoader validation (#GRO-1614) |
| `origin/ned/gro-1824-plugin-telemetry` | 3 | Tue Jun 16 15:57:51 2026 +0000 | 26 | [Fred] GRO-1825: Update Fred lane to [*] for orchestrator cross-lane access |
| `origin/ned/headless-env` | 3 | Sat Jun 13 23:52:56 2026 +0000 | 29 | [Jules] GRO-1589 — remove core/telemetry copy (redundant, prismatic/core/telemetry is canonical) |
| `origin/feature/gro-1823-gvisor-runtime-1488509838444020558` | 2 | Fri Jul 3 20:01:24 2026 +0000 | 9 | [Ned] GRO-1823: Finalize gVisor runtime support and acknowledge supersedence |
| `origin/fix/gro-1613-multi-platform-cooker` | 2 | Sun Jun 14 23:55:52 2026 +0000 | 28 | [Jules] Add multi_platform_cooker.py MVP for PC/Steam Deck/PS5 profiles (#GRO-1613) |
| `origin/governor-allocation-lifecycle-1107098321864387251` | 2 | Fri Jul 3 20:54:04 2026 +0000 | 9 | Finalize Distributed Compute Governor implementation |
| `origin/gro-1521-telemetry-hooks-17058662210687217397` | 2 | Fri Jul 3 18:52:43 2026 +0000 | 9 | GRO-1521: Finalize PR after acknowledging superseded status |
| `origin/jules/swarm-ipc-bridge-recreate-server-13282024616854638039` | 2 | Fri Jul 3 19:39:56 2026 +0000 | 9 | [AGENT] Finalizing task closure: Superseded by #88 |
| `origin/ned-gro-2205-gvisor-runtime-hardened-12213006512082573070` | 2 | Sat Jul 4 01:26:29 2026 +0000 | 9 | [Ned] GRO-2205: Robustify lifecycle manager DB loading (#GRO-2205) |
| `origin/ned/GRO-1316` | 2 | Fri Jun 12 17:10:05 2026 +0000 | 30 | [Ned] Build automated research-to-task decomposer — parses AGY/Kai comments, extracts deliverables + tasks, auto-creates Linear issues (#GRO-1317) |
| `origin/ned/GRO-3273` | 2 | Sat Jul 4 15:53:03 2026 +0000 | 8 | [Ned] Refresh GRO-3273 silent cron verification (#GRO-3273) |
| `origin/ned/gro-1646-edge-guards` | 2 | Mon Jun 15 13:07:26 2026 +0000 | 28 | [Fred] Restore Vertex telemetry module for systemd poller (#GRO-1591) |
| `origin/ned/gro-1823-gvisor-runtime-4581400630964588884` | 2 | Fri Jul 3 20:02:02 2026 +0000 | 9 | [Ned] Acknowledge supersede and close PR. |
| `origin/refine-install-and-cli-6085762468004995322` | 2 | Fri Jul 3 19:34:40 2026 +0000 | 9 | Acknowledge PR superseded and stopping work. |
| `origin/agent-hd-synthesis-scaffold-4829638201061480543` | 1 | Fri Jul 3 11:19:00 2026 +0000 | 10 | [AGENT] Scaffold HD Synthesis Backend Agent (#HD-1) |
| `origin/execution-curator-streams-GRO-2866-4734926026656210369` | 1 | Fri Jul 3 10:46:02 2026 +0000 | 10 | [Jules] Curator Streams 1+2 refactor (GRO-2866) |
| `origin/feature-gro-1618-api-gateway-17233514225241463851` | 1 | Fri Jul 3 11:25:02 2026 +0000 | 10 | [Jules] Implement FastAPI API gateway for job submission and credits check (#GRO-1618) |
| `origin/feature-gro-1823-gvisor-runtime-1488509838444020558` | 1 | Fri Jul 3 11:26:38 2026 +0000 | 10 | [Ned] GRO-1823: Implement gVisor runtime support for plugin sandboxes |
| `origin/feature/journal-sync-string-handling` | 1 | Sat Jun 27 19:45:01 2026 +0000 | 15 | [Fred] journal.py: handle _last_sync as timestamp string (GRO-XXXX) |
| `origin/feature/providers-attach` | 1 | Thu Jun 18 23:15:18 2026 +0000 | 24 | [Fred] Integrate research utility and provider attach (#GRO-1980 #GRO-1982) |
| `origin/fix-GRO-1517-systemd-path-config-13799748940670824865` | 1 | Fri Jul 3 11:23:25 2026 +0000 | 10 | GRO-1517: Systemd service and path configuration |
| `origin/fix-dispatcher-activation-signal-agents-3112114326627074541` | 1 | Fri Jul 3 11:21:54 2026 +0000 | 10 | fix: enable dispatcher activation for signal-based agents |
| `origin/fix-gro-2205-gvisor-support-13338769333584450571` | 1 | Fri Jul 3 11:17:42 2026 +0000 | 10 | [Jules] Implement gVisor runtime support for sandbox plugins (#2205) |
| `origin/fix/GRO-1517-systemd-path-config-13799748940670824865` | 1 | Fri Jul 3 19:34:00 2026 +0000 | 9 | GRO-1517: Acknowledged as superseded by PR #96. |
| `origin/fred-gro-2196-supervisor-fixes-5666907232575744302` | 1 | Fri Jul 3 11:08:01 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) (#GRO-2196) |
| `origin/fred-supervisor-fixes-GRO-2196-5117141796951370469` | 1 | Fri Jul 3 11:08:29 2026 +0000 | 10 | [Fred] update supervisor heartbeat to mtime and mandate RESULT.md (#GRO-2196) |
| `origin/fred-supervisor-fixes-GRO-2196-8181094042036142091` | 1 | Fri Jul 3 11:08:18 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) |
| `origin/fred-supervisor-fixes-heartbeat-result-md-15212598510849814849` | 1 | Fri Jul 3 11:26:06 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) |
| `origin/fred-supervisor-fixes-mtime-result-md-15908310939482243785` | 1 | Fri Jul 3 11:02:06 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) |
| `origin/fred-validate-supervisor-fixes-15742618630542730635` | 1 | Fri Jul 3 11:15:13 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) |
| `origin/fred-validate-supervisor-fixes-16339148130370398780` | 1 | Fri Jul 3 11:07:50 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) |
| `origin/fred-validate-supervisor-fixes-910715970730585050` | 1 | Fri Jul 3 11:17:25 2026 +0000 | 10 | [Fred] Transition heartbeat to mtime + mandate RESULT.md post-con (#GRO-2196) |
| `origin/fred-validate-supervisor-fixes-GRO-2196-3504293600156634527` | 1 | Fri Jul 3 11:12:16 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) (#GRO-2196) |
| `origin/fred-validate-supervisor-fixes-mtime-result-md-4903471965189654678` | 1 | Fri Jul 3 11:17:14 2026 +0000 | 10 | [Fred] Validate supervisor fixes (heartbeat mtime + RESULT.md post-con) |
| `origin/jules-GRO-2205-gvisor-runtime-support-4917459844011526215` | 1 | Fri Jul 3 11:13:29 2026 +0000 | 10 | [Jules] GRO-2205: Implement gVisor runtime support for sandbox pods |
| `origin/jules-swarm-ipc-bridge-recreate-server-13282024616854638039` | 1 | Fri Jul 3 11:19:13 2026 +0000 | 10 | [AGENT] GRO-1567: Recreate Swarm IPC Bridge server with event ingest and WebSocket (#1567) |
| `origin/jules/GRO-2205-gvisor-runtime-support-4917459844011526215` | 1 | Fri Jul 3 17:56:43 2026 +0000 | 9 | [Jules] Acknowledge PR closure as duplicate of #82 and #68 |
| `origin/ned-GRO-1823-gvisor-runtime-5499805987157484255` | 1 | Fri Jul 3 11:15:52 2026 +0000 | 10 | [Ned] Implement gVisor support in SandboxPodManager (#GRO-1823) |
| `origin/ned-gro-1823-gvisor-runtime-13267768504582971242` | 1 | Fri Jul 3 17:18:56 2026 +0000 | 9 | [Ned] GRO-1823: Implement gVisor Runtime support for Sandbox pods |
| `origin/ned-gro-1823-gvisor-runtime-4581400630964588884` | 1 | Fri Jul 3 11:12:43 2026 +0000 | 10 | [Ned] GRO-1823: Implement gVisor runtime support for plugin sandboxes (#GRO-2205) |
| `origin/ned/GRO-1222` | 1 | Fri Jun 12 12:47:42 2026 +0000 | 31 | feat(command-deck): Design agent controls and task queue (#GRO-1222) |
| `origin/ned/GRO-2993` | 1 | Fri Jul 3 09:17:58 2026 +0000 | 10 | [Ned] Record plugin registration telemetry (#GRO-2993) |
| `origin/ned/GRO-3112-vacuum-runner` | 1 | Fri Jul 3 09:01:50 2026 +0000 | 10 | [Ned] Build unified SQLite vacuum runner (#GRO-3112) |
| `origin/ned/GRO-3384-journal-snapshot-fix-v2` | 1 | Fri Jul 3 11:22:47 2026 +0000 | 10 | [Ned] Fix journal snapshot string sync crash (#GRO-3384) |
| `origin/ned/GRO-3393-journal-snapshot-fix-wt` | 1 | Sat Jul 4 01:41:56 2026 +0000 | 9 | [Ned] Fix journal snapshot string sync crash (#GRO-3393) |
| `origin/ned/fix-gitignore-env-GRO-1786` | 1 | Mon Jun 15 23:53:44 2026 +0000 | 27 | [Ned-Code] Add .env to .gitignore (#GRO-1786) |
| `origin/ned/gro-1569-sandbox-review` | 1 | Mon Jun 15 08:08:18 2026 +0000 | 28 | [Ned-Code] Implement sandbox isolation manager (#GRO-1569) |
| `origin/ned/gro-1581-prometheus-metrics` | 1 | Sun Jun 14 20:09:55 2026 +0000 | 28 | [Ned] Implement Prometheus metrics endpoint — counters, histograms, gauges, /metrics HTTP server, lock instrumentation (#GRO-1581) |
| `origin/ned/gro-1620-containerized-worker-sandbox` | 1 | Mon Jun 15 12:56:37 2026 +0000 | 28 | [Fred] Add containerized worker sandbox manager (#GRO-1620) |
| `origin/ned/gro-1815-pipeline-trigger` | 1 | Tue Jun 16 05:39:51 2026 +0000 | 27 | [Ned-Code] GRO-1815: Implement cross-project pipeline trigger events (#GRO-1815) |
| `origin/ned/gro-1820-plugin-marketplace-registry` | 1 | Tue Jun 16 07:36:34 2026 +0000 | 27 | [Ned] GRO-1820: Plugin Marketplace Registry API — SQLite-backed plugin index with search, filter, pagination |
| `origin/ned/gro-1821-version-compat-resolver` | 1 | Tue Jun 16 10:40:30 2026 +0000 | 27 | [Ned] Version Compatibility Resolver — conflict detection, resolution report, matrix (#GRO-1821) |
| `origin/ned/gro-1822-plugin-lifecycle-manager` | 1 | Tue Jun 16 12:10:10 2026 +0000 | 27 | [Ned] Plugin Lifecycle Sandbox Manager — state machine, sandbox pods, SQLite persistence, dispatcher integration (#GRO-1822) |
| `origin/ned/gro-1829-egress-scanner` | 1 | Tue Jun 16 08:29:58 2026 +0000 | 27 | [Ned] GRO-1829: Egress Secret & PII Scanner Hook — regex/entropy scanner that blocks secrets before egress |
| `origin/ned/gro-1832-security-policy` | 1 | Tue Jun 16 12:06:04 2026 +0000 | 27 | checkpoint |

### Active Branches (<= 7 days old)
The following branches are active and have unmerged commits that are less than 7 days old:

| Branch Name | Unmerged Commits | Last Commit Date | Age (Days) | Subject |
| :--- | :---: | :--- | :---: | :--- |
| `origin/deploy-fresh` | 154 | Sat Jul 11 16:20:08 2026 -0600 | 1 | [Ned] Land observer wiring in live dispatcher (#GRO-3617) |
| `origin/ned/GRO-3707` | 49 | Fri Jul 10 10:48:23 2026 +0000 | 3 | [Ned] Add theme validator dev dependency (#GRO-3707) |
| `origin/ned/GRO-3533` | 45 | Sat Jul 11 14:42:28 2026 -0600 | 1 | Merge pull request #162 from mbgulden/ned/GRO-3534 |
| `origin/ned/GRO-3697` | 45 | Thu Jul 9 22:21:12 2026 +0000 | 3 | [Ned] WIP GRO-3697: fix removed override fixture |
| `origin/ned/GRO-3534` | 44 | Mon Jul 6 17:31:05 2026 +0000 | 6 | [Ned] Record quota freshness verification (#GRO-3534) |
| `origin/ned/GRO-3695` | 44 | Thu Jul 9 22:08:43 2026 +0000 | 3 | [Ned] WIP GRO-3695: format PWP theme installer |
| `origin/ned/GRO-3274` | 42 | Wed Jul 8 13:42:05 2026 +0000 | 5 | [Ned] Clarify GRO-3274 live watchdog follow-up (#GRO-3274) |
| `origin/ned/GRO-3526` | 42 | Sat Jul 11 13:50:32 2026 -0600 | 2 | fix: add dev dependencies to pyproject.toml to resolve pytest import failure in plugin load gate (#GRO-3802) |
| `origin/ned/GRO-2445-okf-drive-drift-check-push` | 41 | Sat Jul 11 09:32:51 2026 +0000 | 2 | [Ned] Refresh OKF Drive drift cron alert handling (#GRO-2445) |
| `origin/ned/GRO-2445-okf-drive-drift-refresh-push` | 41 | Sat Jul 11 09:32:51 2026 +0000 | 2 | [Ned] Refresh OKF Drive drift cron alert handling (#GRO-2445) |
| `origin/ned/GRO-3557` | 41 | Tue Jul 7 09:28:29 2026 +0000 | 6 | [Ned] Add GRO-3557 verification report (#GRO-3557) |
| `origin/ned/GRO-2981` | 40 | Thu Jul 9 22:18:32 2026 +0000 | 3 | [Ned] Investigate dispatcher telemetry silence (#GRO-2981) |
| `origin/ned/GRO-3363` | 40 | Mon Jul 6 20:58:36 2026 +0000 | 6 | [Ned] Verify mounted plugin routes through FastAPI (#GRO-3363) |
| `origin/ned/GRO-3476` | 40 | Mon Jul 6 19:48:58 2026 +0000 | 6 | [Ned] Tighten label debt noise detection (#GRO-3476) |
| `origin/ned/GRO-3481` | 40 | Mon Jul 6 19:24:44 2026 +0000 | 6 | [Ned] Move queue health tests into Ned lane (GRO-3481) |
| `origin/ned/GRO-3789` | 40 | Sat Jul 11 09:24:57 2026 +0000 | 2 | [Ned] Land journal health filter patch (#GRO-3789) |
| `origin/ned/GRO-3310` | 39 | Mon Jul 6 22:20:33 2026 +0000 | 6 | [Ned] GRO-3310: format launch verifier |
| `origin/ned/GRO-3311` | 39 | Mon Jul 6 22:06:10 2026 +0000 | 6 | [Ned] Fix workspace optimizer idempotency (#GRO-3311) |
| `origin/ned/GRO-3343` | 39 | Mon Jul 6 21:53:32 2026 +0000 | 6 | [Ned] Clean up runs API lint and formatting (GRO-3343) |
| `origin/ned/GRO-3344` | 39 | Mon Jul 6 21:39:23 2026 +0000 | 6 | [Ned] Fix mode-change notification lint (GRO-3344) |
| `origin/ned/GRO-3472` | 39 | Mon Jul 6 20:35:23 2026 +0000 | 6 | [Ned] Format health view module (#GRO-3472) |
| `origin/ned/GRO-3473` | 39 | Mon Jul 6 20:23:29 2026 +0000 | 6 | [Ned] Clean dispatcher reliability verification (#GRO-3473) |
| `origin/ned/GRO-3475` | 39 | Mon Jul 6 20:00:23 2026 +0000 | 6 | [Ned] Format execution proof scorer (#GRO-3475) |
| `origin/ned/GRO-3477` | 39 | Mon Jul 6 19:35:59 2026 +0000 | 6 | [Ned] Format Phase 6 guardrail module (#GRO-3477) |
| `origin/ned/GRO-3485` | 39 | Mon Jul 6 10:06:14 2026 +0000 | 7 | [Ned] Format dead-letter implementation (#GRO-3485) |
| `origin/ned/GRO-3552` | 39 | Tue Jul 7 00:06:49 2026 +0000 | 6 | [Ned] Format board hygiene worker (#GRO-3552) |
| `origin/backup/pr-132-ned-GRO-3518-20260707T104430Z` | 38 | Mon Jul 6 07:07:05 2026 +0000 | 7 | [Ned] Disposition merge candidates (#GRO-3518) |
| `origin/ned/GRO-3455` | 38 | Sun Jul 5 20:12:16 2026 +0000 | 7 | [Ned] PVE Proxmox VM health monitoring audit (#GRO-3455) |
| `origin/ned/GRO-3457` | 38 | Sun Jul 5 20:40:01 2026 +0000 | 7 | [Ned] Add state database vacuum audit (#GRO-3457) |
| `origin/ned/GRO-3459` | 38 | Sun Jul 5 21:05:15 2026 +0000 | 7 | [Ned] Add GRO-3459 circuit breaker audit (#GRO-3459) |
| `origin/ned/GRO-3460` | 38 | Sun Jul 5 21:21:30 2026 +0000 | 7 | [Ned] Audit watchdog timers and fleet uptime (#GRO-3460) |
| `origin/ned/GRO-3461` | 38 | Sun Jul 5 21:36:49 2026 +0000 | 7 | [Ned] Audit OKF drift compliance scans (#GRO-3461) |
| `origin/ned/GRO-3462` | 38 | Sun Jul 5 21:50:08 2026 +0000 | 7 | [Ned] Add webhook security audit (#GRO-3462) |
| `origin/ned/GRO-3471` | 38 | Mon Jul 6 20:45:05 2026 +0000 | 6 | [Ned] Define ingestion dispatch reliability program (#GRO-3471) |
| `origin/ned/GRO-3482` | 38 | Mon Jul 6 14:03:03 2026 +0000 | 7 | [Ned] Trace consumer restart-loop root cause (#GRO-3482) |
| `origin/ned/GRO-3484` | 38 | Mon Jul 6 12:19:42 2026 +0000 | 7 | [Ned] Make queue processing idempotent (#GRO-3484) |
| `origin/ned/GRO-3487` | 38 | Mon Jul 6 10:19:43 2026 +0000 | 7 | [Ned] Enforce dispatch-ready gate (#GRO-3487) |
| `origin/ned/GRO-3490` | 38 | Mon Jul 6 10:58:26 2026 +0000 | 7 | [Ned] Persist launch records and sandbox handles (#GRO-3490) |
| `origin/ned/GRO-3492` | 38 | Mon Jul 6 11:26:00 2026 +0000 | 7 | [Ned] Add Linear completion sync helper (#GRO-3492) |
| `origin/ned/GRO-3495` | 38 | Mon Jul 6 11:48:50 2026 +0000 | 7 | [Ned] Quarantine duplicate Linear review noise (#GRO-3495) |
| `origin/ned/GRO-3497` | 38 | Mon Jul 6 12:00:44 2026 +0000 | 7 | [Ned] Add label debt queue drift report (#GRO-3497) |
| `origin/ned/GRO-3500` | 38 | Mon Jul 6 13:00:08 2026 +0000 | 7 | [Ned] Add zero-execution queue alerts (#GRO-3500) |
| `origin/ned/GRO-3501` | 38 | Mon Jul 6 13:13:31 2026 +0000 | 7 | [Ned] Define dispatch rollout rollback gates (#GRO-3501) |
| `origin/ned/GRO-3504` | 38 | Mon Jul 6 13:24:58 2026 +0000 | 7 | [Ned] Document Linear API key rotation blocker (#GRO-3504) |
| `origin/ned/GRO-3512` | 38 | Mon Jul 6 06:01:34 2026 +0000 | 7 | [Ned] Reconcile watchdog live-health truth (#GRO-3512) |
| `origin/ned/GRO-3514` | 38 | Mon Jul 6 06:16:06 2026 +0000 | 7 | [Ned] Triage merge backlog winners and stale entries (#GRO-3514) |
| `origin/ned/GRO-3516` | 38 | Mon Jul 6 06:36:32 2026 +0000 | 7 | [Ned] Prune terminal merge-pending Linear issues (#GRO-3516) |
| `origin/ned/GRO-3517` | 38 | Mon Jul 6 06:51:46 2026 +0000 | 7 | [Ned] Record merge contention winner decisions (#GRO-3517) |
| `origin/ned/GRO-3531` | 38 | Mon Jul 6 16:51:41 2026 +0000 | 6 | [Ned] Add live watchdog boundary verification (#GRO-3531) |
| `origin/ned/pwp-ai-theme-master-plan` | 29 | Sat Jul 11 16:01:50 2026 -0600 | 1 | [Ned] Add PWP deployment provenance manifest (#GRO-3713) |
| `origin/ned/GRO-3715` | 9 | Fri Jul 10 21:40:14 2026 +0000 | 2 | [Ned] Add PWP rollback adapter contract (#GRO-3715) |
| `origin/ned/GRO-3716` | 7 | Sat Jul 11 14:42:35 2026 -0600 | 1 | Merge pull request #209 from mbgulden/ned/GRO-3714 |
| `origin/ned/GRO-3714` | 6 | Fri Jul 10 00:12:50 2026 +0000 | 3 | [Ned] Format PWP run-state metadata changes (#GRO-3714) |
| `origin/ned/GRO-3672` | 4 | Thu Jul 9 16:09:52 2026 +0000 | 3 | [Ned] WIP GRO-3672: lint pwp command shim |
| `origin/ned/GRO-3677` | 4 | Sat Jul 11 14:43:00 2026 -0600 | 1 | Merge pull request #200 from mbgulden/ned/GRO-3679 |
| `origin/ned/GRO-3690` | 4 | Thu Jul 9 17:16:38 2026 +0000 | 3 | [Ned] Fix nested compliance claim filtering (#GRO-3690) |
| `origin/ned/GRO-3736` | 4 | Fri Jul 10 00:00:59 2026 +0000 | 3 | [Ned] Move PWP task template tests into plugin lane (#GRO-3736) |
| `origin/agent/hd-synthesis-scaffold-4829638201061480543` | 3 | Sat Jul 11 19:57:26 2026 +0000 | 1 | [AGENT] Finalize and acknowledge PR closure (#HD-1) |
| `origin/execution/curator-streams-GRO-2866-4734926026656210369` | 3 | Sat Jul 11 19:43:48 2026 +0000 | 1 | [Jules] Final acknowledgement of PR closure (GRO-2866) |
| `origin/feature/pe-native-seo-crons` | 3 | Sun Jul 12 16:59:54 2026 +0000 | 0 | Stabilize native cron tests in CI import context |
| `origin/feature/proof-loop-clean-publish` | 3 | Wed Jul 8 05:56:23 2026 +0000 | 5 | [Fred] Add packaging runtime dependency (#GRO-3589) |
| `origin/fred/supervisor-fixes-mtime-result-md-15908310939482243785` | 3 | Sat Jul 11 19:43:42 2026 +0000 | 1 | [Fred] Finalize branch; Closed as superseded by golden-path cleanup |
| `origin/ned/GRO-3570` | 3 | Tue Jul 7 11:32:10 2026 +0000 | 6 | [Ned] Align Jules host marker labels (#GRO-3570) |
| `origin/ned/GRO-3679` | 3 | Thu Jul 9 16:50:11 2026 +0000 | 3 | [Ned] Record PWP token provenance hashes (#GRO-3679) |
| `origin/ned/GRO-3738` | 3 | Thu Jul 9 23:48:50 2026 +0000 | 3 | [Ned] Clean PWP task generation lint (#GRO-3738) |
| `origin/ned/gro-1823-gvisor-runtime-13267768504582971242` | 3 | Sat Jul 11 20:04:33 2026 +0000 | 1 | [Ned] GRO-1823: Final acknowledgment and cleanup |
| `origin/feature/gro-1618-api-gateway-17233514225241463851` | 2 | Sat Jul 11 09:24:42 2026 +0000 | 2 | [Jules] Final acknowledgment of PR closure. |
| `origin/fix/dispatcher-activation-signal-agents-3112114326627074541` | 2 | Sat Jul 11 09:24:32 2026 +0000 | 2 | chore: finalize branch and acknowledge supersede status |
| `origin/fred/supervisor-fixes-GRO-2196-5117141796951370469` | 2 | Sat Jul 11 09:25:00 2026 +0000 | 2 | [Fred] Acknowledge PR closure and sync with main (#GRO-2196) |
| `origin/fred/supervisor-fixes-GRO-2196-8181094042036142091` | 2 | Sat Jul 11 09:26:01 2026 +0000 | 2 | [Fred] Final acknowledgment of PR closure |
| `origin/fred/supervisor-fixes-heartbeat-result-md-15212598510849814849` | 2 | Sat Jul 11 09:25:01 2026 +0000 | 2 | [Fred] Update supervisor fixes based on review feedback |
| `origin/fred/validate-supervisor-fixes-15742618630542730635` | 2 | Sat Jul 11 09:24:29 2026 +0000 | 2 | [Fred] Acknowledge PR closure as superseded by main (#GRO-2196) |
| `origin/fred/validate-supervisor-fixes-16339148130370398780` | 2 | Sat Jul 11 09:26:39 2026 +0000 | 2 | [Fred] Add regression test for RESULT.md default validation (#2196) |
| `origin/ned/GRO-3530` | 2 | Mon Jul 6 16:40:46 2026 +0000 | 6 | [Ned] Fix dashboard recovery formatting (#GRO-3530) |
| `origin/ned/GRO-3739` | 2 | Thu Jul 9 22:33:21 2026 +0000 | 3 | [Ned] Add PWP verifier artifact requirements (#GRO-3739) |
| `origin/ned/GRO-3740` | 2 | Fri Jul 10 21:16:44 2026 +0000 | 2 | [Ned] Add PWP Phase 9.5 fixture dry-run (#GRO-3740) |
| `origin/ned/gro-2205-gvisor-runtime-hardened-12213006512082573070` | 2 | Sat Jul 11 09:46:56 2026 +0000 | 2 | [Ned] GRO-2205: Robustify lifecycle manager DB loading (#GRO-2205) |
| `origin/feature/comprehensive-plugin-architecture` | 1 | Sun Jul 12 21:30:22 2026 +0000 | 0 | Add comprehensive PE plugin architecture path |
| `origin/feature/managed-seo-golden-path-doc-marker` | 1 | Sun Jul 12 19:32:37 2026 +0000 | 0 | Document managed SEO GTM golden path |
| `origin/feature/managed-seo-sites-ga4` | 1 | Sun Jul 12 17:59:30 2026 +0000 | 0 | Add managed SEO sites and GA4 insights crons |
| `origin/feature/managed-sites-gtm-datalayer-audit` | 1 | Sun Jul 12 19:29:51 2026 +0000 | 0 | Add GTM dataLayer setup audit for managed SEO sites |
| `origin/feature/pe-gsc-export-contract-marker` | 1 | Sun Jul 12 17:30:01 2026 +0000 | 0 | Document GSC export cron contract |
| `origin/feature/pe-native-seo-cron-extensions` | 1 | Sun Jul 12 17:26:59 2026 +0000 | 0 | Add extended PE-native SEO cron jobs |
| `origin/feature/plugin-artifact-provenance-registry` | 1 | Mon Jul 13 05:02:55 2026 +0000 | 0 | Add universal plugin artifact provenance registry |
| `origin/feature/plugin-jobs-audit-registry` | 1 | Mon Jul 13 03:06:55 2026 +0000 | 0 | Add durable plugin job audit registry |
| `origin/feature/plugin-production-governance-readiness` | 1 | Sun Jul 12 22:09:06 2026 +0000 | 0 | Add plugin governance readiness surfaces |
| `origin/feature/pwp-additive-pe-plugin-integration` | 1 | Sun Jul 12 20:17:29 2026 +0000 | 0 | Make PWP a connected PE additive plugin |
| `origin/feature/pwp-ubersuggest-oauth-refresh` | 1 | Sun Jul 12 03:51:27 2026 +0000 | 1 | Add PWP Ubersuggest credential refresh provider |
| `origin/ned/worktree-janitor-core` | 1 | Sun Jul 12 04:02:31 2026 +0000 | 1 | [Ned] Add core worktree janitor API and crons (#GRO-3811) |
| `origin/ned/worktree-janitor-safety-gates` | 1 | Sun Jul 12 04:20:05 2026 +0000 | 1 | [Ned] Harden worktree janitor safety gates (#GRO-3811) |
| `origin/ned/worktree-value-proof` | 1 | Sun Jul 12 04:41:52 2026 +0000 | 1 | [Ned] Add worktree usefulness proof layer (#GRO-3811) |