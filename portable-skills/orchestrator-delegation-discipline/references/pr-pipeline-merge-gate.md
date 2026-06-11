# PR pipeline and merge gate

Use this when a task involves an open PR, a CI gate, or deciding whether to merge and move to the next bounded phase.

## Durable checks
- Treat GitHub as the source of truth for open PR state; do not infer from local branch names alone.
- Verify with `gh pr list` / `gh pr view` before saying there are no outstanding PRs.
- If a PR is open and mergeable, inspect its checks and review state before merging.

## Merge flow
1. Confirm the PR is the intended bounded change set.
2. Verify required checks are green.
3. Merge with the least surprising strategy for the repo's policy.
4. Delete the feature branch if the repository expects cleanup.
5. Sync local `main` with `origin/main` and verify the worktree is clean.
6. Convert the next step into a bounded, reviewable plan before starting new implementation.

## Next-step planning after merge
- Identify the next smallest phase with a clear file boundary.
- Write the next phase as a PR-sized unit.
- Keep the plan explicit: implement -> validate -> open PR -> merge gate.
- Avoid continuing to the next phase until the repository is synced and the previous PR is fully closed out.

## Pitfalls
- Assuming there are no open PRs because the local branch looks current.
- Merging without checking the PR's CI and mergeability.
- Forgetting to sync local main after a merge.
- Letting the next step remain vague instead of naming the next bounded phase.
