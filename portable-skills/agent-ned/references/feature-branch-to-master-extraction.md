# Feature-Branch Work → Master Extraction Pattern

When a prior session completed work on a non-master feature branch and the Linear card
was never closed, Ned must merge the work to master before closing. Cherry-pick often
fails when the branch has diverged significantly. Use direct file extraction instead.

## When to Use

- Work exists on a feature branch (e.g., `audit/agy-GRO-NNN`)
- The commit is NOT on `origin/master` — verified with `git log origin/master --grep="ISSUE_ID"`
- Cherry-pick fails with conflicts (branch has accumulated unrelated commits)
- Always: `git checkout master` first — Ned works on master only

## Workflow

```bash
# 1. Identify the commit on the feature branch
git log --oneline --all --grep="ISSUE_ID"  # find commit SHA
# Or if you already know the branch:
git checkout <feature-branch>
git log --oneline -1  # confirm you see the commit

# 2. See what files were changed
git show --stat <commit_sha>

# 3. Extract every file to /tmp/
git show <commit_sha>:path/to/file > /tmp/issue_file
git show <commit_sha>:path/to/other_file > /tmp/issue_other

# 4. Switch to master (stash any pre-existing changes)
git checkout master
git stash  # if dirty working tree

# 5. Apply files
# New files: create parent dirs first
mkdir -p site/guides/new-dir
cp /tmp/issue_file site/guides/new-dir/index.html
# Existing files: overwrite
cp /tmp/issue_modified site/guides/existing/index.html

# 6. Stage explicitly (never git add -A)
git add path/to/file1 path/to/file2 ...

# 7. Verify only your files are staged
git diff --cached --stat

# 8. Commit with [Ned] prefix + issue ID
git commit -m "[Ned] ISSUE_ID: merge verified work to master"

# 9. Push
git push origin master
```

## Multi-Repo Push (AOT-specific)

Both `active-oahu-tours-mirror` and `active-oahu-static` share the same GitHub remote.
Pushing from one makes the other "already up-to-date" after `git pull --rebase`.
Push from one repo, then in the other: `git fetch; git pull --rebase origin master`.

## Verification Before Closing

1. `git log --oneline origin/master -1` — your commit should be HEAD
2. Stat the files: `ls <deliverable_paths>` — all should exist
3. If the issue's description lists specific files, verify each one exists

## Real Example: GRO-788 (Jun 2026)

- Feature branch: `audit/agy-GRO-788`, commit `e0a74a08`
- 8 files: 3 new guides, 2 updated guides, 2 helper scripts, 1 map image
- Cherry-pick conflicted on waimanalo-beach (branch had 12 commits ahead of master)
- Extracted all 8 files with `git show e0a74a08:<path>`, applied to master
- Committed `19bd6368`, pushed to origin
- Closed Linear card: comment + agent:fred → agent:done + Backlog → Done
