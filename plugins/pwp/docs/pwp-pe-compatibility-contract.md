# PWP–Prismatic Engine compatibility contract

**Contract version:** `1.0.0`  
**Scope:** immutable, machine-readable compatibility boundary for an external PWP package and Prismatic Engine (PE). It is not a runtime integration, connection request, package installation, deployment, or PE Core change.

The normative schema is [`../schemas/pwp-pe-compatibility-contract.schema.json`](../schemas/pwp-pe-compatibility-contract.schema.json). A valid reference fixture and a deliberately rejected fixture live under `../tests/fixtures/pwp_pe_compatibility/`.

## Version matrix

| Surface | Required value | Compatibility rule |
| --- | --- | --- |
| Contract | `1.0.0` | Unknown required fields and unrecognized capability IDs fail validation. |
| PWP plugin | `pwp-design-token-plugin`, SemVer version | Must advertise `plugin-manifest.yaml` and `pwp.plugin:PWPDesignTokenPlugin`. |
| PE | `>=0.2.0, <2.0.0` | PE supplies the plugin interfaces named below; a new major needs a new contract review. |
| External discovery | `prismatic.plugins` entry-point group | Manifest is required and discovery is fail-closed. |
| Legacy boundary | `prismatic.pwp_integration` | Documented compatibility adapter only; this contract does not replace or mutate it. |

## Direct imports and discovery

PWP's currently observed PE imports are deliberately explicit:

- `prismatic.interface.plugin` for `PrismaticPlugin` and `PluginContext`.
- `prismatic.capability_router` for capability routing.

The external package must declare the `prismatic.plugins` entry-point group and retain a valid `plugin-manifest.yaml`. A consumer must reject a missing manifest, wrong entry point, unsupported PE range, schema-invalid contract, or unknown capability ID. It must not guess a fallback capability or silently load an unreviewed surface.

## Capability and tool contract

The allowed capability IDs are:

| ID | Kind | Stable intent |
| --- | --- | --- |
| `pwp.theme-system` | `theme` | Theme validation, diff, and compilation. |
| `pwp.credentials` | `credentials` | Redacted provider status and refresh. |
| `pwp.visual-governance` | `governance` | Additive visual and policy checks. |
| `pwp.reference-lifecycle` | `lifecycle` | Credential-free reference lifecycle proof. |

Allowed tool names are `pwp_credentials_refresh`, `pwp_credentials_status`, and `pwp lifecycle demo`. Each tool declares an object-valued input schema; the credential tools require `provider`. Adding or renaming tools/capabilities requires a contract-version review and a new accepted fixture.

## State and disconnect semantics

Only these PE-visible transitions are in contract scope:

1. `disconnected -> connected`
2. `connected -> disconnected`
3. `connected -> connected:refresh`

A disconnect is reversible and must preserve PWP plugin files, generated artifacts, and lifecycle job history. PE Core queues, crons, and dashboards continue without PWP. The reference lifecycle can remain queryable, but PWP readiness/capabilities are no longer active.

## Verification

Run the fail-closed fixture proof from the PE root:

```bash
python3 -m pytest plugins/pwp/tests/test_pe_compatibility_contract.py -q
```

The test accepts the valid contract and rejects an otherwise shaped fixture that introduces `pwp.unreviewed-capability`. This is a compatibility proof only; it makes no claim of standalone wheel acceptance, adapter bootstrap, lifecycle cutover, or production deployment.
