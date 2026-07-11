# PWP Token Provenance Metadata

GRO-3679 adds deterministic token provenance for later deployment manifest, run-state, and rollback integration.

The compiler records three pieces of evidence:

- `tokenHash` — SHA-256 of merged design-token JSON in canonical form (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).
- `cssHash` — SHA-256 of the compiled CSS custom-property output.
- `sources` — ordered source records for default tokens and tenant overrides, each with a source kind, repo-relative path, and SHA-256 hash.

The provenance record uses schema version `pwp.token-provenance.v1` and deliberately excludes timestamps, absolute workspace paths, hostnames, and other build-host noise. Deployment manifests can compare `tokenHash` and `cssHash` directly for idempotency; rollback records can keep the same object as compact proof of the token inputs used for the artifact.

Example shape:

```json
{
  "schemaVersion": "pwp.token-provenance.v1",
  "tenantId": "client-123",
  "algorithm": "sha256",
  "canonicalFormat": "json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=False)",
  "tokenHash": "<sha256 of merged canonical token JSON>",
  "cssHash": "<sha256 of compiled CSS custom properties>",
  "sections": ["animations", "colors", "radii", "shadows", "spacing", "typography"],
  "sources": [
    { "kind": "default", "path": "templates/tokens.json", "hash": "<sha256>" },
    { "kind": "tenant_override", "path": "tenants/client-123/tokens.json", "hash": "<sha256>" }
  ]
}
```

Use `plugins.pwp.compiler.get_token_provenance_for_tenant(tenant_id)` or `PWPDesignTokenPlugin.get_token_provenance(tenant_id)` when later deployment code needs to attach token metadata to a manifest or run-state record.
