---
name: prismatic-artifact-publisher
description: "Publish any local file to a stable, Access-protected URL using the Prismatic Engine File Reference Resolution standard. Harness-agnostic: works with Hermes, OpenClaw, or any other agent framework. Use this skill whenever you (or any agent) are about to reference a local file path in a reply so the user can click it."
version: 0.2.0
author: Prismatic Engine + Michael
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [prismatic, files, publishing, access, visibility]
---

# Prismatic Artifact Publisher

Make every local file the user might want to see a single click away. No attachments, no copy/paste, no "open a new SSH session." The skill is part of the **Prismatic Engine** and is fully harness-agnostic — Hermes is one of several agent harnesses that can call it, not a dependency.

## TL;DR

```bash
prismatic-publish /home/ubuntu/work/Hermes-Research/reports/journal-continuity-audit/initial/fred-synthesis.md
# Output: https://files.growthwebdev.com/raw/published/journal-continuity-audit/initial/fred-synthesis.md
```

That's it. The user clicks the link, signs in once with email OTP (or whatever Access policy you've configured), and reads the file in the browser.

## When to Use

- **You (or any agent) are about to mention a local file path in a reply.** Publish first, then include the URL in the reply. Do this *before* the reply text is finalized, not after.
- **You just produced a report, audit, or spec** and need to give the user a durable link to it.
- **You want to share a file with someone outside the Claude/Cursor/Code Server toolchain** and don't want to keep copying paths.

When **not** to use:

