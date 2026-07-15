# Prismatic Production Durability Standard

**Status:** canonical standard  
**Applies to:** every production-facing Prismatic project, route, dashboard, gateway, agent workflow, and deployment handoff  
**Immediate case study / first enforcement target:** `/workspace-tree` on `prismatic.growthwebdev.com`  
**Required document marker:** `PRODUCTION_DURABILITY_STANDARD_DOC_OK`

---

## 1. Why this standard exists

The `/workspace-tree` black-page incident exposed a platform-standard failure, not merely a route bug.

A production-facing Prismatic surface is not durable when it depends on any of these conditions being accidentally true:

- a mutable shared development worktree happens to be on the right branch;
- an unreviewed checkout is serving live traffic;
- a CDN script loads successfully before the page can render anything useful;
- an agent self-reports a fix without local gateway proof, public route proof, path-safety proof, and visual/browser proof;
- a deploy/restart/reload happens implicitly as a side effect of local hacking;
- proof is described as “done” without separating ad-hoc targeted verification from canonical suite green.

Production must live durably. A route fix is not production-ready until the code, checkout, runtime, deployment, public route, browser render, and rollback posture are all verifiable.

`PRODUCTION_DURABILITY_STANDARD_DOC_OK`

---

## 2. Non-negotiable production workflow

Every production-facing Prismatic fix must follow this ladder:

```text
clean production-safe branch/worktree
→ local gateway/service reproduces the problem
→ patch in reviewed branch, not mutable live checkout
→ local route/API/browser proof passes
→ path safety/security checks pass
→ intentional deploy/restart/reload
→ public/authenticated route proof passes
→ screenshot/browser proof attached
→ production source/worktree remains durable and clean
```

No agent may skip directly from “I edited a file” to “production is fixed.”

---

## 3. production checkout / worktree model

### 3.1 mutable live worktrees are unsafe

A live service must not depend on `/home/ubuntu/work/prismatic-engine` or any other shared development checkout being on a particular branch.

Shared worktrees are allowed for development, review, and local verification. They are not a durable production source of truth because they can be changed by:

- branch switches;
- agent auto-checkpoints;
- untracked files;
- force-push cleanup;
- local experiments;
- interrupted rebases;
- stale locks;
- another agent working in a different lane.

### 3.2 Required durable production source

Production-facing services must run from one of these durable models:

1. **Immutable release artifact** built from a known commit SHA.
2. **Dedicated production checkout/worktree** pinned to a deployment branch and cleaned before deploy.
3. **Container/image artifact** tagged by commit SHA and promoted intentionally.
4. **Systemd service source path** that can be read back and matched to the deployed commit.

The production source must be inspectable after deploy:

```text
service name
source path
branch/tag/commit SHA
dirty status
restart timestamp
rollback target
```

---

## 4. Branch naming and PR expectations

Production-facing route fixes must be worked from clean branches, not directly from mutable `main`, `deploy-fresh`, or a live checkout.

Recommended branch forms:

```text
feature/fred-production-route-<short-name>
feature/fred-production-durability-standard
hotfix/fred-production-route-<short-name>
```

Every production-facing PR must state:

- exact user-facing route(s), e.g. `/workspace-tree`;
- local reproduce command and result;
- local proof command and result;
- path-safety/security proof;
- deploy/restart/reload plan;
- public/authenticated proof command and result;
- screenshot/browser proof path;
- rollback command/path;
- whether verification is ad-hoc targeted or canonical suite green.

---

## 5. local-first verification ladder

Local proof must happen before public proof.

Minimum local proof packet:

```text
repo branch and commit SHA
local service/gateway source path
local /health result
route table includes expected route
local route/API status for the target path
local browser/DOM render proof when UI is touched
path-safety checks for route inputs
py_compile or equivalent build/lint for touched backend files
node --check or equivalent for touched frontend JavaScript
```

For `/workspace-tree`, local proof must include at least:

