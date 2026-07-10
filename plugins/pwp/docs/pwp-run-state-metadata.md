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

All fields are optional so existing state files continue to load. Unknown fields in old or future records are ignored by `PWPRunState.from_dict()`.

## Idempotency behavior

`PWPRunStateStore.should_skip_deploy()` compares every supplied hash field against the latest deployment for the same `client_id` and `target`. Hash-free deploy contexts still deploy: without at least one meaningful hash, the runner cannot prove the output is unchanged.

## Rollback/provenance behavior

Rollback still uses the previous reversible artifact SHA as the execution target. The theme metadata remains attached to each run-state record so operators can identify the theme version, token set, module set, and compatibility contract that produced both the current and prior artifacts.
