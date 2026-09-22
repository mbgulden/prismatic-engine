# prismatic-merge

Merge capability plugin for the Prismatic Engine — a **guest-callable
registry** of merge strategies.

Guests (harnesses, agents, operators) invoke a named strategy with their own
inputs; the plugin applies it as a pure function and returns the merged
result. The plugin never auto-merges anything, never holds kernel state,
and is never driven by the kernel.

> **Scope guard** (from `docs/infrastructure-capabilities.md`,
> non-negotiable in review): *"merge stays a guest-callable registry. Merge
> strategies are functions guests invoke; merge is never driven by the kernel
> and never holds kernel state."*

Merging is **infrastructure, not an agent tool**: the plugin registers no
agent tools and exposes the registry only through its own API routes. It
never edits `prismatic/gateway/server.py` or any other core file, and it
never imports kernel transaction/lock/ledger paths.

> **Disabled by default.** `auto_enable: false` in the manifest: the plugin
> registers on attach but starts DISABLED. An operator must explicitly enable
> it (`PluginLoader.enable("prismatic-merge")`) before any strategy can be
> applied.

## Attach

Drop (or symlink) this directory on the plugin discovery path as `merge/`
(the directory name must match the first segment of `entry_point`:
`merge.plugin:MergePlugin`), then attach:

```
prismatic plugins attach ./plugins/merge --config ./merge-config.yaml
prismatic plugins enable prismatic-merge
```

`swarmmerge>=0.1.0` is declared in `dependencies.pip` as an aspirational
PyPI name (the primitive is not published yet). The loader treats it as
metadata; the plugin imports the primitive **lazily** and raises a clear
`RuntimeError` at apply time if it is missing. Loader scans and attaches
never depend on the primitive.

On enable, the loader validates the config against the manifest's
`config_schema`; `MergePlugin.on_init()` re-validates defensively and raises
`PluginValidationError` on any violation.

## Config syntax

```yaml
# merge-config.yaml
strategies:
  - name: last-writer-wins
    description: Later documents overwrite earlier keys on conflict.
    handler: strategies.last_writer_wins   # dotted path INSIDE swarmmerge

  - name: concat-lists
    description: Concatenate input lists in order.
    handler: strategies.concat_lists
```

- `name` — unique lowercase slug (`^[a-z0-9][a-z0-9_-]*$`).
- `description` — shown by `GET /api/merge/strategies`.
- `handler` — dotted path to the strategy function **inside the
  `swarmmerge` package namespace** (e.g. `strategies.last_writer_wins`).
  Resolved lazily at apply time; private/dunder segments are rejected so a
  misconfigured handler can never reach outside the primitive.

Strategy implementations live in the `swarmmerge` primitive (separate
repo); this plugin only holds the registry and invokes what guests ask
for.

## Operator API

Plugin-registered routes (declared in `register_api_routes()`, no core edits):

- `GET /api/merge/strategies` →
  `[{name, description, handler}]`
- `POST /api/merge/apply` with `{"strategy": "<name>", "inputs": {...}}` →
  `{"strategy": "<name>", "result": <merged>, "applied_at": "<utc iso>"}`

Apply is **stateless**: the result is a pure function of the strategy and
inputs. The same inputs always produce the same output. Inputs and results
must be JSON-serializable. Unknown strategies and malformed inputs raise
`MergeRequestError`; a missing `swarmmerge` primitive raises a clear
`RuntimeError` at apply time (never a bare 500 traceback).

Every apply call appends one audit event (`merge_applied` / `merge_failed`)
to `audit.jsonl` — observability only; the audit log never feeds back into
merge behavior.

## State & lifecycle

- State root: `$PRISMATIC_HOME/plugin-state/prismatic-merge/`
  (`Path(os.environ.get("PRISMATIC_HOME") or Path.home())`).
  - `audit.jsonl` — append-only audit events, one per apply call
    (strategy name + sha256 digests of inputs/result, never full payloads).
  - `state.json` — suspend snapshot (`{"strategies": [...]}`), written
    belt-and-braces by `on_suspend()` alongside the loader's own
    persistence.
- `on_suspend()` returns the JSON-serializable registry echo (no inputs,
  no results, no secrets); `on_resume(state)` restores it. Config stays
  authoritative: the snapshot is only a fallback when the new instance got
  no explicit `strategies` config.

## Boundary notes

- No imports from kernel transaction/lock/ledger paths anywhere in the
  plugin (asserted by `test_plugin_merge.py`).
- Nothing is written outside the plugin's own state dir.
- The plugin defines no kernel-driven hooks that merge (no
  `before/after_task_execution`, no `on_issue_dispatch` overrides) — apply
  only ever runs when a guest calls the route.

## Tests

```
cd ~/work/prismatic-engine && .venv/bin/python -m pytest tests/test_plugin_merge.py -q
```

The `swarmmerge` primitive is stubbed via `sys.modules` injection (it is
not installed on the box and not on PyPI); the real primitive is never
imported. Covers: manifest validity + entry point importable; `on_init`
with valid/invalid configs; suspend/resume round-trip; disabled-by-default
via a fresh `PluginLoader` scan; both routes; apply happy path, unknown
strategy, and malformed inputs; missing-primitive degradation; per-call
audit events; statelessness (repeated apply → identical output); the
kernel-state boundary assertions; `register_tools() == []`.
