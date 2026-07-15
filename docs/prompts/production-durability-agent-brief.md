# Production Durability Agent Brief

**Paste this into AGY/Fred/Kai/Ned/Jules tasks that may affect production.**  
**Required marker:** `PRODUCTION_DURABILITY_AGENT_BRIEF_OK`

`PRODUCTION_DURABILITY_AGENT_BRIEF_OK`

---

## Brief

If your task affects production, do **not** work from the mutable production checkout.

Use a clean branch or clean production-safe worktree. Record the branch, commit SHA, and `git status --short --branch` before and after the change.

Verify locally first. A production route/service/dashboard is not fixed until the local gateway or local service proves the route/API behavior and any path-safety/security checks pass.

Deploy intentionally. Do not rely on a shared checkout, accidental branch state, auto-reload, or a background service picking up local edits. Record the deploy/restart/reload action and source/commit readback.

Attach browser or screenshot proof for user-facing pages. Code checks, static inspection, and CI are not enough to claim a public page is fixed.

Do not claim production fixed from code/static checks alone. Use precise labels:

```text
ad_hoc_targeted ≠ canonical suite green
standard installed ≠ route fixed
route fixed ≠ all production risks eliminated
```

For `/workspace-tree`, remember the case study: it rendered as a black page because production routing/source/fallback behavior was not durably verified. Any fix must include local gateway proof, path traversal proof, public/authenticated proof, and screenshot/browser proof.

---

## Required closeout lines

```text
Production-safe branch/worktree proof:
Local gateway/service proof:
Path-safety/security proof:
Intentional deploy/restart/reload proof:
Public/authenticated proof:
Browser/screenshot proof:
Rollback path:
Verification label:
Remaining blockers:
```
