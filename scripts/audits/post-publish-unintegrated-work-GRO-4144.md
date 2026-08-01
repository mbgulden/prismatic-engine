# GRO-4144 Post-Publish Audit: prismatic-engine unintegrated work

Generated: 2026-07-23T00:10:23Z
Repo: `mbgulden/prismatic-engine`
Branch used for recovery: `ned/GRO-4144`

## Finding

The audit alert is real. The 20-commit window split cleanly into:

- 7 commits merged through PR #371 (`feature/dashboard-control-auth-1` -> `main`, merged 2026-07-22T19:31:38Z): https://github.com/mbgulden/prismatic-engine/pull/371
- 13 additional commits present only in the local detached checkout at `0bb35333`, ahead of `origin/main` `2114badc`
- 1 uncommitted repair delta in `prismatic/gateway/event_handlers/dispatch_consumer_v3.py` before this branch commit:

```text
.../gateway/event_handlers/dispatch_consumer_v3.py | 346 ++++++++++++++++++---
 1 file changed, 303 insertions(+), 43 deletions(-)
```

The sample audit commits (`0bb35333`, `8f22efe3`, `565d816c`, `3ad4d3fe`, `d8a44691`) were not on any remote branch when checked; they were reachable only from the detached local HEAD. I created `ned/GRO-4144` at that HEAD so the work is no longer orphaned.

## Detached commit chain rescued

```text
06d6bcc0 feat(gateway): add dispatch cursor generation safety, fail-closed gate, and repair primitives
c4ad05ae Repair dispatch cursor generation safety
4ef714ef Harden WAL cursor repair rollback proof
7f3215e0 Repair 3: backup descriptor open, mandatory fsync, and partial artifact cleanup
8779db2d Bind cursor generation through repair side effects
ed393b13 Harden cursor repair recovery boundary
a2e5a754 Remove cursor safety doc trailing blank line
f1b46685 Make cursor lock release observable with exact recovery and rollback
d8a44691 Repair 7: Safe reserialized rollback, zero-byte cursor preservation, and protected snapshot phase
3ad4d3fe Fix contender release regression harness
565d816c Fail closed on uncertain cursor lock ownership
8f22efe3 Prove cursor lock closure before rollback completion
0bb35333 Repair 8: Descriptor-bound no-follow cursor snapshot and strict regular private lock validation
```

Committed diff versus `origin/main` before the final rescue commit:

```text
docs/dispatch-cursor-generation-safety.md          |  136 ++
 .../gateway/event_handlers/dispatch_consumer_v3.py | 2410 ++++++++++++++++++--
 tests/test_dispatch_consumer_cursor_generation.py  | 2361 +++++++++++++++++++
 3 files changed, 4779 insertions(+), 128 deletions(-)
```

## Open PRs with unmerged work checked

Representative stale/open branches from `gh pr list --state open` and ahead-count checks:

| PR | Branch | Base | Notes |
|---:|---|---|---|
| #324 | `content/gro-3969-overnight-readiness-guard-design` | `main` | open content/design work; not Ned lane |
| #315 | `feature/fred-agent-completed-work-skill-packs` | `main` | open Fred/AGY repair queue; 14 commits ahead |
| #301 | `feature/fred-agy-overnight-readiness-guard` | `main` | open Fred work |
| #293 | `feature/fred-agy-autopilot-result-packets` | `main` | open Fred work |
| #282 | `feature/fred-gro-549-handoff-contracts` | `main` | open Fred work |
| #250 | `ned/GRO-3711-final` | `deploy-fresh` | stale Ned PR; review/close/retarget separately |
| #249 | `ned/GRO-3738` | `ned/pwp-ai-theme-master-plan` | stale Ned PR against topic branch |

## Recommended follow-up

1. Push `ned/GRO-4144` and open a PR to `main` for review of the rescued cursor-generation safety work.
2. Review stale open PRs, especially old `ned/*` PRs still targeting `deploy-fresh` or topic branches.
3. Delete or archive remote branches that have merged PRs but still appear as ahead of `main` due to squash/rebase history; they create false positives in post-publish scans.

## Verification commands

- `git rev-list --count origin/main..HEAD`
- `git log --oneline --reverse origin/main..HEAD`
- `gh pr view 371 --repo mbgulden/prismatic-engine --json number,title,state,mergedAt,headRefName,commits,url`
- `gh pr list --repo mbgulden/prismatic-engine --state open --limit 20 --json number,title,headRefName,baseRefName,updatedAt,url`
