# Force-Push Rollback + Deploy Trigger for Git-Connected CF Pages

When a bad commit lands on `main` and triggers an unwanted CF Pages deploy.

## Procedure

### 1. Identify the clean commit
```bash
cd /path/to/repo
git log --oneline -20
# Find the commit hash BEFORE the bad changes started
```

### 2. Verify the target is actually clean
```bash
# Check what files changed between target and current
git diff <clean_commit>..HEAD -- site/_templates/ site/src/ --stat
# Inspect specific files to confirm the bad changes are absent at target
git show <clean_commit>:path/to/file.html | grep 'bad-pattern'
```

**Pitfall — JS-generated elements:** When rolling back nav/UI changes, check ALL template files (not just the obvious one). A clean `body_top.html` is insufficient if `body_bottom.html` or `head.html` still contains JS that dynamically creates the unwanted element at runtime. Query: `git log --oneline -- site/_templates/` to trace when each template was last changed, and `grep -r 'unwanted-pattern' site/_templates/` to check all template files.

**Pitfall — the last non-obviously-bad commit may still contain the problem:** The most recent non-nav commit may still have related changes. Always verify with `git diff` that the target has zero files in scope. In one session, rolling back to GRO-754 still had nav changes — had to go two commits further to get the truly clean nav.

### 3. Force-push the rollback
```bash
git reset --hard <clean_commit>
git push --force origin main
```

### 4. Verify the deploy triggered
CF Pages auto-deploys on push to `main`. Check the live site:
```bash
curl -sI "https://site.com/?nocache=$(date +%s)" | grep last-modified
```

### 5. If deploy didn't trigger — use a trigger commit
Force-pushes sometimes don't fire the CF Pages webhook. An empty commit forces it:
```bash
git commit --allow-empty -m "trigger: rebuild CF Pages deploy"
git push origin main
```
This creates a new commit SHA that CF Pages can't ignore. Wait 30-90 seconds and re-check.

### 6. Verify the live site
```bash
# Confirm bad patterns gone
curl -s "https://site.com/" | grep -c 'bad-pattern'   # should be 0
# Confirm expected patterns present
curl -s "https://site.com/" | grep -c 'expected-pattern'
```

**Pitfall — external CSS may still be cached:** HTML pages deploy fresh but external assets (style.css) served via `<link>` have independent CDN edge caches. After rollback, the page HTML may be correct but the CSS file may still be the cached old version. Check: `curl -sI "https://site.com/path/to/style.css" | grep cf-cache-status`. If `HIT`, the browser sees stale CSS. Fix: version-bust the CSS link (`style.css?v=2`) in all HTML files. See `references/cf-external-asset-cache-staleness.md`.

## Fallback: Cache Purge

If the site serves stale content after deploy confirmation:
- Without Cloudflare API credentials: the trigger commit + new deploy is the only cache-bust available
- With API access: use the Cloudflare purge-cache endpoint or dashboard
- `cf-cache-status: HIT` with a large `age` value = stale cache serving old content
