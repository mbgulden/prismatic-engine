# PWP deploy idempotency

PWP pipeline deploys use the run-state store to avoid replaying Cloudflare deploy side effects when the latest successful deployment already matches the current artifact inputs.

## Hash contract

A deploy context may provide any of these fields:

- `commit_hash` — source revision for the deployable artifact.
- `theme_hash` — compiled theme/token artifact hash.
- `content_hash` — rendered content/module/page artifact hash.

When `deploy_target` is set, `PWPPluginRunner` compares the current `commit_hash`, `theme_hash`, and `content_hash` against the latest `PWPRunStateStore` record for the same `client_id` and target.

- If all three hash values match the latest record, the runner returns `deploy_skipped: true` and does not call the deploy artifact provider or fire `on_deploy`.
- If any hash differs, or no prior record exists, the runner deploys normally and records the new hashes.
- If no hashes are supplied, the runner deploys normally. Hash-free contexts are not considered idempotent because the engine cannot prove the output is unchanged.

## State fields

`PWPRunState` preserves the existing rollback fields and now also stores:

- `commit_hash`
- `theme_hash`
- `content_hash`

These fields are optional for backward compatibility with older `pwp_run_state.json` records.

## Operational note

For Cloudflare Pages, this keeps repeated pipeline runs from generating duplicate deployments when source, theme, and content artifacts are unchanged, while still allowing deploys whenever a meaningful theme or content artifact changes without a new source commit.
