---
name: hermes-artifact-publisher
description: "Publish any local file to files.growthwebdev.com as a clickable, Cloudflare Access-protected link. Use this skill whenever you (or Fred) are about to reference a local file path in a Telegram reply so Michael can click it instead of waiting for an attachment."
version: 0.1.0
author: Hermes Agent + Michael
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [hermes, files, publishing, access, prismatic]
---

# Hermes Artifact Publisher

Make every local file Michael might want to see a single click away. No attachments, no copy/paste, no "open a new SSH session."

## TL;DR

```bash
hermes-publish /home/ubuntu/work/Hermes-Research/reports/journal-continuity-audit/initial/fred-synthesis.md
# Output: https://files.growthwebdev.com/raw/published/journal-continuity-audit/initial/fred-synthesis.md
```

That's it. The user clicks the link, signs in once with email OTP, and reads the file in the browser.

## When to Use

- **You (or Fred) are about to mention a local file path in a Telegram reply.** Publish first, then include the URL in the reply. Do this *before* the reply text is finalized, not after.
- **You just produced a report, audit, or spec** and need to give the user a durable link to it.
- **You want to share a file with someone outside the Claude/Cursor/Code Server toolchain** and don't want to keep copying paths.

When **not** to use:

- The path is already inside a public Cloudflare Pages deployment (e.g. `activeoahutours.com/...`). Use the public URL.
- The file is a secret, credential, key, or anything in the safety blocklist. See [Safety Policy](#safety-policy).
- You only need to read the file as an LLM. Use the publisher's `/preview/<ws>/<rel>` endpoint or `read_file` directly — don't publish what the user will never see.

## Architecture

```
┌─────────────┐  publishes     ┌──────────────────┐  ingress    ┌──────────┐
│ hermes-     │ ─────────────▶ │ /home/ubuntu/    │             │ Cloudflare│
│ publish CLI │               │ .hermes/profiles/ │             │ tunnel   │
│ (Python)    │               │ orchestrator/     │             │          │
└─────────────┘               │ artifact_publisher/             └────┬─────┘
                              │ published/         │                  │
                              │ + workspaces map   │                  │ Access
                              └────────┬───────────┘                  │ (michael only)
                                       │                              │
                                       ▼                              ▼
                              ┌──────────────────┐            ┌──────────────┐
                              │ FastAPI on       │  HTTPS     │ hermes-user  │
                              │ 127.0.0.1:9120   │ ◀───────── │ browser      │
                              └──────────────────┘            └──────────────┘
```

- **Service:** `hermes_artifact_publisher` (FastAPI, profile-safe, NOT pipx-managed)
- **Bind:** `127.0.0.1:9120` (IPv4 only — see IPv6 pitfall below)
- **Hostname:** `files.growthwebdev.com` on tunnel `4a6097ff-dfcb-45f2-a856-3d967a9c798b`
- **Access policy:** allow `mbgulden@gmail.com` only, 720h session

## CLI: `hermes-publish`

`/home/ubuntu/.local/bin/hermes-publish` (also symlinked into every profile's `~/.local/bin/`).

```text
Usage: hermes-publish [options] SOURCE [SOURCE ...]

  --workspace WORKSPACE   Workspace label (default: published)
  --rel REL               Relative path inside the workspace
  --rel-from DIR          If set, compute --rel relative to DIR
  --json                  Machine-readable JSON
  --skip-health-check     Skip the local publisher health probe
  --yes                   Skip sensitive-path confirmation

Exit codes:
  0   Published
  2   Publisher service not running
  3   Sensitive path refused
  4   Bad workspace / source
```

## Standard URL Shapes

When you include a link in a Telegram reply, **always use the `/raw/` form** unless the user explicitly wants to download:

| Purpose | URL |
|---|---|
| View in browser | `https://files.growthwebdev.com/raw/<workspace>/<rel>` |
| Force download | `https://files.growthwebdev.com/download/<workspace>/<rel>` |
| JSON for LLM use | `https://files.growthwebdev.com/preview/<workspace>/<rel>` |
| Directory tree | `https://files.growthwebdev.com/tree/<workspace>/<rel>` |
| Workspace list | `https://files.growthwebdev.com/workspaces` |
| Health | `https://files.growthwebdev.com/health` |

Workspaces: `published`, `hermes-research-reports`, `prismatic-engine`, `agentic-swarm-ops`.

## Required Workflow for Fred and Any Agent

When generating a Telegram reply that includes a local file reference:

1. **Scan the draft for `/home/...` paths.** Use the `rewrite_paths_to_files_links.py` post-processor (see below) or do it manually.
2. **For each path, run `hermes-publish <path>`** (or `hermes-publish <path> --workspace <ws> --rel <rel>`).
3. **Replace the local path with the returned `https://files.growthwebdev.com/...` URL.**
4. **If publish fails:** say so explicitly in the reply. Do not pretend the link works.
5. **Refuse and surface to the user** any path matching the safety blocklist. Never bypass `--yes` silently.

## Telegram Post-Processor

`/home/ubuntu/.hermes/profiles/orchestrator/artifact_publisher/rewrite_paths_to_files_links.py` scans a string for `/home/...` paths, publishes them in parallel, and rewrites the string with the clickable link.

Hook it into the orchestrator reply flow by setting `HERMES_REPLY_PATH_REWRITE=1` in the orchestrator `.env`. The hook is opt-in to avoid surprising existing sessions.

```bash
echo "I left the report at /home/ubuntu/work/Hermes-Research/reports/foo.md." \
  | python3 /home/ubuntu/.hermes/profiles/orchestrator/artifact_publisher/rewrite_paths_to_files_links.py
# Output: I left the report at https://files.growthwebdev.com/raw/published/foo.md.
```

## Safety Policy

The publisher blocks any path whose name or path contains (case-insensitive):

`.env`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `.pem`, `.key`, `.p12`, `credentials`, `secrets`, `.kube/`, `auth.json`, `state.json`, `session.db`, `swarm_locks.json`, `.netrc`, `.pgpass`

The CLI refuses by default. The HTTP publisher returns `403` for any blocked file. The safety policy is enforced at three layers (CLI, HTTP, name-pattern blocklist) so a single bypass attempt fails in the next layer.

## Operational Runbook

### Publisher down (port 9120 not listening)

```bash
bash /home/ubuntu/.hermes/profiles/orchestrator/artifact_publisher/run_publisher.sh &
# Verify
curl -sS http://127.0.0.1:9120/health
```

### Ingress not routing to publisher

```bash
# Fetch current config
python3 - <<'PY'
import json, os, urllib.request
KEY=os.environ['CLOUDFLARE_GROWTHWEB_API_KEY']
EMAIL=os.environ['CLOUDFLARE_GROWTHWEB_EMAIL']
ACCT='196c1798da487413b0281ccc570f05a1'
TUNNEL='4a6097ff-dfcb-45f2-a856-3d967a9c798b'
req=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{ACCT}/cfd_tunnel/{TUNNEL}/configurations',
    headers={'X-Auth-Key':KEY,'X-Auth-Email':EMAIL})
print(json.dumps(json.load(urllib.request.urlopen(req))['result']['config'], indent=2))
PY
# Expect a rule: files.growthwebdev.com -> http://127.0.0.1:9120
```

### DNS missing or wrong zone

The CNAME must live in `growthwebdev.com` (zone `059d09f6cd5b84b8eedb0eaf1e1f4698`), not in `prismaticengine.com`. If you accidentally created it in the wrong zone, delete the bad one and recreate in the right zone:

```bash
# Delete wrong
curl -sS -X DELETE -H "X-Auth-Key: $KEY" -H "X-Auth-Email: $EMAIL" \
  "https://api.cloudflare.com/client/v4/zones/b008d11093f4852e7aae67e28c76c0f5/dns_records/<id>"
# Create right
curl -sS -X POST -H "X-Auth-Key: $KEY" -H "X-Auth-Email: $EMAIL" \
  -H "Content-Type: application/json" \
  -d '{"type":"CNAME","name":"files.growthwebdev.com","content":"4a6097ff-dfcb-45f2-a856-3d967a9c798b.cfargotunnel.com","proxied":true}' \
  "https://api.cloudflare.com/client/v4/zones/059d09f6cd5b84b8eedb0eaf1e1f4698/dns_records"
```

### Access policy change

```bash
# Lock to one email
python3 - <<'PY'
import json, os, urllib.request
KEY=os.environ['CLOUDFLARE_GROWTHWEB_API_KEY']
EMAIL=os.environ['CLOUDFLARE_GROWTHWEB_EMAIL']
ACCT='196c1798da487413b0281ccc570f05a1'
H='files.growthwebdev.com'
apps=json.load(urllib.request.urlopen(urllib.request.Request(
    f'https://api.cloudflare.com/client/v4/accounts/{ACCT}/access/apps?domain={H}',
    headers={'X-Auth-Key':KEY,'X-Auth-Email':EMAIL})))
APP_ID=apps['result'][0]['id']
for p in json.load(urllib.request.urlopen(urllib.request.Request(
    f'https://api.cloudflare.com/client/v4/accounts/{ACCT}/access/apps/{APP_ID}/policies',
    headers={'X-Auth-Key':KEY,'X-Auth-Email':EMAIL})))['result']:
    body={'name':'Michael only (mbgulden@gmail.com)','decision':'allow',
          'include':[{'email':{'email':'mbgulden@gmail.com'}}],'precedence':1}
    req=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{ACCT}/access/apps/{APP_ID}/policies/{p["id"]}',
        data=json.dumps(body).encode(),
        headers={'X-Auth-Key':KEY,'X-Auth-Email':EMAIL,'Content-Type':'application/json'}, method='PUT')
    print(json.load(urllib.request.urlopen(req)))
PY
```

## Pitfalls

- **⚠️ IPv6 localhost 502 root cause:** Always use `127.0.0.1` in tunnel ingress, never `localhost`. See the `cloudflare-tunnel-api-management` skill.
- **Pipx-managed static dir was the old workaround:** Don't write artifacts under `/home/ubuntu/.local/share/pipx/venvs/hermes-agent/...` — they vanish on Hermes updates. The publisher's `published/` workspace is the durable home.
- **Two `growthwebdev` zones exist in the API:** `growthwebdev.com` is the right one. `prismaticengine.com` also has the literal hostname `files.growthwebdev.com.prismaticengine.com` as a record — delete it if you see it. The CNAME for the tunnel must live in `growthwebdev.com`.
- **Tunnel pull is async:** After `PUT /configurations`, wait 30–60s before testing externally.
- **Don't conflate domains:** `activeoahu.com` and `activeoahutours.com` are different properties. Don't assume one URL covers the other.
- **Publish BEFORE you write the reply, not after:** A retry-mid-reply is more error-prone than publish-first.
- **SENSITIVE paths return non-zero exit:** Don't `--yes` past them silently; tell the user.

## Acceptance Criteria for Any Change to This System

A change to the publisher, CLI, tunnel ingress, or Access policy is complete when:

- [ ] `hermes-publish <file>` returns a clickable URL.
- [ ] The URL serves the file when accessed by an authenticated browser session.
- [ ] The URL returns 302 → Access login when accessed unauthenticated.
- [ ] The CLI refuses `.env`, `.key`, `.pem`, etc. by default.
- [ ] The HTTP publisher returns 403 for blocked files.
- [ ] The skill directory is present at both:
  - `/home/ubuntu/.hermes/profiles/orchestrator/skills/agent-orchestration/hermes-artifact-publisher/`
  - `/home/ubuntu/work/prismatic-engine/portable-skills/hermes-artifact-publisher/`
- [ ] Tunnel ingress contains `files.growthwebdev.com -> http://127.0.0.1:9120` ahead of the 404 catch-all.
- [ ] DNS CNAME for `files.growthwebdev.com` lives in zone `growthwebdev.com`, not `prismaticengine.com`.
- [ ] Access policy is locked to `mbgulden@gmail.com` with 720h session.