```text
GET /health
GET /workspace-tree?file=<safe repo-relative file>
GET /api/workspace-tree/preview?file=<safe repo-relative file>
GET /api/gateway/dashboard/contracts
blocked traversal: ../../etc/passwd
blocked absolute private path: /etc/passwd
blocked encoded traversal: %2e%2e/%2e%2e/etc/passwd
```

---

## 6. public/authenticated verification ladder

Public proof happens after local proof and after intentional deploy/restart/reload.

Minimum public/authenticated proof packet:

```text
public URL
HTTP status and headers
body/DOM marker proving the intended route rendered
Cloudflare/auth result clearly labeled
browser console errors/warnings reviewed
screenshot path or browser proof artifact
commit SHA or release artifact serving production
```

If Cloudflare Access or another auth layer blocks unauthenticated proof, agents must report:

```text
public_status=skipped_auth_required
reason=<exact auth/access response>
```

They must not silently replace public proof with localhost proof.

---

## 7. Browser and screenshot evidence expectations

User-facing pages require browser evidence, not just source inspection or `curl`.

Acceptable evidence:

- screenshot path captured after the route renders;
- browser DOM assertions for title/header/content markers;
- browser console readback showing no fatal script errors;
- accessibility-tree proof that primary controls are reachable;
- explicit fallback proof when CDN scripts are unavailable.

For large pages, a compact DOM proof may accompany or replace a large screenshot only if it proves:

```text
route path
page title/header
no blank page
no 404 body
no fatal JavaScript console error
primary route content rendered
```

---

## 8. CDN and frontend fallback robustness

Production user-facing pages must not go blank solely because a CDN script failed.

Required behavior:

- critical route shell renders without third-party CDN JavaScript;
- failed enhancement scripts produce a visible degraded state, not a blank page;
- local/static fallback exists for critical CSS/JS where practical;
- inline bootstrap errors are caught and reported in-page or through an operator-safe error panel;
- browser proof includes either CDN success or fallback-mode proof.

For `/workspace-tree`, the page must render a durable shell with a useful error/empty state even if tree enhancement scripts fail.

---

## 9. Path-safety and security expectations

Production route fixes must preserve security while restoring functionality.

Minimum path-safety requirements for workspace-tree-like routes:

- only allow configured workspace roots;
- normalize and resolve paths before reading;
- block `..` traversal;
- block encoded traversal;
- block absolute private paths outside allowed roots;
- avoid returning raw secret files;
- cap preview size where applicable;
- return safe `400`, `403`, or equivalent blocked responses for unsafe inputs;
- include tests or verifier output for representative unsafe patterns.

Security proof is required even when the visible bug is “black page” or “404.”

---

## 10. Deploy, restart, reload, and source-readback rules

A production-facing fix is not live until deployment is intentional and read back.

Required deploy/restart packet:

```text
deploy command or PR merge/release action
service restart/reload command
systemd/nginx/process readback
health after restart
route proof after restart
source path and commit SHA after restart
rollback target
```

Examples:

```bash
python3 -m py_compile prismatic/gateway/server.py
sudo systemctl restart prismatic-gateway
sudo nginx -t
sudo systemctl reload nginx
curl -sS http://127.0.0.1:9000/health
```

Agents must distinguish:

- **merged** — code is in the repository;
- **deployed** — production source/runtime intentionally updated;
- **verified public** — public/authenticated route proof passes;
- **visual-proofed** — browser/screenshot proof attached.

---

## 11. Rollback expectations

Every production-facing PR must include a rollback note:

```text
previous commit/release
command or PR revert path
service restart/reload needed
data/state migration risk
expected rollback verification
```

Rollback is not optional when the route is user-facing.

---

## 12. Required proof packet format

Use this format in PRs, Linear comments, and final agent reports:

