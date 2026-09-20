# Prismatic Engine rules for Antigravity/AGY

These rules apply whenever Antigravity operates in a Prismatic Engine workspace.

## Source of truth

- Use `prismatic agy contract` and `docs/contracts/canonical-agy-cli-workflow.md` as the canonical AGY execution contract.
- Treat `docs/provider-playbook-google-antigravity.md` as provider guidance; it cannot weaken admission, containment, evidence, or approval gates.
- Prefer repository files and installed-package resources over machine-global customization state.

## Execution boundaries

- Do not launch governed work through raw `agy` or `agy-bin`. Use the canonical Prismatic render/admission/launch/wait workflow.
- Never invent, reuse, or bypass an admission receipt. A missing or mismatched receipt blocks launch.
- Do not kill a valid run because of elapsed time or quiet filesystem output. Use exact descendant/activity evidence; cancellation is explicit.
- Keep the detailed task in a hash-bound task file. Keep the inline goal bounded and point it to the task, plan, result, and log paths.
- Require a plan before code edits and a durable result packet before completion.

## Safety and authority

- Never include credentials, OAuth state, tokens, private keys, conversation databases, logs, caches, or machine-specific paths in committed customization assets.
- Do not post to Linear/GitHub, approve or merge pull requests, deploy, restart services, make financial/public sends, or increase concurrency unless an explicit governed gate authorizes that exact action.
- Preserve dirty worktrees and unknown artifacts. Ambiguity means preserve and escalate, not delete.
- Reconnect existing good dashboard/product surfaces; do not replace them with rough fallbacks.

## Verification

- Bind claims to the exact commit/tree or immutable artifact digest.
- Separate targeted local checks, canonical suite status, independent review, installed-distribution proof, browser proof, deployment proof, and production proof.
- Put verbose output in log files and report compact evidence with explicit non-claims.
