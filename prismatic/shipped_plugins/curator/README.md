# prismatic-curator

Advisory-only capability recommendation plugin for the Prismatic Engine —
a consumer of the generic plugin lifecycle (`on_suspend` / `on_resume`).

It answers one question: *"which registered capability fits this request?"*
`GET /api/curator/suggest?q=...` returns ranked capability suggestions with
reasons, read from a read-only snapshot of the loader's registered
capability contracts (`GET /api/curator/catalog` shows the snapshot).
Catalog in → ranked suggestions out.

Recommendations are **infrastructure, not decisions**: the plugin registers
no agent tools and exposes no write routes. It never auto-attaches,
auto-selects, auto-enables, or rewires a capability — acting on its own
recommendations would cross into harness territory, so there is deliberately
no code path that can do it. The route table is GET-only by design.

> **Disabled by default.** `auto_enable: false` in the manifest: the plugin
> registers on attach but starts DISABLED. An operator must explicitly enable
> it (`PluginLoader.enable("prismatic-curator")`) before it serves routes.

## Attach

Drop (or symlink) this directory on the plugin discovery path as `curator/`
(the directory name must match the first segment of `entry_point`:
`curator.plugin:CuratorPlugin`), then attach:

```
prismatic plugins attach ./plugins/curator --config ./curator-config.yaml
prismatic plugins enable prismatic-curator
```

`swarmcurator>=0.1.0` is declared in `dependencies.pip`. The import is lazy
(`_swarmcurator_or_raise()` — never at module top level, so loader scans of
base installs never crash). On enable, the loader validates the config
against the manifest's `config_schema`; `CuratorPlugin.on_init()`
re-validates defensively and raises `PluginValidationError` on any
violation.

## Config syntax

```yaml
# curator-config.yaml
max_suggestions: 5   # optional; integer 1..25, default 5
```

## How the catalog gets in

The plugin keeps a local snapshot of the loader's registered capability
contracts (`PluginLoader.registered_capability_contracts`, collected from
each plugin's `capability_contract()` at attach time). The harness refreshes
it before serving routes:

```python
curator.refresh_catalog(loader.registered_capability_contracts)
```

`refresh_catalog()` deep-copies the contracts — the loader's dicts are never
mutated, and the plugin never calls back into the loader. Suggestions are
stateless per call, so the snapshot is not persisted; `on_suspend()` keeps
only a minimal config echo (no secrets).

## Operator API

Plugin-registered routes (declared in `register_api_routes()`, no core
edits — GET only):

- `GET /api/curator/suggest?q=<query>[&limit=<n>]` →
  `{query, suggestions: [{capability, score, reasons, fingerprint, advisory}],
   catalog_size, advisory_only}`
- `GET /api/curator/catalog` → `{capabilities: [{name, contract}], count,
  advisory_only}`

Scoring is a transparent term-overlap scorer: each query token contributes
at most once, at the highest-weight field it matches (name ×3, description
×2, tags/keywords ×1). Order is deterministic: score desc, then name asc.
Each suggestion carries a stable `fingerprint`
(`swarmcurator.models.compute_fingerprint`) so callers can cache or dedupe
suggestion sets, and every response is marked `advisory_only: true` /
`advisory: true` so no consumer mistakes a recommendation for a decision.

Empty or blank `q` returns `{query, suggestions: [], catalog_size, ...}` —
no error, no guessing.

## State & lifecycle

- Suggestions are stateless; there is no per-suggestion state to persist.
- `on_suspend()` returns a JSON-serializable dict
  (`{plugin, version, saved_at, config: {max_suggestions}, catalog_names,
  advisory_only}`) — config echo only, no secrets, no catalog payload.
- `on_resume(state)` restores `max_suggestions` from the echo when valid.

## The scope guard

`docs/infrastructure-capabilities.md` is normative: *"A 'capability curator'
that suggests which registered capability fits a request → capability
plugin **only if it stays advisory** … The moment it acts on its own
recommendations, it has crossed into harness territory."*

Enforced in code and in tests (`tests/test_plugin_curator.py`):

1. The route table is GET-only — no write route exists to act through.
2. No method references enable/attach/mutate semantics (source scan in the
   boundary test).
3. Calling `suggest()` twice leaves all loader/plugin state untouched.

## Tests

```
~/work/prismatic-engine/.venv/bin/python -m pytest tests/test_plugin_curator.py -q
```

Covers: manifest parses (`auto_enable: false`, tags, entry point, pip dep);
`config_schema` accepts/rejects configs; `on_init` valid/invalid
(`PluginValidationError`); suspend/resume round-trip (JSON-serializable, no
secrets); disabled-by-default via a real loader scan; routes registered and
GET-only; suggest happy path (ranked, reasons, fingerprints) and empty
query; missing-primitive degradation (clear `RuntimeError`, scan unaffected);
the boundary test (GET-only route table, no mutate semantics in source,
suggest leaves state untouched); `register_tools() == []`.
