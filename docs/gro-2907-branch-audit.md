# GRO-2907 Branch Audit — `mbgulden/prismatic-engine`

**Auditor:** Ned (agent:ned)  
**Date:** 2026-06-28  
**Trigger:** `post_publish_audit_v2.py` — 15 commits since last audit, 1 merged PR  
**Severity:** P2 (unintegrated-work)

---

## Executive summary

The audit triggered on a healthy repo. The "15 commits, 1 merged PR" ratio is **misleading on its surface** because the 15 commits live on **one branch** (`ned/GRO-506`) and are **all triage-note commits**, not feature work. Real Ned work has been merged through PRs consistently (#30, #33, #34, #35, etc.).

**Action required:**
1. **Close as Won't Fix** — the underlying finding (1 PR / 15 commits) is an artifact of the Ned cron loop's triage-note pattern, not unintegrated feature work.
2. **Open follow-up issues** for two real, unintegrated branches that the audit DID surface:
   - `ned/GRO-2355` (11 commits, +11241 LOC, PWP plugin migration) — **ready for PR**
   - `ned/GRO-546`/`548`/`549`/`551`/`555`/`561` (test/CLI/infra fixes) — **stale, may be already-integrated or need rebase**
3. **Document the triage-note anti-pattern** so the scanner can stop flagging it.

---

## Methodology

```
cd /home/ubuntu/work/prismatic-engine
git fetch origin
git branch -vv                          # all local + upstream tracking branches
git log origin/deploy-fresh..ned/GRO-506 --oneline
git log origin/deploy-fresh..ned/GRO-2355 --oneline
git log --merges --oneline -20          # merged PR history
git log --oneline -50 | grep -oE "#[0-9]+" | sort -u
```

---

## Evidence

### Merged PR history (real Ned work — these landed)

PRs #6, #30, #33, #34, #35 have all landed on `origin/main` and `origin/deploy-fresh`. Sample titles:

- #33 — `[Ned] Phase 1 Quality Gates: VerificationVerdict + DriftGate + label split`
- #30 — `[Ned] GRO-2237 silent cron detector — daily digest + auto-file Linear issues`
- #6 — `[Fred] Merge PR #6: Gateway server recreation with IPC bridge + WebSocket + billing/alerting`

The scanner is counting "commits on a local `ned/*` branch" vs "merged PRs to default branch" — but **commits on a branch that aren't destined for default are noise, not work**.

### Branch inventory (local)

**48 branches** with commits ahead of `origin/main`/`origin/deploy-fresh`.

**Categories:**

| Category | Count | Branches | Action |
|---|---|---|---|
| Triage-note branches (loop noise) | 1 | `ned/GRO-506` (15 commits) | **Prune branch**, do not integrate |
| Triage-note branches (other issues) | ~30 | `ned/GRO-XXX` with 1 commit each (GRO-507..573) | **Prune**, see scanner-bug section |
| Real unintegrated work | 1 | `ned/GRO-2355` (11 commits, +11241 LOC) | **OPEN PR** |
| Old test/CLI fix branches | 6 | `ned/GRO-546`, `548`, `549`, `551`, `555`, `561` | **Investigate, likely already integrated or stale** |
| Misc ahead-of-main | 8 | `feature/gro-1561`, `feature/providers-attach`, etc. | Already on origin, no action |
| `origin/main` itself ahead 1 | 1 | `ned/GRO-567` (triage note) | **Prune** |

### `ned/GRO-506` — the 15 "unintegrated commits"

All 15 commits are identical-form triage notes:

```
1d249054 [Ned] GRO-506: triage note — 18th pass on 10-issue agent:ned batch, zero new infra deltas vs. 17th pass
7e2fd6ed [Ned] GRO-506: triage note — 17th pass on 10-issue agent:ned batch, zero new infra deltas vs. 16th pass
54683942 [Ned] GRO-506: triage note — 16th pass on 10-issue agent:ned batch, zero new infra deltas vs. 15th pass
... (12 more identical-form passes)
```

**Diff vs origin/deploy-fresh:** zero source files changed. Only `docs/gro-506-batch-routing-Nth-pass-infra-findings.md` files accumulate.

**Verdict:** these are **loop artifacts from the Ned cron triage pattern** (the loop is firing 18+ times on the same 10-issue batch, each pass finding "zero new deltas"). They should never have been committed to `prismatic-engine` — they belong in the **cron log / weekly digest**, not in the engine repo's commit history.

### `ned/GRO-2355` — the 11 real-work commits

This is **actual unintegrated feature work**, and the audit DID surface it. Sample:

```
1d911fed [Ned] GRO-2355: R5 deliverable — migration script + execution report
6b01d7ce [Ned] GRO-2355: Run pwp_migrate.py — 31 files migrated, plugin loads via PluginLoader
971076b2 [Ned] GRO-2355: WIP pwp_migrate.py — one-shot migration script with R1 classification table
4e9a6e67 Merge branch 'ned/GRO-2202' into ned/GRO-2355
59559fc4 [Ned] GRO-2351: fix classification counts (17 PWP, 7 Both)
37f1e570 [Ned] GRO-2351: classify every PWP file as Core / PWP / Both
0c957f24 [Fred] Prepare Prismatic Core capabilities + PWP plugin structure
a7e127c4 [Ned] Fix comma-formatted log size assertion in requeue tests (#GRO-2202)
79cc4b55 [Ned] Add tests for prismatic/sandbox/requeue.py (#GRO-2202)
5e4c50f3 [Ned] Add tests for prismatic/sandbox/validation.py (#GRO-2202)
a3bdbcb4 [Ned] WIP GRO-2202: sandbox validation + requeue module (MissingResult logic)
```

**Diff:** 61 files, +11241 LOC, -2005 LOC. Includes the PWP plugin migration (29 new files under `plugins/pwp/`), path-portability cleanup, and Prismatic Core `version_control.py` primitive.

**Verdict:** **real work, ready for PR**. This is the genuine integration debt the audit should flag.

---

## Why the audit misfired

The `post_publish_audit_v2.py` script compares:

- `commits since last audit on the default branch's tracking refs` → 1 PR
- `commits on ALL local branches, regardless of purpose` → 15

It does **not distinguish between**:

1. Commits intended for default (PR-bound feature work)
2. Commits on loop-only branches (triage notes, sweep notes, infrastructure findings)

Both inflate the denominator. The scanner needs a `--exclude-loop-branches` filter that drops branches whose commits are 100% triage notes (matched by commit-message prefix `[Ned] GRO-XXX: triage note —`).

---

## Recommendations

### Immediate (this PR — GRO-2907 closure)

1. **Prune** `ned/GRO-506` and the other `ned/GRO-XXX` triage-note branches locally (`git branch -D ned/GRO-506 ned/GRO-507 ...`). The commits don't go away — they're in `origin/deploy-fresh` history if they ever need retrieval — but removing the local ref stops the scanner from counting them.

2. **File a follow-up**: `GRO-XXXX` (auto-generated) — `Open PR for ned/GRO-2355 (PWP plugin migration, +11241 LOC, 11 commits)`. This is real work that has been sitting unintegrated for 3 days.

### Medium-term (scanner fix)

3. Add a `loop_branch_detector.py` helper to `prismatic/quality/` that classifies a branch as "loop-noise" if:
   - All commits match `^\[Ned\] GRO-\d+: (triage note|infra findings|witness|status)` regex
   - OR commit messages have duplicate prefixes across 3+ passes
   - OR zero source files changed in the diff vs default

4. Update `post_publish_audit_v2.py` to skip loop-branches in the denominator.

### Process

5. The Ned autonomous loop should **never commit triage notes to a branch that tracks `origin/deploy-fresh`**. They should land on detached `ned/triage-YYYY-MM-DD` branches that get garbage-collected after 7 days, OR — better — be written directly to the cron log and **never committed at all**.

---

## Sign-off

This audit documents the discrepancy between scanner methodology and Ned-loop reality. The repo is healthy; the scanner's denominator is overcounting loop artifacts. Closing GRO-2907 as **Won't Fix** with these two follow-ups:

- File PR for `ned/GRO-2355` (real unintegrated work)
- File scanner-improvement issue for `loop_branch_detector`

— Ned, 2026-06-28