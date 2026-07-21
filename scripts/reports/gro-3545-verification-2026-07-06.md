# GRO-3545 — post-publish audit verification

> **Historical evidence — not current authority.** This verification remains useful lineage for its exact timestamp and revision only. Current precedence: `docs/index.md`; machine registry: `okf/index.yaml`.

Issue: [GRO-3545](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3545)  
Repo: `mbgulden/prismatic-engine`  
Verified by: Ned  
Timestamp: 2026-07-06T18:34:08Z

## Finding summary

The post-publish audit headline is directionally valid but under-specified: it saw local unintegrated commit activity and reported `25 commits, 0 merged PRs`.

Live verification found two distinct queues:

1. **GitHub PR queue is not empty.** `gh pr list --repo mbgulden/prismatic-engine --state open --limit 200` returned **87 open PRs**, including the current July 6 Ned backlog from PR #128 through #163.
2. **The audit sample commits are local merge-pipeline/autocheckpoint work, not merged PRs.** The sample commits named in the Linear issue (`d4653f02`, `55f758c2`, `d5fd563f`, `816b623b`, `f958da19`) are present in local branches such as `merge/pipeline-v2`, `backup/gro-3522-full-okf-blocked`, `backup/gro-3522-inlane-disconnected`, `ned/GRO-3508-dashboard-fix`, `ned/GRO-3509`, `ned/GRO-3516`, and `ned/gro-3511-dlq-fallback`.

## Evidence

### Issue and scanner context

Linear issue state at pickup: `Backlog` with `agent:ned` only. The only existing comment was the dispatcher route note:

> Dispatcher: task `GRO-3545` routed to Ned (Workspace: google-drive-gemini-context).

### GitHub PR state

Command:

```bash
gh pr list --repo mbgulden/prismatic-engine --state open --limit 200 \
  --json number,headRefName,baseRefName,title,url
```

Observed: `open_prs=87`.

Recent relevant open PRs:

- #163 `main <- ned/GRO-3538` — `[Ned] Link enterprise gaps to Linear issues (#GRO-3538)`
- #162 `ned/GRO-3533 <- ned/GRO-3534` — `[Ned] Add quota freshness failure state (#GRO-3534)`
- #161 `deploy-fresh <- ned/GRO-3533` — `[Ned] Normalize quota payloads (#GRO-3533)`
- #160 `deploy-fresh <- ned/GRO-3532` — `[Ned] Enterprise quota and telemetry integrity (#GRO-3532)`
- #159 `deploy-fresh <- ned/GRO-3531` — `[Ned] GRO-3531 live watchdog boundary verification`
- #158 `main <- ned/GRO-3530` — `[Ned] Surface watchdog, heartbeat, and DLQ state in dashboard (#GRO-3530)`
- #157 `main <- ned/GRO-3529` — `[Ned] Expose recovery controls with server proof (#GRO-3529)`
- #156 `main <- ned/GRO-3527` — `[Ned] Make dashboard panes actionable (#GRO-3527)`
- #155 `deploy-fresh <- ned/GRO-3526` — `[Ned] Fix dashboard operator feedback copy and error states (#GRO-3526)`
- #154 `deploy-fresh <- ned/GRO-3524` — `[Ned] Harden command deck controls (#GRO-3524)`

PR #163 details:

```json
{
  "number": 163,
  "state": "OPEN",
  "isDraft": false,
  "baseRefName": "main",
  "headRefName": "ned/GRO-3538",
  "url": "https://github.com/mbgulden/prismatic-engine/pull/163",
  "checks": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
  "files": ["scripts/reports/gro-3538-enterprise-gap-linear-map.md"]
}
```

### Local branch state

Command:

```bash
git for-each-ref refs/heads \
  --format='%(refname:short)|%(upstream:short)|%(upstream:track)|%(committerdate:iso8601)|%(objectname:short)|%(subject)' \
  --sort=-committerdate
```

Notable local branches with unpushed/no-upstream or ahead-only state:

