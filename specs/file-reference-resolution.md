# Prismatic Engine — File Reference Resolution Spec

## Purpose

The Prismatic Engine guarantees that any local file path an agent
references in conversation is **immediately clickable for the user**
through a stable, Access-protected Hermes URL — never a raw disk path,
never an attachment waiting for follow-up.

This is the "File Reference Resolution Rule" of the Prismatic Engine.
It applies to every agent (Fred, Kai, AGY, Ned, Jules CLI) and every
interface (Telegram, Slack, dashboard, email).

## The Standard

A clickable file reference always has the shape:

```
https://files.growthwebdev.com/<route>/<workspace>/<rel>
```

Where:

- `<route>` is one of `raw` (default), `download`, `preview`, `tree`, `workspaces`, `health`.
- `<workspace>` is a name from the publisher's allowlist.
- `<rel>` is the file or directory path within that workspace.

## Required Components

A working Prismatic Engine file-reference pipeline requires all of these:

1. **Service** — a profile-safe FastAPI artifact publisher on `127.0.0.1:9120`. The publisher MUST live outside the pipx-managed Hermes directory tree so it survives Hermes updates.
2. **Tunnel ingress** — a Cloudflare tunnel rule `files.growthwebdev.com -> http://127.0.0.1:9120` ahead of the 404 catch-all.
3. **DNS** — a CNAME for `files.growthwebdev.com` in the `growthwebdev.com` zone (not `prismaticengine.com`) pointing at the tunnel UUID.
4. **Access** — a Cloudflare Access self-hosted app for the hostname, locked to the user's verified email with a 720h session.
5. **CLI** — `hermes-publish` in `~/.local/bin/`, symlinked into every profile's `~/.local/bin/`.
6. **Post-processor** — `hermes-reply` in `~/.local/bin/`, symlinked into every profile, that scans reply text and rewrites local paths to URLs.
7. **Portable skill** — a SKILL.md document available in both the orchestrator profile and the portable Prismatic Engine so any agent can find the contract.

## Required Agent Behavior

Every agent (Fred, Kai, AGY, Ned, Jules CLI) MUST:

1. Before referencing a local file path in a reply, run `hermes-publish <path>` and use the returned URL.
2. Use the `/raw/` route by default; use `/download/` only when the user explicitly wants a download.
3. Surface publish failures inline (do not pretend a broken path works).
4. Refuse to bypass the safety blocklist without explicit user confirmation.
5. Pipe multi-path replies through `hermes-reply` so the rewrite is automatic.

## Identity & Domain Integrity

The Prismatic Engine treats `activeoahu.com` and `activeoahutours.com` as different properties. The same rule applies to all brands: do not infer that similar names refer to the same domain, repo, deployment, or Linear project.

Every file-reference action carries explicit identity keys:

- Public domain(s) (e.g. `files.growthwebdev.com`).
- Workspace label.
- Relative path within the workspace.
- Source file path on local disk.
- Timestamp.

The publisher's `/preview/<ws>/<rel>` JSON response includes the absolute local path, so the chain of custody is auditable.

## Safety Policy

The publisher blocks any path whose name or path contains (case-insensitive):

`.env`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `.pem`, `.key`, `.p12`, `credentials`, `secrets`, `.kube/`, `auth.json`, `state.json`, `session.db`, `swarm_locks.json`, `.netrc`, `.pgpass`

The CLI refuses by default. The HTTP publisher returns `403` for any blocked file. The safety policy is enforced at three layers (CLI, HTTP, name-pattern blocklist) so a single bypass attempt fails in the next layer.

## Verification Gates

A change to the file-reference pipeline is complete when ALL of the following are true:

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
- [ ] Access policy is locked to a specific email (e.g. `mbgulden@gmail.com`) with 720h session.
- [ ] `hermes-publish` and `hermes-reply` are symlinked into kai, autobot, ned, and orchestrator profile `~/.local/bin/`.
- [ ] `hermes-reply < reply.txt` rewrites `/home/...` paths to URLs while preserving sentence punctuation, backticks, and parens.

## Pitfalls

- **⚠️ IPv6 localhost 502 root cause:** Always use `127.0.0.1` in tunnel ingress, never `localhost`. The gateway binds to `0.0.0.0:9119` (IPv4 only) and cloudflared tries IPv6 first.
- **Pipx-managed static dir is the old workaround:** Don't write artifacts under `/home/ubuntu/.local/share/pipx/venvs/hermes-agent/...` — they vanish on Hermes updates. The publisher's `published/` workspace is the durable home.
- **Two `growthwebdev` zones exist:** `growthwebdev.com` is the right one. The CNAME for the tunnel must live in `growthwebdev.com`, not `prismaticengine.com`. If you see a record `files.growthwebdev.com.prismaticengine.com`, delete it.
- **Tunnel pull is async:** After `PUT /configurations`, wait 30–60s before testing externally.
- **Don't conflate domains:** `activeoahu.com` and `activeoahutours.com` are different properties. Don't assume one URL covers the other.
- **Publish BEFORE you write the reply, not after:** A retry-mid-reply is more error-prone than publish-first.
- **SENSITIVE paths return non-zero exit:** Don't `--yes` past them silently; tell the user.
- **The Telegram post-processor is opt-in:** Set `HERMES_REPLY_PATH_REWRITE=1` in the profile `.env` to enable automatic rewrites. Off by default to avoid surprising existing sessions.

## Reference Implementation

- **Service:** `/home/ubuntu/.hermes/profiles/orchestrator/artifact_publisher/hermes_artifact_publisher.py`
- **CLI:** `/home/ubuntu/.local/bin/hermes-publish` (symlinked into all profiles)
- **Post-processor:** `/home/ubuntu/.local/bin/hermes-reply` (symlinked into all profiles)
- **Portable skill:** `/home/ubuntu/work/prismatic-engine/portable-skills/hermes-artifact-publisher/SKILL.md`
- **Linear tracking issue:** GRO-1953
