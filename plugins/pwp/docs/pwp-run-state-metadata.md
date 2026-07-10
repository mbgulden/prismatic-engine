# PWP Run-State Theme Metadata

PWP deployment run-state records carry enough theme provenance to answer three operational questions without archaeology:

1. which theme package/version produced the deployed artifact;
2. which token, module, and content inputs were hashed for deploy idempotency; and
3. which compatibility contract the deployment claimed to satisfy.

## Stored fields

`prismatic/core/pwp_state.py` stores these optional fields on each `PWPRunState` record:

| Field | Meaning |
|---|---|
| `theme_id` | Stable theme package identifier, for example `pwp.theme.trust-light`. |
| `theme_version` | Theme package version used for the deploy. |
| `theme_hash` | Hash of the resolved theme package/artifact inputs. |
| `token_hash` | Hash of the resolved design-token set, including tenant overrides. |
| `module_hash` | Hash of the module contract/render inputs. |
| `content_hash` | Hash of the generated content payload. |
| `theme_engine_compatibility` | Compatibility range declared by the theme manifest, e.g. `>=0.2.0`. |
| `theme_schema_version` | Theme schema/contract version used to validate the package. |
| `previous_run_id` | Reversible prior run selected as this deployment's rollback source. |
| `previous_artifact_sha` | Artifact SHA passed to the deploy adapter during rollback. |
| `previous_theme_id` / `previous_theme_version` | Prior theme package identity restored by rollback. |
| `previous_theme_hash` | Prior resolved theme package/artifact hash restored by rollback. |
| `previous_token_hash` | Prior resolved token override set hash restored by rollback. |
| `previous_module_hash` | Prior module contract/render hash restored by rollback. |
| `previous_content_hash` | Prior generated content payload hash restored by rollback. |
| `previous_theme_engine_compatibility` | Compatibility range associated with the prior theme artifact. |
| `previous_theme_schema_version` | Schema/contract version associated with the prior theme artifact. |

All fields are optional so existing state files continue to load. Unknown fields in old or future records are ignored by `PWPRunState.from_dict()`.

## Idempotency behavior

`PWPRunStateStore.should_skip_deploy()` compares every supplied hash field against the latest deployment for the same `client_id` and `target`. Hash-free deploy contexts still deploy: without at least one meaningful hash, the runner cannot prove the output is unchanged.

## Rollback/provenance behavior

Rollback uses the previous reversible artifact SHA as the execution target. When a deployment is recorded, the store now snapshots the reversible prior run's theme identity, version, theme hash, token override hash, module hash, content hash, and compatibility fields into `previous_*` fields on the new record. Non-reversible deployments are deliberately ignored as rollback sources.

`PWPRunState.rollback_restore_metadata()` returns the prior artifact/theme/token/content bundle in adapter-friendly form. `handle_rollback()` passes that bundle as `context["rollback_restore"]` and writes it into the audit log so operators can verify exactly which theme version and token override set were restored. Legacy records without these fields still roll back by `previous_artifact_sha`, but their restored theme metadata is reported as `null` rather than guessed.
