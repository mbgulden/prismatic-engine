# Git Orphan Branch — Clean History Reset

An alternative to `git filter-branch` when the entire history needs to be replaced with a single clean commit. Faster, simpler, and produces exactly one commit with no trace of old data.

## When to Use

- PII or secrets are scattered across many historical commits
- The repo history has no valuable content worth preserving
- You want a single clean commit as the new root
- `git filter-branch` would be too complex or slow for hundreds of commits

## How to Execute

```bash
cd /path/to/repo

# Step 1: Ensure the working tree is clean (all changes committed)
git status  # Should show nothing

# Step 2: Create an orphan branch (no history)
git checkout --orphan clean-main

# Step 3: Stage everything
git add -A

# Step 4: Single clean commit
git commit -m "Project Launch — clean history"

# Step 5: Force push the orphan branch as main
git push origin clean-main:main --force

# Step 6: Delete old remote branches (optional)
git push origin --delete feature/old-branch-1 feature/old-branch-2

# Step 7: Switch back to main and reset local to match remote
git fetch origin
git reset --hard origin/main
```

## Verification

After force push, verify:
```bash
git log --oneline        # Should show ONLY the single clean commit
git log -p | grep -i "sensitive_pattern"  # Should return nothing
```

## Pitfalls

- This DESTROYS all commit history. Only use if the user explicitly wants a clean slate.
- All existing clones and forks will break — they must be re-cloned.
- Old branches on the remote must be deleted manually.
- GitHub Actions/CI that reference specific commit SHAs will break.
- If the force push fails (protected branch), you may need to temporarily unprotect the branch in GitHub Settings → Branches.

## vs filter-branch

| Approach | When to use |
|---|---|
| **orphan branch** | Whole history is contaminated, nothing worth keeping |
| **filter-branch** | Only a few files/commits contain secrets, rest of history is valuable |
