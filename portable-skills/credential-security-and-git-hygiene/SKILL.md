---
name: credential-security-and-git-hygiene
description: >
  Consolidated security & hygiene umbrella — prevent, detect, and remediate credential
  leaks in code repositories AND manage large binary asset bloat. Covers env-var patterns,
  .gitignore hygiene, systemd credential management, git history scrubbing (filter-repo +
  filter-branch + orphan branch), post-leak token rotation, PII handling, repo bloat
  diagnosis, disk-full failure recovery, Git LFS patterns for AI-generated asset repos,
  and git-bloat diagnosis fix (see references/git-bloat-from-ai-assets.md).
  Mandatory pre-push checklist for every new project, config file, bot, or script.
  Absorbs the former secrets-hygiene skill.
triggers:
  - any new project or writing config or creating a bot
  - API key or token or secret or credential in code
  - hardcoded password or token or api_key in committed file
  - git history scrub or remove secret from repo
  - .env file setup or gitignore secrets
  - token leaked or exposed in git history
  - systemd EnvironmentFile or service credential
  - force push after secret removal
  - env var masking or grep truncating credentials
  - repo is unusually large or .git is multi-GB
  - disk full during git push or git gc times out
  - gitignore for AI-generated assets or large binary files
  - Git LFS for sprites, audio, or video assets
  - git hygiene cleanup or repo audit
always-delegate: false
---

# Credential Security & Git Hygiene

## Golden Rule

**Never commit secrets to version control.** Period. No exceptions for
"temporary" or "placeholder" tokens. A secret in any commit becomes part
of immutable git history and is discoverable forever.

## Setup Pattern (any new service)

### 1. Code reads from environment only

```python
# CORRECT
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
```

Never: `BOT_TOKEN = "12345:abcde"` in source files.

### 2. .env file (local only, never committed)

```bash
# /home/user/work/project/.env
TELEGRAM_BOT_TOKEN=12345:abcde
DEEPSEEK_API_KEY=sk-...
```

### 3. .gitignore blocks .env

```
# .gitignore
.env
*.db
__pycache__/
*.pyc
```

Add .gitignore BEFORE the first commit, or at minimum before .env is created.

### 4. systemd services use EnvironmentFile

Point systemd at the project's local `.env`:

```ini
[Service]
Type=simple
User=ubuntu
WorkingDirectory=$PRISMATIC_HOME/work/project-name
EnvironmentFile=$PRISMATIC_HOME/work/project-name/.env
ExecStart=/path/to/venv/bin/python $PRISMATIC_HOME/work/project-name/bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
```

After creating or updating the unit file:
```bash
sudo cp project.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now project  # or: restart
```

Never put secrets directly in the systemd unit file — it may be committed.

### 5. File Layout (canonical)

```
~/work/project-name/
├── .env              # Secrets live HERE (gitignored)
├── .gitignore        # Must contain .env
├── bot.py            # Reads os.environ.get()
└── project.service   # systemd unit referencing .env
```

## Env Var Access: Use $VAR Directly, Never Grep/Cat

**CRITICAL PITFALL**: Environment variables stored in systemd EnvironmentFile
or shell profiles may be **masked in terminal output**. When you `grep` or
`cat` them, the display shows truncated values like `lin_ap...Y5LR`. The
truncated string is NOT a valid key — API calls will fail with
`AUTHENTICATION_ERROR`.

**Always use the variable directly in scripts:**

```bash
# ❌ WRONG — grep/cat produces truncated/masked value, API calls fail
LINEAR_KEY=$(grep -oP 'LINEAR_API_KEY=\K[^ ]+' ~/.env)
curl ... -H "Authorization: $LINEAR_KEY"  # fails!

# ✅ RIGHT — use the env var directly, it holds the full value
curl ... -H "Authorization: $LINEAR_API_KEY"  # works!
```

When you must extract a secret from a file (copying between profiles, debugging),
use Python binary read to bypass masking. See `references/reading-masked-secrets-from-files.md`.

## Stored Credential Validation

**Never assume a stored credential still works.** API keys and tokens expire
or get revoked. The `cfk_` prefix does NOT guarantee a working Global API Key.
Always validate with a lightweight API call before using in production flows.

