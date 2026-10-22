# ADR-0004: Canonical Prismatic Engine checkout split (runtime vs build)

**Status:** Proposed
**Owner:** Prismatic Engine orchestration maintainers
**Date:** 2026-10-21
**Supersedes:** Unwritten assumption that either local clone was authoritative for both roles

## Context

Two full clones of this repository coexist on the build host, and both were drifting:

- **Runtime tree** — `/home/ubuntu/.prismatic/repos/mbgulden/prismatic-engine`
  (HEAD `ed28c608`, 2026-09-29). The prismatic-gateway runtime is sourced from this
  tree; it is 2 commits behind `origin/main` (#644, #645) and carries two
  **uncommitted local edits** (autonomy v1→v2 spec pointer + `enabled: true` in
  `autonomy_tiers_v2.yaml`).
- **Work tree** — `/home/ubuntu/work/prismatic-engine` (HEAD `dde25dfb`, 2026-09-28).
  Used as the build/dev checkout; 76 commits behind `origin/main`, and its single
  ahead commit (PWP site SEO crawler) is already superseded on main by PR #571.

Builds and PRs that started from the work tree's HEAD were therefore cutting branches
from a stale base, while the runtime tree was running code that had never been
committed. The 2026-10-21 divergence audit
(`/home/ubuntu/work/prismatic-hypervisor/CANONICAL-CHECKOUT.md`) measured the split
in full: 98 content-differing files were pure work-tree staleness (work ≠ main,
live = main), 3 files were not on `origin/main` at all, and the only true local
divergence was the two uncommitted autonomy edits in the live tree.

## Decision

1. **Canonical for runtime (source of record):** the tree at
   `/home/ubuntu/.prismatic/repos/mbgulden/prismatic-engine`. The gateway runtime is
   rebuilt only from this tree or, equivalently, from a release cut of `origin/main`
   under `/home/ubuntu/.prismatic/current`. No runtime process may import source
   from any other checkout.
2. **Canonical for builds/dev (authoring):** the tree at
   `/home/ubuntu/work/prismatic-engine` remains the designated dev checkout — but
   **no build, branch, or PR may start from its HEAD**.
3. **Build/PR start rule:** every build and every PR starts from `origin/main` in a
   **fresh worktree** (`git fetch origin && git worktree add <path> -b <branch> origin/main`).
   Branch naming: `muse/<slug>` for review-lane work, or the assigned agent's branch
   otherwise.
4. **Merge authority:** workers never merge. **Jimmy reviews and merges** per the
   2026-10-21 merge-gate policy (verification stack + Jimmy review make a merge safe;
   no manual merge by Michael).
5. The work tree is **not deleted**; it is retired from build-origin duty. Until it is
   fast-forwarded to `origin/main` (follow-up), anything authored there must be
   re-based onto `origin/main` before use.

## Consequences

- A stale work tree can no longer seed a build or a PR; the accidental path that
  produced the dde25dfb/ed28c608 divergence is closed at the workflow level.
- The two uncommitted autonomy-v2 edits in the live tree are **local divergence, not
  approved code**: they must be promoted through a normal PR (reviewed, committed,
  merged by Jimmy) or explicitly reverted. Running an uncommitted change on the
  gateway is a durability gap until then.
- Untracked local work in the work tree (notably `prismatic/journal_init.py`, 239
  lines, present in no commit) is dispositioned per file — promote via PR or archive —
  not carried forward by inheritance.
- Runtime state (`prismatic_state/`, sockets, dbs) does not belong in either source
  tree per the runtime-services contract; keeping it there is a hygiene follow-up.
- Observed, out of scope for this ADR: the :9000 endpoint is currently held by an
  orphaned user-session process (cwd = runtime tree, ~10 days old) while
  `prismatic-gateway.service` sits in a restart loop (port conflict). Restoring
  single-owner runtime control is a separate task; this ADR declares the
  source-of-record split and does not move any process.

## Verification

- Divergence audit with full command log:
  `/home/ubuntu/work/prismatic-hypervisor/CANONICAL-CHECKOUT.md` (2026-10-21).
- Local verification attestation for this PR (re-checkable, durable — never an
  ephemeral `/tmp` log): `python scripts/public_launch_smoke.py` on a clean detached
  worktree at the branch head → **PUBLIC_LAUNCH_SMOKE_OK** (public docs, dashboard
  markers, 10/10 plugins ready), transcript captured at the durable receipt
  `/home/ubuntu/work/prismatic-receipts/canonical-checkout/canonical-checkout-verify.log`
  (sha256 `0f03b76c5fa6970b7d053acef6d467896801af6ecb865b2d3162196989fc2b62`).
  Not claimed: canonical full-suite green (the `smoke`/`signal`/`extended` CI gates).
- Re-checkable anytime: `git rev-parse HEAD` in both trees,
  `git rev-list --count <tree>..origin/main`, and the audit's per-file hash
  classification against `origin/main`.