- The path is already inside a public Cloudflare Pages deployment (e.g. `activeoahutours.com/...`). Use the public URL.
- The file is a secret, credential, key, or anything in the safety blocklist. See [Safety Policy](#safety-policy).
- You only need to read the file as an LLM. Use the publisher's `/preview/<ws>/<rel>` endpoint or `read_file` directly — don't publish what the user will never see.

## Engine vs Harness

This is the Prismatic Engine standard. It works with or without an agent harness.

| Layer | What it provides | Examples |
|---|---|---|
| **Prismatic Engine (this skill)** | The contract: hostname, URL shape, workspace allowlist, safety policy, CLI, post-processor, accept-test. | `prismatic-publish`, `prismatic-reply`, `portable-skills/prismatic-artifact-publisher/` |
| **Agent harness (optional)** | Plumbing: cron, dashboards, OAuth refresh, alert routing, profile isolation. | Hermes (`hermes-publish` shim), OpenClaw, Cursor, Claude Code, etc. |

If you only have the engine and no harness, the CLI still works. If you have a harness, the harness may provide a thin shim (e.g. Hermes' `hermes-publish` is a 2-line shim that calls `prismatic-publish`) so existing scripts continue to work.

## Architecture

```
┌─────────────┐  publishes     ┌──────────────────┐  ingress    ┌──────────┐
│ prismatic-  │ ─────────────▶ │ $PRISMATIC_HOME/  │             │ Cloudflare│
│ publish CLI │               │ bin/prismatic_    │             │ tunnel   │
│ (Python,    │               │ artifact_publisher│             │          │
│  stdlib)    │               │ .py on            │             └────┬─────┘
└─────────────┘               │ 127.0.0.1:9120    │                  │
                              └────────┬───────────┘                  │ Access
                                       │                              │ (michael only)
                                       ▼                              ▼
                              ┌──────────────────┐            ┌──────────────┐
                              │ FastAPI on       │  HTTPS     │ user         │
                              │ 127.0.0.1:9120   │ ◀───────── │ browser      │
                              └──────────────────┘            └──────────────┘
```

- **Service:** `prismatic_artifact_publisher` (FastAPI, profile-safe, NOT pipx-managed)
- **Bind:** `127.0.0.1:9120` (IPv4 only — see IPv6 pitfall below)
- **Hostname:** `files.growthwebdev.com` on the active Cloudflare tunnel
- **Access policy:** user-defined (default in this deployment: `mbgulden@gmail.com`, 720h session)

## CLI: `prismatic-publish`

The canonical, harness-agnostic CLI. Pure stdlib, no Hermes or any other framework required.

```text
Usage: prismatic-publish [options] SOURCE [SOURCE ...]

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

The CLI is at `/home/ubuntu/.local/bin/prismatic-publish`. For Hermes-specific deployments, the shim `/home/ubuntu/.local/bin/hermes-publish` calls it. Both behave identically.

## CLI: `prismatic-reply`

The post-processor. Pipe any text and it rewrites `/home/...` paths to clickable URLs.

```bash
cat draft.txt | prismatic-reply
prismatic-reply --emit-links < draft.txt   # also prints the link list to stderr
```

The rewriter preserves sentence punctuation, backticks, and parens. Surfaces publish failures inline. Refuses to bypass the safety blocklist.

For Hermes-specific deployments, the shim `hermes-reply` calls it. Both behave identically.

## Standard URL Shapes

Always use `/raw/` unless the user explicitly wants to download:

| Purpose | URL |
|---|---|
| View in browser | `https://files.growthwebdev.com/raw/<workspace>/<rel>` |
| Force download | `https://files.growthwebdev.com/download/<workspace>/<rel>` |
| JSON for LLM use | `https://files.growthwebdev.com/preview/<workspace>/<rel>` |
| Directory tree | `https://files.growthwebdev.com/tree/<workspace>/<rel>` |
| Workspace list | `https://files.growthwebdev.com/workspaces` |
| Health | `https://files.growthwebdev.com/health` |

Workspaces: `published`, `hermes-research-reports`, `prismatic-engine`, `agentic-swarm-ops`.

## Required Agent Workflow (Harness-Agnostic)

1. **Scan the draft for `/home/...` paths.** Use the engine's rewriter (`prismatic-reply`) for the whole draft, or call `prismatic-publish <path>` per path.
2. **Replace the local path with the returned URL.**
3. **If publish fails:** say so explicitly. Do not pretend a broken path works.
4. **Refuse and surface to the user** any path matching the safety blocklist. Never bypass `--yes` silently.

## Harness Plumbing: How Hermes Layers On

Hermes adds (does not replace) the following on top of the engine:

- A `hermes-publish` shim that calls `prismatic-publish`. Lets existing cron scripts keep their old name.
- A `hermes-reply` shim that calls `prismatic-reply`. Same idea.
- A `HERMES_ARTIFACT_*` env-var passthrough in `/home/ubuntu/.hermes/profiles/<profile>/.env`. (Equivalent to `PRISMATIC_ARTIFACT_*`.)
- Profile-level symlinks of `~/.local/bin/hermes-publish` and `~/.local/bin/hermes-reply` so the shims are on `$PATH` for every Hermes profile.
- The skill is mirrored at `~/.hermes/profiles/orchestrator/skills/agent-orchestration/prismatic-artifact-publisher/SKILL.md` so any Hermes agent can `skill_view` it.
- A cron / dispatcher path that the engine doesn't need: alerting, dashboards, and a one-time Access policy management helper.

If you swap Hermes for OpenClaw, the engine binary, the CLI, and the URL contract are unchanged. You only need a different way to wire cron / profile PATH.

## Safety Policy

The publisher blocks any path whose name or path contains (case-insensitive):

`.env`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `.pem`, `.key`, `.p12`, `credentials`, `secrets`, `.kube/`, `auth.json`, `state.json`, `session.db`, `swarm_locks.json`, `.netrc`, `.pgpass`

The CLI refuses by default. The HTTP publisher returns `403` for any blocked file. The safety policy is enforced at three layers (CLI, HTTP, name-pattern blocklist) so a single bypass attempt fails in the next layer.

## Operational Runbook

### Publisher down (port 9120 not listening)

```bash
# Engine-level: run the engine binary directly
python3 $PRISMATIC_HOME/bin/prismatic_artifact_publisher.py

# Or via uvicorn
cd $PRISMATIC_HOME && python3 -m uvicorn bin.prismatic_artifact_publisher:app \
  --host 127.0.0.1 --port 9120
```

### Ingress not routing to publisher

```bash
python3 - <<'PY'
import json, os, urllib.request
KEY=os.environ['PRISMATIC_GROWTHWEB_API_KEY']
EMAIL=os.environ['PRISMATIC_GROWTHWEB_EMAIL']
ACCT='196c1798da487413b0281ccc570f05a1'
TUNNEL='4a6097ff-dfcb-45f2-a856-3d967a9c798b'
req=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{ACCT}/cfd_tunnel/{TUNNEL}/configurations',
    headers={'X-Auth-Key':KEY,'X-Auth-Email':EMAIL})
print(json.dumps(json.load(urllib.request.urlopen(req))['result']['config'], indent=2))
PY
# Expect a rule: files.growthwebdev.com -> http://127.0.0.1:9120
```

### DNS missing or wrong zone

The CNAME must live in `growthwebdev.com` (zone `059d09f6cd5b84b8eedb0eaf1e1f4698`), not in `prismaticengine.com`. If you accidentally created it in the wrong zone, delete the bad one and recreate in the right zone.

### Access policy change

```bash
python3 - <<'PY'
import json, os, urllib.request
KEY=os.environ['PRISMATIC_GROWTHWEB_API_KEY']
EMAIL=os.environ['PRISMATIC_GROWTHWEB_EMAIL']
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
- **Pipx-managed static dir is the old workaround:** Don't write artifacts under `/home/ubuntu/.local/share/pipx/venvs/hermes-agent/...` — they vanish on Hermes updates. The engine binary in `$PRISMATIC_HOME/bin/` is the durable home.
- **Two `growthwebdev` zones exist in the API:** `growthwebdev.com` is the right one. `prismaticengine.com` also has the literal hostname `files.growthwebdev.com.prismaticengine.com` as a record — delete it if you see it. The CNAME for the tunnel must live in `growthwebdev.com`.
- **Tunnel pull is async:** After `PUT /configurations`, wait 30–60s before testing externally.
- **Don't conflate domains:** `activeoahu.com` and `activeoahutours.com` are different properties. Don't assume one URL covers the other.
- **Publish BEFORE you write the reply, not after:** A retry-mid-reply is more error-prone than publish-first.
- **SENSITIVE paths return non-zero exit:** Don't `--yes` past them silently; tell the user.

## Acceptance Criteria for Any Change to This System

A change to the publisher, CLI, tunnel ingress, or Access policy is complete when:

- [ ] `prismatic-publish <file>` returns a clickable URL.
- [ ] The URL serves the file when accessed by an authenticated browser session.
- [ ] The URL returns 302 → Access login when accessed unauthenticated.
- [ ] The CLI refuses `.env`, `.key`, `.pem`, etc. by default.
- [ ] The HTTP publisher returns 403 for blocked files.
- [ ] The skill directory is present at both:
  - `/home/ubuntu/work/prismatic-engine/portable-skills/prismatic-artifact-publisher/`
  - (Optional, harness-coupled) `~/.hermes/profiles/orchestrator/skills/agent-orchestration/prismatic-artifact-publisher/`
- [ ] The engine binary lives at `$PRISMATIC_HOME/bin/prismatic_artifact_publisher.py`, not under any harness's profile directory.
- [ ] Tunnel ingress contains `files.growthwebdev.com -> http://127.0.0.1:9120` ahead of the 404 catch-all.
- [ ] DNS CNAME for `files.growthwebdev.com` lives in zone `growthwebdev.com`, not `prismaticengine.com`.
- [ ] Access policy is locked to a specific email with 720h session.
- [ ] `prismatic-publish` and `prismatic-reply` are the canonical CLI names. Harness shims (e.g. `hermes-publish`, `hermes-reply`) call them.
- [ ] The rewriter preserves sentence punctuation, backticks, and parens.

## Where things live

| What | Engine path | Hermes-specific path |
|---|---|---|
| Publisher binary | `$PRISMATIC_HOME/bin/prismatic_artifact_publisher.py` | n/a |
| Rewriter | `$PRISMATIC_HOME/bin/prismatic_rewrite_paths.py` | n/a |
| Reply wrapper | `$PRISMATIC_HOME/bin/prismatic_reply_rewrite.py` | n/a |
| CLI: `prismatic-publish` | `~/.local/bin/prismatic-publish` | n/a |
| CLI: `prismatic-reply` | `~/.local/bin/prismatic-reply` | n/a |
| Harness shim: `hermes-publish` | n/a | `~/.local/bin/hermes-publish` (calls prismatic-publish) |
| Harness shim: `hermes-reply` | n/a | `~/.local/bin/hermes-reply` (calls prismatic-reply) |
| Skill | `portable-skills/prismatic-artifact-publisher/SKILL.md` | `~/.hermes/profiles/orchestrator/skills/agent-orchestration/prismatic-artifact-publisher/SKILL.md` |
| Spec | `specs/file-reference-resolution.md` | n/a |
| Linear tracking | GRO-1953 | n/a |
