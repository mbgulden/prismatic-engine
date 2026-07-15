# Production Durability Review Gate

**Status:** canonical review gate block  
**Import into future GitHub PR templates:** `.github/pull_request_template.md` or `.github/PULL_REQUEST_TEMPLATE/production-facing-change.md` when this repo adopts centralized GitHub templates.  
**Required marker:** `PRODUCTION_DURABILITY_REVIEW_GATE_OK`

This repo currently has no central GitHub pull-request template. Until one is added, reviewers and agents must import this block into any PR, Linear review comment, or handoff that affects a live route/service/dashboard.

`PRODUCTION_DURABILITY_REVIEW_GATE_OK`

---

## Production-facing change question block

Answer these questions before requesting review:

```md
## Production Durability Gate

- [ ] Does this affect a live route/service/dashboard?
  - Affected route/service/dashboard:
  - If no, why not:

- [ ] Where is the production-safe branch/worktree proof?
  - Branch/worktree:
  - Commit SHA:
  - `git status --short --branch`:
  - Production source model/readback:

- [ ] Where is the local gateway/service proof?
  - Local base URL:
  - `/health` result:
  - Route table proof:
  - Local route/API result:

- [ ] Where is the security/path-safety proof?
  - Safe path result:
  - Traversal blocked result:
  - Encoded traversal blocked result:
  - Absolute/private path blocked result:

- [ ] Where is the public/authenticated proof?
  - Public URL:
  - HTTP status:
  - Auth state, or `skipped_auth_required` with exact response:
  - Body/DOM marker:

- [ ] Where is the screenshot/browser proof?
  - Screenshot/browser artifact:
  - Console error readback:
  - Blank-page/404 check:
  - CDN/fallback mode check:

- [ ] What is the rollback path?
  - Previous commit/release:
  - Rollback command or revert PR path:
  - Restart/reload needed:
  - Rollback verification:

- [ ] Verification label:
  - `ad_hoc_targeted` / `canonical_suite_green` / `skipped_auth_required`
```

If any required proof is missing, the PR must say **why** and list the follow-up issue. Do not mark production fixed from code/static checks alone.

---

## Reviewer decision rule

Production-facing PRs are **blocked** unless they include evidence for:

1. clean production-safe branch/worktree;
2. local reproduce or local route/service proof;
3. path-safety/security proof when user input touches paths/files;
4. intentional deploy/restart/reload plan or proof;
5. public/authenticated proof, or explicit `skipped_auth_required` blocker;
6. browser/screenshot proof for user-facing pages;
7. rollback path;
8. explicit ad-hoc vs canonical-suite verification label.

Reviewers must reject vague claims like:

```text
fixed in code
localhost works
route should work after deploy
CI passed so production is fixed
```

Accepted language looks like:

```text
Standard installed ≠ /workspace-tree fixed.
/workspace-tree fixed ≠ all production risks eliminated.
Ad-hoc targeted proof ≠ canonical suite green.
```

---

## `/workspace-tree` first enforcement target

The current `/workspace-tree` incident is the first required case study. The failure mode was:

```text
/workspace-tree rendered as a black page because production routing/source/fallback behavior was not durably verified.
```

Any future `/workspace-tree` fix must include the full gate above, including local gateway proof, path traversal proof, public/authenticated proof, and screenshot/browser proof.

---

## Scope across Prismatic-managed production

This gate applies to:

- Prismatic dashboard routes;
- plugin pages;
- gateway API routes;
- public operator surfaces;
- Active Oahu production apps;
- Human Design Engine production apps;
- future Prismatic-managed services.
