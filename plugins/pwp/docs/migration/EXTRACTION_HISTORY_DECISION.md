# PWP extraction history decision

**Decision:** use a **provenance import**, not filtered Git history, for the standalone `mbgulden/prismatic-web-publisher` bootstrap.

**Status:** fail-closed decision for Phase 2.1. This is a migration record only; it does not create a target repository, remove the monorepo plugin, change Prismatic Engine Core, or perform a production cutover.

## Fixed source boundary

| Field | Value |
| --- | --- |
| Source repository | `mbgulden/prismatic-engine` |
| Source commit | `7ba0716ce1027c0e9cd8741dd548cf733508ecf3` |
| Source extraction path | `plugins/pwp/` |
| Target repository (when created) | `mbgulden/prismatic-web-publisher` (private) |
| Chosen history method | Provenance import |

The future import commit must be made from a clean checkout at the fixed source commit and contain only the approved PWP extraction inventory. Its formal source/tree hashes, UTC timestamp, path manifest, and SHA-256 file manifest belong in `MIGRATION_PROVENANCE.json` (Phase 2.5).

## Evidence considered

At the decision point, the fixed source revision had **41** files below `plugins/pwp/`. Its reachable PWP history contained **13** commits, of which **5** also changed paths outside `plugins/pwp/`. Examples of those mixed commits touched PE gateway/integration code, top-level documentation, scripts, and plugin governance files.

A filename-only historical review found no PWP-path names matching common credential-file patterns such as `.env`, private-key extensions, or `id_rsa`. That is not sufficient evidence to import historical blobs: a filtered-history rewrite would still require complete historical content review, ref/path rewrite validation, and a post-rewrite leak audit before it could establish that no unrelated or sensitive material survived.

## Why filtered history is rejected

Path-filtering could make a visually tidy repository history, but it is not the safest method for this slice:

1. The useful PWP changes are interleaved with PE Core and operational changes in mixed commits. Preserving commit lineage would require history rewriting rather than a simple copy.
2. A path-only filter cannot, by itself, prove that every retained historical blob meets the new repository's credential, licensing, generated-artifact, and boundary rules.
3. The extraction must never carry PE Core, runtime state, credentials, unrelated monorepo material, or mutable developer-worktree assumptions into the standalone project.
4. The task does not require preservation of commit topology, while it explicitly makes safety outrank pretty history.

Therefore this task **fails closed** from filtered history to provenance import. No filtered clone, `filter-repo`, subtree split, cherry-pick, or history rewrite is authorized by this decision.

## Required provenance-import procedure

A later bootstrap slice must:

1. Check out exactly `7ba0716ce1027c0e9cd8741dd548cf733508ecf3` in a clean temporary source checkout.
2. Produce an allowlisted source-file inventory and SHA-256 manifest for `plugins/pwp/`.
3. Run a safe-to-share secret/credential scan and exclude `.env`, credentials, runtime state, generated customer artifacts, caches, build products, and non-PWP paths.
4. Create a new root import commit containing only reviewed, standalone-ready files; do not copy PE Core implementation or stale PR branches wholesale.
5. Record this decision, the source/tree hashes, manifests, scan result, exact candidate head, and explicit non-claims in `MIGRATION_PROVENANCE.json` and `RESULT.md`.
6. Preserve historical PRs #249 and #250 only as migration-inventory source material; neither is to be merged, closed, or wholesale cherry-picked.

## Boundary and non-claims

This decision does **not** claim that the target repository exists, that PWP is independently installable, that any wheel/resource proof has passed, or that Prismatic Engine has been cut over. Those are separate, evidence-gated phases.