```text
PRODUCTION_DURABILITY_PROOF_PACKET

Scope:
- project:
- route(s):
- commit SHA:
- source path:
- verification type: ad_hoc_targeted | canonical_suite_green | skipped_auth_required

Branch/worktree:
- branch:
- clean before patch: yes/no + evidence
- production source durable: yes/no + source model

Local reproduce:
- command:
- result:

Patch:
- files changed:
- PR:

Local proof:
- /health:
- route table:
- route/API:
- browser/DOM/screenshot:

Security/path safety:
- safe path:
- traversal blocked:
- encoded traversal blocked:
- absolute private path blocked:

Deploy/restart/reload:
- command/action:
- service readback:
- source/commit readback:

Public/authenticated proof:
- URL:
- status:
- auth state:
- body/DOM marker:
- screenshot/browser proof:

CDN/fallback proof:
- CDN mode:
- fallback mode:

Rollback:
- rollback target:
- rollback command:
- rollback verification:

Cleanup:
- worktree clean:
- temp artifacts cleaned or retained intentionally:
- locks released:
```

---

## 13. Required markers

A production-facing fix may only claim the final standard marker when all applicable checks are satisfied or explicitly labeled as skipped with cause:

```text
PRODUCTION_DURABILITY_STANDARD_DOC_OK
AGENT_PRODUCTION_ROUTE_CHECKLIST_OK
PRODUCTION_DURABILITY_VERIFIER_OK
PRISMATIC_PRODUCTION_DURABILITY_STANDARD_OK
```

`PRISMATIC_PRODUCTION_DURABILITY_STANDARD_OK` means the standard/checklist/verifier have shipped and passed targeted verification. It does **not** mean any specific production route is fixed unless that route has its own proof packet.

---

## 14. Review gate and agent prompt integration

Production-facing changes must import the canonical review gate from:

```text
docs/production-durability-review-gate.md
```

Required marker:

```text
PRODUCTION_DURABILITY_REVIEW_GATE_OK
```

Agent task prompts that may affect production must include or link:

```text
docs/prompts/production-durability-agent-brief.md
```

Required marker:

```text
PRODUCTION_DURABILITY_AGENT_BRIEF_OK
```

This repo currently has no central GitHub PR template. When a central template is added, it should import the question block from `docs/production-durability-review-gate.md` rather than rewriting a parallel checklist.

---

## 15. `/workspace-tree` immediate case study and first enforcement target

The current `/workspace-tree` failure mode is the first required case study for this standard:

```text
/workspace-tree rendered as a black page because production routing/source/fallback behavior was not durably verified.
```

This standard is broader than `/workspace-tree`; it applies to:

- dashboard routes;
- plugin pages;
- gateway API routes;
- public operator surfaces;
- project-specific production apps like Active Oahu;
- Human Design Engine production apps;
- future Prismatic-managed services.

But `/workspace-tree` is the first enforcement target. A `/workspace-tree` fix may not claim production fixed unless it includes:

- clean production-safe branch/worktree proof;
- local gateway `/health` and route table proof;
- local `/workspace-tree` route proof;
- local `/api/workspace-tree/preview` safe-path proof;
- traversal/encoded traversal/absolute path blocking proof;
- intentional deploy/restart/reload proof;
- public/authenticated route proof;
- screenshot/browser proof showing the page is not black/blank;
- rollback path.

---

## 16. Production worktree durability policy

The Prismatic gateway currently has a named production source risk documented in:

```text
docs/production-worktree-durability-migration-plan.md
```

Required marker:

```text
PRODUCTION_WORKTREE_DURABILITY_PLAN_OK
```

Policy invariant:

```text
live service source != mutable multi-agent development checkout
```

Preferred durable runtime path:

```text
/home/ubuntu/.prismatic/runtime/prismatic-engine
```

Changing systemd runtime source is a production operation and must not be silently folded into unrelated documentation work. If the migration is not implemented in the current slice, the risk must be stated plainly and tracked as a follow-up issue/PR.

