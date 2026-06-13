# GitHub API Commit — Workaround for Large Repo Push Failures

## When to Use
- `git push` fails with `RPC failed; HTTP 500` or `send-pack: unexpected disconnect`
- The repo is large (4GB+ .git) due to binary assets (sprites, audio, video)
- You only changed a few files (e.g., `index.html`) but git tries to negotiate the entire pack
- `--no-thin`, increased `http.postBuffer`, and SSH fallback all fail
- The CF Pages project auto-deploys from the GitHub repo branch

## Pattern: Commit a Single File via GitHub REST API

```python
import base64, json, urllib.request

# Extract token from git remote URL
GH_TOKEN = subprocess output of: git remote get-url origin → parse: https://USER:TOKEN@github.com/...

# 1. Get current file SHA (required for update)
url = f"https://api.github.com/repos/{REPO}/contents/index.html?ref=staging"
req = urllib.request.Request(url, headers={
    'Authorization': f'Bearer {GH_TOKEN}',
    'Accept': 'application/vnd.github.v3+json'
})
sha = json.loads(urllib.request.urlopen(req).read())['sha']

# 2. Read new file content
with open('index.html', 'rb') as f:
    content = f.read()

# 3. Commit the update
payload = {
    'message': 'commit message',
    'content': base64.b64encode(content).decode(),
    'sha': sha,           # MUST include — prevents overwriting concurrent changes
    'branch': 'staging'   # CF Pages auto-deploys from this branch
}
req2 = urllib.request.Request(
    f"https://api.github.com/repos/{REPO}/contents/index.html",
    data=json.dumps(payload).encode(),
    headers={
        'Authorization': f'Bearer {GH_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json'
    },
    method='PUT'
)
result = json.loads(urllib.request.urlopen(req2).read())
# result['commit']['sha'] — the new commit SHA
# result['commit']['html_url'] — link to the commit on GitHub
```

## How It Works
1. Bypasses git's pack negotiation entirely — sends only the file content via HTTPS
2. GitHub creates the commit server-side, identical to a normal push
3. CF Pages webhook fires on the new commit → auto-deploys
4. After commit, sync local git: `git fetch origin staging && git reset --hard origin/staging`

## Prerequisites
- **GitHub token** with repo access — check `git remote get-url origin` for `https://USER:TOKEN@github.com/...` pattern
- If no token in URL, check `~/.git-credentials` or `GITHUB_TOKEN` env var
- Token needs `contents: write` scope (classic PAT) or repo access (fine-grained)

## Pitfalls
- **Single file only** — this updates ONE file per API call. For multi-file changes, call the API once per file.
- **SHA is mandatory** — the `sha` field prevents race conditions. Always fetch the current SHA first.
- **Branch must exist** — the `?ref=staging` parameter fetches from that branch. The commit goes to the same branch.
- **Base64 encoding** — content must be base64-encoded. Python's `base64.b64encode()` returns bytes — call `.decode()`.
- **Large files** — the API has a 100MB file size limit. For game index.html (~400KB) this is fine.
- **GitHub Actions** — API commits DO trigger workflows. If CF Pages auto-deploys from the branch, it will deploy.

## When This Won't Work
- The repo needs to push new binary assets (sprites, audio) — API commit only updates the tracked file
- You need to create/delete files — use the `DELETE` method or POST for creation (different payload format)
- The repo uses Git LFS — API commits bypass LFS, binary files would be corrupted

## Related Patterns
- `cf-pages-direct-upload-api.md` — alternative: upload directly to CF Pages via REST API (bypasses GitHub entirely)
- `force-push-rollback-and-deploy-trigger.md` — use when the git push works but CF Pages caches stale content