**Multi-profile drift:** When multiple Hermes profiles share the same provider,
their `.env` files can drift independently — one gets updated, others keep
stale keys. After any key rotation, verify ALL profiles with the same provider.
See `hermes-agent-profiles-and-swarms` skill, reference `multi-profile-key-drift-diagnostic.md`
for the full diagnostic sequence.

```bash
# Cloudflare Global API Key test (cfk_ prefix)
curl -s "https://api.cloudflare.com/client/v4/user" \
  -H "X-Auth-Email: $EMAIL" -H "X-Auth-Key: $KEY"
# 9103 "Unknown X-Auth-Key" → expired or revoked

# Cloudflare API Token test
curl -s "https://api.cloudflare.com/client/v4/zones" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
# 1000 "Invalid API Token" → expired or revoked
```

**Prefix ≠ validity.** `cfk_5s...713a` returned 9103 in June 2026 — the key
was expired. The real credential was `cfat_sW...6a1c` (API Token, Bearer auth)
in a nested `.env` path. Two profiles had the same invalid key copied between them.

## Remediation: Secret Already Committed

When a token has been pushed to a public repo, execute in order:

### 1. Revoke the token immediately

Go to the service's admin panel (BotFather for Telegram, API dashboard for
others) and revoke/regenerate the token. The old token is compromised and
will be discovered by automated scanners within minutes.

### 2. Scrub git history

**Option A: git filter-repo (preferred — faster, safer, no backup refs)**
```bash
pip install git-filter-repo
cd /path/to/repo
git filter-repo --replace-text <(echo "LEAKED_SECRET==>***REDACTED***")
```

**Option B: git filter-branch (fallback — leaves backup refs that must be cleaned)**
```bash
git filter-branch --force --tree-filter \
  'if [ -f file_with_secret.py ]; then
     sed -i "s/LEAKED_SECRET/***REDACTED***/g" file_with_secret.py;
   fi' \
  -- --all

# CRITICAL: Clean up backup refs or they still contain the secret
git for-each-ref --format="%(refname)" refs/original/ | xargs -n1 git update-ref -d
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

`git filter-repo` is preferred because `filter-branch` leaves `refs/original/`
backup refs that still contain the secret unless manually cleaned. Many users
forget this step, making the scrub incomplete.

Verify: `git log -p | grep -i "LEAKED_SECRET"` should return nothing.

### 3. Force push clean history

```bash
git push --force --all origin
```

This overwrites all branches on the remote. Collaborators must re-clone.

### 4. Replace token in .env, restart services, verify

Update the local `.env` with the new token, then restart any services that
depend on it:

```bash
sudo systemctl restart project-name
```

Verify the secret is gone from the remote:

```bash
# Check GitHub raw (or equivalent for your host)
curl -s "https://raw.githubusercontent.com/user/repo/main/file.py" | grep -c "LEAKED_SECRET"
# Should return 0
```

## Pre-commit Checklist

Before ANY `git push`:
- [ ] `grep -r "token\|secret\|api_key\|password\|TOKEN\|SECRET\|API_KEY" --include="*.py" --include="*.yaml" --include="*.json" --include="*.env" . | grep -v ".gitignore" | grep -v "os.environ"`
- [ ] .gitignore exists and includes `.env`, `*.db`, `__pycache__/`
- [ ] All secrets come from `os.environ.get()` not string literals
- [ ] systemd units use `EnvironmentFile=`, not inline secrets

## PII in Source Code (Distinct from API Keys)

Personal information is NOT a credential, but it's equally dangerous in a public
repo. Birth dates, names, family relationships, home addresses, and geographic
coordinates tied to specific people are permanent identifiers — you cannot
"rotate" them like an API key.

### Detection

Search source files for PII patterns before ANY push:

```bash
# Names, dates, locations
grep -rnE "(Michael|Becca|Benjamin|William|Victoria)" --include="*.py" src/
grep -rnE "(198[0-9]|199[0-9]|200[0-9]|201[0-9]|202[0-9])" --include="*.py" src/
grep -rnE "(Simi Valley|Tacoma|Kailua|Honolulu)" --include="*.py" src/

# Birth-time patterns (decimal hours like 17.1167)
grep -rnE "[0-9]+\.[0-9]{4}" --include="*.py" src/

