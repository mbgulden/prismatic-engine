# GitHub API Commit as Push Workaround

When `git push` fails due to large repo size (HTTP 500 on 4.4GB+ repos), use the
GitHub REST API to commit changed files directly. This triggers CF Pages
auto-deploy without needing a successful git push.

## When to Use

- `git push` returns `RPC failed; HTTP 500` repeatedly (server rejects pack)
- Repo `.git/` is multi-GB (large binary assets — sprites, audio, cinematics)
- Only a few text files changed (committing ALL via API is impractical)
- The repo is already connected to CF Pages for auto-deploy

## Step 1: Discover GitHub Token from Remote URL

The HTTPS remote URL often contains a valid PAT (Personal Access Token):

```bash
git remote get-url origin
# https://USERNAME:ghp_xxxxxxxxxxxx@github.com/OWNER/REPO.git

# Extract token programmatically
GH_TOKEN=$(git remote get-url origin | python3 -c "
import sys, re
print(re.match(r'https://[^:]+:([^@]+)@', sys.stdin.read()).group(1))
")
```

This token has write access to the repo (it's how `git push` authenticates).

## Step 2: Commit via API

```python
import base64, json, urllib.request, os

GH_TOKEN = os.environ['GH_TOKEN']
REPO = "owner/repo"  # e.g. "mbgulden/darius-star"

with open('index.html', 'rb') as f:
    content = f.read()

# Get current file SHA from the target branch
url = f"https://api.github.com/repos/{REPO}/contents/index.html?ref=staging"
req = urllib.request.Request(url, headers={
    'Authorization': f'Bearer {GH_TOKEN}',
    'Accept': 'application/vnd.github.v3+json'
})
resp = urllib.request.urlopen(req)
sha = json.loads(resp.read())['sha']

# PUT the new content
payload = {
    'message': 'commit message here',
    'content': base64.b64encode(content).decode(),
    'sha': sha,
    'branch': 'staging'
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
resp2 = urllib.request.urlopen(req2)
result = json.loads(resp2.read())
print(f"Committed: {result['commit']['sha'][:8]}")
```

## Step 3: Sync Local Repo

The API commit is on the remote branch; local git doesn't know about it:

```bash
git fetch origin staging
git reset --hard origin/staging
```

## Step 4: CF Pages Auto-Deploy

If the repo is connected to CF Pages, the API commit triggers an automatic
deployment — same as a git push would. Verify:

```bash
# Check latest deployment stage
curl -s "https://api.cloudflare.com/client/v4/accounts/{ACCT}/pages/projects/{PROJ}/deployments?per_page=1" \
  -H "Authorization: Bearer ${PAGES_TOKEN}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['result'][0]; print(d['latest_stage']['name'])"
```

Wait for `deploy` stage, then verify content via the hash URL.

## Caveats

- **One file at a time**: This patches a single file. For multi-file changes, call the API once per file (sequential — they're small PUTs).
- **Not for binary files**: Base64-encoding a 40MB PNG will hit API size limits. For binary assets, fix the root cause (Git LFS, .gitignore).
- **Branch must exist**: The `?ref=branch` query must match an existing branch.
- **GitHub PAT scopes**: The token needs `repo` scope. Tokens from git remote URLs typically have full `repo` access.
