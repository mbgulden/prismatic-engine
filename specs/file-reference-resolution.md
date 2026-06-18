# Prismatic Engine — File Reference Resolution Spec

## Purpose

The Prismatic Engine guarantees that any local file path an agent
references in conversation is **immediately clickable for the user**
through a stable, Access-protected URL — never a raw disk path,
never an attachment waiting for follow-up.

This is the "File Reference Resolution Rule" of the Prismatic Engine.
It applies to every interface (Telegram, Slack, dashboard, email) and
is **harness-agnostic**: it works with Hermes, OpenClaw, or no agent
harness at all.

## Engine vs Harness (mandatory separation)

The Prismatic Engine defines the contract. An agent harness (Hermes,
OpenClaw, Claude Code, Cursor, etc.) provides plumbing on top.

| Layer | Responsibility | Example |
|---|---|---|
| **Prismatic Engine** | Contract: URL shape, workspace allowlist, safety policy, CLI, post-processor, accept-test. Lives under `$PRISMATIC_HOME`. | `prismatic-publish`, `prismatic-reply`, `specs/file-reference-resolution.md` |
| **Agent harness (optional)** | Plumbing: cron, dashboards, OAuth refresh, alert routing, profile isolation, login shims. | `hermes-publish` shim, `hermes-reply` shim, profile-level PATH |
| **User** | The end consumer of the clickable link. | Michael |

A user who only has the engine binary and the CLI gets a working pipeline. A user who adds a harness gets extra plumbing (cron, dashboards, profile isolation) but the URL contract, workspace map, and safety policy are unchanged.

If you swap Hermes for OpenClaw:

- The CLI names `prismatic-publish` and `prismatic-reply` keep working.
- The skill stays valid; the harness just decides which agents can `skill_view` it.
- The tunnel ingress, DNS, and Access policy on the Cloudflare side don't change.
- Cron wiring is the only piece that needs to be re-done, and only because every harness has its own scheduler.

## The Standard

A clickable file reference always has the shape:

```
https://<host>/<route>/<workspace>/<rel>
```

Where:

- `<host>` is the engine's configured artifact hostname (default: `files.growthwebdev.com`).
- `<route>` is one of `raw` (default), `download`, `preview`, `tree`, `workspaces`, `health`.
- `<workspace>` is a name from the publisher's allowlist.
- `<rel>` is the file or directory path within that workspace.

## Required Components

A working Prismatic Engine file-reference pipeline requires all of these:

1. **Service** — a profile-safe FastAPI artifact publisher on `127.0.0.1:9120`. The publisher MUST live under `$PRISMATIC_HOME/bin/` (not under any agent harness's profile directory) so it survives both engine upgrades and harness changes.
2. **CLI** — `prismatic-publish` in `~/.local/bin/`, symlinked into every profile's `~/.local/bin/`. The CLI MUST NOT import from any agent harness.
3. **Post-processor** — `prismatic-reply` in `~/.local/bin/`, symlinked into every profile. Same constraint: no harness imports.
4. **Tunnel ingress** — a Cloudflare tunnel rule `<host> -> http://127.0.0.1:9120` ahead of the 404 catch-all.
5. **DNS** — a CNAME for the hostname in the appropriate zone (e.g. `growthwebdev.com`) pointing at the tunnel UUID.
6. **Access** — a Cloudflare Access self-hosted app for the hostname, locked to the user's verified email with a 720h session.
7. **Portable skill** — a SKILL.md document at `portable-skills/prismatic-artifact-publisher/SKILL.md` so any agent on any harness can find the contract.

## Optional: Harness Shims

A harness MAY provide thin shims that call the engine CLIs:

- `hermes-publish` → `prismatic-publish`
- `hermes-reply` → `prismatic-reply`

This lets existing harness-specific scripts keep working when the engine CLI is renamed. The shims MUST be 1–3 line wrappers that exec the engine CLI; they MUST NOT reimplement the logic.

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
- [ ] The portable skill is present at `portable-skills/prismatic-artifact-publisher/SKILL.md` and (optionally) mirrored into any harness-specific skill directories.
- [ ] The engine binary lives at `$PRISMATIC_HOME/bin/`, not under any harness's profile directory.
- [ ] The CLI and rewriter do not import from any agent harness (Hermes, OpenClaw, etc.).
- [ ] Tunnel ingress contains the host rule ahead of the 404 catch-all.
- [ ] DNS CNAME lives in the correct zone, not a sibling zone.
- [ ] Access policy is locked to a specific email with 720h session.
- [ ] `prismatic-publish` and `prismatic-reply` are the canonical CLI names.
- [ ] Harness shims (e.g. `hermes-publish`, `hermes-reply`) are ≤ 3 lines and exec the engine CLIs.
- [ ] `prismatic-reply < reply.txt` rewrites `/home/...` paths to URLs while preserving sentence punctuation, backticks, and parens.

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

- **Service:** `$PRISMATIC_HOME/bin/prismatic_artifact_publisher.py` (currently `/home/ubuntu/work/prismatic-engine/bin/prismatic_artifact_publisher.py`)
- **Rewriter:** `$PRISMATIC_HOME/bin/prismatic_rewrite_paths.py`
- **Reply wrapper:** `$PRISMATIC_HOME/bin/prismatic_reply_rewrite.py`
- **CLI:** `/home/ubuntu/.local/bin/prismatic-publish` (canonical) and `/home/ubuntu/.local/bin/hermes-publish` (Hermes shim)
- **Post-processor:** `/home/ubuntu/.local/bin/prismatic-reply` (canonical) and `/home/ubuntu/.local/bin/hermes-reply` (Hermes shim)
- **Portable skill:** `/home/ubuntu/work/prismatic-engine/portable-skills/prismatic-artifact-publisher/SKILL.md` (also mirrored at `~/.hermes/profiles/orchestrator/skills/agent-orchestration/prismatic-artifact-publisher/SKILL.md`)
- **Linear tracking issue:** GRO-1953
