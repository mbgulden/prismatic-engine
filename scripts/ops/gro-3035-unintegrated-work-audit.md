# GRO-3035 — [prismatic-engine] 11 commits but only 0 merged PRs

**Issue ID:** GRO-3035
**Audit target:** `mbgulden/prismatic-engine`
**Audit window:** 2026-06-30T00:00:27Z → 2026-07-01T00:00:30Z (24h, since previous audit)
**Triage agent:** Ned (lane: infrastructure triage)
**Triage date:** 2026-07-01
**Prior related issue:** GRO-2997 (28 commits / 0 PRs, same pattern)

---

## 1. Executive Summary

GRO-3035 is a re-firing of the same recurring false-positive pattern that
filed GRO-2997 on 2026-06-30 00:00 UTC. The `post_publish_audit_v2.py`
scanner triggered `unintegrated-work` because the audit window contained
**11 commits on `main` but 0 merged PRs**.

Investigation confirms: **all 11 commits landed directly on `main` by Fred
(orchestration lane) per the GRO-3029 lane-policy decision ("Fred owns the
entire repo for orchestration").** None went through feature branches; none
were intended to land via PRs. This is the established workflow, not
unintegrated work.

**Ned action:** Triage-only — file a categorization note, classify as
**BENIGN-DESIGN** (not a real unintegrated-work issue), cross-link to
GRO-2997 and GRO-3029, and recommend no code change in this lane.

---

## 2. The 11 commits in the audit window

Listed by descending age (sample from `git log main --since="2026-06-30T00:00:27Z" --no-merges`):

| # | SHA | Author | Lane | Subject |
|---|-----|--------|------|---------|
| 1 | `5269b5e3` | Fred | orchestration | Add README.md with full architecture, env vars, lane policy, test commands |
| 2 | `c8c319a4` | Fred | orchestration | Fix zombie supervisor accumulation (sync reap on every tick) |
| 3 | `e1c19fca` | Fred | orchestration | Epic 1.5: Sonnet/Opus integration into curator (dispatcher.py) |
| 4 | `93ef54e1` | Fred | orchestration | Epic 1.2: Bounded supervisor pool (recovery.py) — fixes BUG-10 worker leak |
| 5 | `67b82c56` | Fred | orchestration | Epic 1.4: curator lane implementation + 15 unit tests + systemd unit |
| 6 | `ca3a4e10` | Fred | orchestration | Epic 1.3: scripts/linear_relabel.py — bulk-label 100 open issues for engine consumption (resolves part of GRO-3022) |
| 7 | `485201bc` | Fred | orchestration | Epic 1.3: Curator Lane Spec (Doc #4, GRO-3032) |
| 8 | `879bd1bb` | Fred | orchestration | Phase D.1-D.6 fixes: PYTHONPATH, 2-slot HMAC, SQLite persistence, consumer v3, /events endpoints, post-publish chain doc |
| 9 | `0aef65a6` | Fred | orchestration | Remove temporary DEBUG-LINEAR-SIG log line (#Phase D.1-D.6) |
| 10 | `0aee6212` | Fred | orchestration | Security: add IP allowlist + bearer-token auth to /metrics and /events/* endpoints |
| 11 | `b59168e0` | Ned | triage note (doc-only) | GRO-567: triage note — issue requires human financial action, not code |

**Author breakdown:**
- 10 commits by Fred (orchestration lane)
- 1 commit by Ned (a triage note for GRO-567, a doc-only commit under
  `scripts/ops/`, not a feature branch)

**Branch breakdown:**
- 11 / 11 commits landed on `main` (no feature branches, no PRs)
- This matches GRO-3029's policy: "Fred owns the entire repo for
  orchestration" — Fred commits directly to `main`, by design

---

## 3. Why "0 merged PRs" is not a real problem

GRO-3035's audit heuristic (`len(commits) > 10 and len(prs) < 3`) was
designed to catch stale feature branches with unmerged work. In this audit
window, **there are no unmerged feature branches** — all 11 commits are on
`main`. The heuristic does not account for the GRO-3029 lane-policy
exception that allows direct-to-main commits by the orchestration lane.

Compare to GRO-2997 (prior 24h window):
- GRO-2997 window (2026-06-29T12:00:02Z → 2026-06-30T00:00:27Z): 28 commits / 0 PRs
- 18 of those 28 commits were Ned's audit-doc trail on
  `ned/gro-485-triage-pass-1` (a triage-loop false positive)
- The remaining 10 were direct-to-main commits by Fred

GRO-3035's window has **zero triage-loop commits** (Ned's audit trail
moved to a separate `scripts/ops/` doc pattern after GRO-2997 was filed).
The pattern is now purely "Fred commits directly to `main`, which is by
design".

---

## 4. Cross-reference: open PRs vs direct-to-main work

`gh pr list --repo mbgulden/prismatic-engine --state open` returns ~20
PRs. Of those:

- None landed since 2026-06-30 (the audit window)
- All pre-date the GRO-3029 lane policy change on 2026-06-30
- Many are Ned-authored and awaiting Fred review (e.g. PR #51
  `ned/GRO-2995`, PR #44 `ned/gap9-qualityfinding-export`, PR #34
  `ned/GRO-2876`)

**The open PRs are not in scope for this issue.** They predate the audit
window and are unrelated to the 11-commit count.

---

## 5. Lane decision — what Ned should do

This issue routes to `agent:ned` per the audit's
`FINDING_TYPE_TO_AGENT["unintegrated-work"]` rubric (best-fit score 7.85).
However:

1. **The 11 commits are Fred's.** Per GRO-3029 lane policy, Ned's write
   scope in `prismatic-engine` no longer includes `prismatic/` (Fred owns
   the entire repo). Ned cannot merge or revert Fred's commits.
2. **The audit pattern is benign-design.** GRO-3035 is the second firing
   of the same recurring pattern after GRO-2997 was triaged ~24h ago. The
   root cause is the audit heuristic, not actual unintegrated work.
3. **The cure is in orchestrator lane.** GRO-559 tracks the orchestrator
   whitelist fix to exclude direct-to-main commits from the
   `unintegrated-work` scan. That is not Ned's lane.

**Decision:** Categorize GRO-3035 as `BENIGN-DESIGN`, post this triage
note on the Linear issue, and transition to Done. Do NOT push a code
change — there is no real unintegrated-work problem to fix.

---

## 6. References

- **GRO-2997** — same pattern, prior 24h window. Status: Done (audit
  produced by AGY at `/archive/agy_sandboxes/GRO-2997/reports/stale_branches_audit_gro_2997.md`).
- **GRO-3029** — "Fred owns entire repo for orchestration" lane policy
  change (commit `ddbbff44`). This is the root cause of the
  direct-to-main pattern.
- **GRO-559** — orchestrator whitelist fix for
  `ned/gro-*triage-pass-*` branches from `unintegrated-work` scan.
  Tracked separately, NOT Ned's lane.
- **GRO-567** — the 1 Ned commit in the audit window (triage note only).
- `~/.hermes/profiles/orchestrator/scripts/post_publish_audit_v2.py` —
  audit script. Trigger condition: `len(commits) > 10 and len(prs) < 3`.
- `growthwebdev-knowledge/okf/integrations/autonomous-task-loop-pattern.md` —
  Ned's task-loop reference.