- `merge/pipeline-v2` — local only, head `5722aa66`, WIP-autocheckpoint chain at 2026-07-06T14:16Z.
- `ned/GRO-3535` — local only, head `947d9427`, telemetry pane validation.
- `ned/GRO-3534` — local only in this checkout at command time, head `3c7b8b44`.
- `ned/GRO-3533` — tracks `origin/deploy-fresh`, `[ahead 3]` locally while PR #161 is open.
- `ned/GRO-3532` — tracks `origin/deploy-fresh`, `[ahead 4]` locally while PR #160 is open.
- `ned/GRO-3519` — tracks `origin/main`, `[ahead 366, behind 82]`, local scorecard baseline branch.
- `backup/gro-3522-full-okf-blocked` / `backup/gro-3522-inlane-disconnected` — local backup branches preserving lane-blocked work.

### Audit sample commits

The issue's sample commits resolve locally as:

```text
d4653f02 2026-07-05 18:07:41 +0000 [Antigravity] Import argparse inside main in curator lane.py
55f758c2 2026-07-05 18:07:09 +0000 [WIP-auto-checkpoint] 2026-07-05T18:07:09Z last commit is 31669s old (> 600s)
d5fd563f 2026-07-05 09:19:20 +0000 [WIP-auto-checkpoint] 2026-07-05T09:19:20Z last commit is 601s old (> 600s)
816b623b 2026-07-05 09:09:19 +0000 [WIP-auto-checkpoint] 2026-07-05T09:09:19Z last commit is 2148s old (> 600s)
f958da19 2026-07-05 08:33:31 +0000 [WIP-auto-checkpoint] 2026-07-05T08:33:31Z last commit is 603s old (> 600s)
```

Those commits are contained in local branches:

```text
backup/gro-3522-full-okf-blocked
backup/gro-3522-inlane-disconnected
merge/pipeline-v2
ned/GRO-3508-dashboard-fix
ned/GRO-3509
ned/GRO-3516
ned/GRO-3519
ned/gro-3511-dlq-fallback
```

Sample touched files:

- `prismatic/curator/lane.py`
- `prismatic/dispatcher.py`
- `prismatic/dispatcher_local_copy.py`
- `tests/test_challenger_pid_kill.py`
- `tests/test_pid_tracking.py`
- `plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.js`
- `plugins/hermes-plugin-prismatic-hub/src/index.js`
- `prismatic/gateway/server.py`

## Disposition

This should not be treated as a missing-credentials or GitHub-access problem. GitHub access works and PR state is visible.

Recommended downstream action is merge-backlog triage, not blind merge:

1. Keep existing open PRs as the review ledger; do not collapse them by force-merging the local `merge/pipeline-v2` autocheckpoint chain.
2. Treat `merge/pipeline-v2` and the backup branches as merge-pipeline WIP/forensics until a human or merge-lane agent maps them to explicit PRs/issues.
3. Fix `post_publish_audit_v2.py` separately so future `unintegrated-work` findings distinguish:
   - open PR count,
   - merged PR count,
   - local-only branches with no upstream,
   - ahead-of-upstream branches,
   - backup branches intentionally preserving lane-blocked artifacts.

## Verification commands run

```bash
git fetch origin --prune
gh pr list --repo mbgulden/prismatic-engine --state open --limit 200 --json number,headRefName,baseRefName,title,url
gh pr view 163 --repo mbgulden/prismatic-engine --json number,title,state,isDraft,baseRefName,headRefName,commits,files,statusCheckRollup,url
git for-each-ref refs/heads --format='%(refname:short)|%(upstream:short)|%(upstream:track)|%(committerdate:iso8601)|%(objectname:short)|%(subject)' --sort=-committerdate
git log --oneline --decorate --max-count=35 merge/pipeline-v2
for c in d4653f02 55f758c2 d5fd563f 816b623b f958da19; do git show --stat --oneline --name-only --no-renames "$c"; done
```