# Family member names in test fixtures
grep -rnE "family|birth" --include="*.py" tests/
```

### Remediation Pattern

**Do NOT just delete files** — they persist in git history forever. The full
pattern from the OpenHumanDesignMCP PII scrub (2026-05-28, 50+ occurrences
across 10 files):

1. **Replace with synthetic data** in source files: use Jan 1 2000 12:00 UTC,
   equator (0°, 0°), generic names ("Example Person", "Person A/B/C").
2. **Move sensitive test files** to `tests/local/` (gitignored directory).
3. **Create sanitized templates** with the same structure but no real data.
4. **Harden .gitignore with glob patterns**, not just extensions:
   ```
   tests/local/
   *birth*
   *family*
   *friends*
   *personal*
   ```
5. **Verify**: re-run the PII grep patterns — they should return zero hits
   in committed files. Remaining hits should only be public geographic data
   (city lat/lon coordinates in a geo resolver are fine).

### Git History Caveat

File deletion and replacement do NOT purge PII from git history. Old commits
still contain the data. Full removal requires `git filter-branch` or
`BFG Repo-Cleaner`, which rewrites the entire commit graph and requires a
force push. Only do this if the PII was exposed on a public repo and the
user explicitly requests it — it's destructive and breaks all existing clones.

- [ ] Token reads from `os.environ.get()` — no string literals in source
- [ ] `.env` exists at project root (mode 600)
- [ ] `.gitignore` includes `.env`
- [ ] `git log -p | grep TOKEN` returns nothing
- [ ] `systemctl show project -p EnvironmentFiles` points to the correct `.env`
- [ ] `sudo journalctl -u project --since "1 min ago"` shows clean start, no "Invalid token"

## Large Binary Assets & Repo Bloat

Repos with AI-generated assets (sprites, audio, cinematics) or large binaries
accumulate multi-GB `.git` directories that consume disk space and block pushes.
This is a git hygiene issue distinct from secrets — but equally critical.

### Git push fails on large repos → GitHub API commit

When `git push` returns `RPC failed; HTTP 500` repeatedly on repos >2GB,
the server-side pack negotiation times out. The workaround is to commit
changed files via the GitHub REST API (`PUT /repos/{owner}/{repo}/contents/{path}`),
which triggers CF Pages auto-deploy without a git push. See
`cloudflare-deployment` skill, reference `references/github-api-commit-for-large-repos.md`
for the full pattern.

### Discovering GitHub token from git remote URL

The HTTPS remote URL often contains a valid PAT:

```bash
git remote get-url origin
# https://USERNAME:ghp_xxxxxxxxxxxx@github.com/OWNER/REPO.git

# Extract programmatically:
GH_TOKEN=$(git remote get-url origin | python3 -c "
import sys, re
print(re.match(r'https://[^:]+:([^@]+)@', sys.stdin.read()).group(1))
")
```

This is NOT a credential leak — it's reading the token already stored by `git`
in its own remote configuration. Use this token for GitHub API calls when no
`GITHUB_TOKEN` env var is set.

### Quick diagnosis
```bash
du -sh .git                              # How big is git history?
df -h /                                  # Is disk full?
git rev-list --objects --all \
  | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \
  | awk '/^blob/ {print $3, $4}' \
  | sort -rn | head -20                  # Largest objects in history
```

### The disk-full failure cascade
When disk space runs out: commit succeeds locally, push fails (silently or
with "No space left on device"), `git gc` times out, `git fetch` breaks.
Recognition avoids wasted retries. Free space first, then diagnose bloat source.

### .gitignore for AI asset repos
Distinguish runtime assets (committed — the game needs them) from generation
artifacts (gitignored — ephemeral tool output):
```gitignore
# Generation catalogs (tool output, not runtime)
assets/ASSET_CATALOG.json
assets/*_CATALOG.json
assets/audio/test/
*.tmp
generation_logs/
.wrangler/
```

### Git LFS for large runtime assets
For assets the game actually loads, use LFS to prevent history bloat:
```bash
git lfs track "assets/audio/ambient/*.wav"
git lfs track "assets/sprites/**/*.png"
```

Full diagnosis patterns, recovery sequence, and real-world example in
`references/git-repo-bloat-diagnosis.md`.
