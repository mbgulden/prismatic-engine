# Dashboard source modularization (Slice A)

The canonical gateway dashboard is still served from:

- `prismatic/gateway/templates/dashboard.html`

That file is now a generated compatibility artifact. Runtime must keep serving the generated template; it must not depend on an unbuilt mutable source tree.

Edit workflow:

1. Edit the deterministic fragments under `prismatic/gateway/dashboard_src/`.
2. Keep fragment order in `prismatic/gateway/dashboard_src/manifest.json` explicit and lossless.
3. Run `python3 scripts/build_dashboard.py`.
4. Run `python3 scripts/build_dashboard.py --check` before committing.

CI/freshness gate:

- `--check` exits nonzero when `dashboard.html` differs from the exact byte concatenation of the manifest fragments.
- The builder does not add implicit whitespace or newlines.
- Lossless fragments preserve legacy canonical whitespace exactly. `.gitattributes` disables trailing-space/blank-at-EOF diagnostics only under `dashboard_src`; normal whitespace checks remain active everywhere else.
- The builder fails closed for missing, duplicate, absolute, traversal, or out-of-root fragment paths.

Slice A boundaries:

- No visual redesign.
- No behavior refactor.
- No route, adapter, CSS semantics, JavaScript behavior, or tab label changes.
- Do not rename Resources back to Quotas in this slice.
- JavaScript may remain one behavior-preserving source fragment until a later refactor slice.
