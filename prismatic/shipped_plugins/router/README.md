# prismatic-router

Router capability plugin for the Prismatic Engine — policy-based ingress
classification: *"request class → capability"*.

An operator configures a rule table mapping request classes onto the
capabilities that should handle them, with advisory policy notes. The
plugin answers the classification question as a pure function of
`(rules, input)` — deterministic, repeatable, zero side effects.

> **The anti-choreography line: it classifies, never dispatches.** There is
> no dispatch, execute, or enqueue path anywhere in this plugin — no
> method, no route, no background thread. Enforcement of a routing decision
> stays with the kernel gate / the harness; the plugin's answer is
> advisory.
>
> **Disabled by default.** `auto_enable: false` in the manifest: the plugin
> registers on attach but starts DISABLED. An operator must explicitly
> enable it (`PluginLoader.enable("prismatic-router")`) before the routes
> are served.

## Attach

Drop (or symlink) this directory on the plugin discovery path as `router/`
(the directory name must match the first segment of `entry_point`:
`router.plugin:RouterPlugin`), then attach:

```
prismatic plugins attach ./plugins/router --config ./router-config.yaml
prismatic plugins enable prismatic-router
```

`swarmrouter>=0.1.0` is declared in `dependencies.pip` and installed at
load time. On enable, the loader validates the config against the
manifest's `config_schema`; `RouterPlugin.on_init()` re-validates
defensively and raises `PluginValidationError` on any violation.

The primitive is imported lazily (`_swarmrouter_or_raise()`), never at
module top level, so the loader scan never crashes on base installs. The
plugin consults the primitive for one advisory annotation only —
`swarmrouter.routing.capability_known(name)` — whether the matched
capability is a known capability in the primitive's taxonomy. The
annotation never changes the classification result; when the primitive is
absent, classification raises a clear `RuntimeError` at use time and the
`/api/router/classify` route returns `{"error": "primitive not installed"}`
instead of a traceback.

## Config syntax

```yaml
# router-config.yaml
default_capability: "prismatic-mesh"   # optional; returned when no rule matches

rules:
  - request_class: "code-review"        # required; matched exactly
    capability: "prismatic-consensus"   # required; the capability that should handle it (advisory)
    policy_notes: "Reviews need quorum before merge."   # optional
    priority: 10                        # optional, default 0; highest wins on ties

  - request_class: "nightly-backup"
    capability: "prismatic-cron"
```

Matching semantics: rules are evaluated highest-`priority`-first (ties
break by config order); the first rule whose `request_class` exactly
matches the input wins. When no rule matches, `default_capability` (when
set) is returned with `decision: "default"`; otherwise `decision:
"no-match"` with `capability: null`.

## Operator API

Plugin-registered routes (declared in `register_api_routes()`, no core edits):

- `GET /api/router/rules` → `{rules: [{request_class, capability, policy_notes, priority}], default_capability}`
- `POST /api/router/classify` with body `{"request_class": "..."}` →
  advisory classification:
  ```json
  {
    "advisory": true,
    "request_class": "code-review",
    "decision": "matched",
    "matched_rule": {"request_class": "code-review", "capability": "prismatic-consensus", "policy_notes": "...", "priority": 10},
    "capability": "prismatic-consensus",
    "policy_notes": "...",
    "capability_known": true,
    "primitive": "swarmrouter"
  }
  ```

`decision` is one of `matched` / `default` / `no-match`. `capability_known`
is the primitive's advisory annotation (`null` when the primitive does not
expose the check). Bad payloads and a missing primitive return an `error`
payload, never a 500 traceback.

## State & lifecycle

- State root: `$PRISMATIC_HOME/plugin-state/prismatic-router/`
  (`Path(os.environ.get("PRISMATIC_HOME") or Path.home())`).
- `on_suspend()` returns the JSON-serializable rule-table snapshot
  (`{plugin, version, saved_at, rules, default_capability}`); the loader
  persists it at the contract path. No secrets are ever in the snapshot.
- `on_resume(state)` restores the rule table. The operator config is
  authoritative: the snapshot's rules are only a fallback when the
  instance holds no rules and `on_init` saw no explicit `rules` key (so an
  intentionally-emptied config stays empty instead of resurrecting old
  rules).
- `register_tools()` returns `[]` — classification is infrastructure, not
  an agent tool.

## Tests

```
PYTHONPATH=~/workspace/prismatic-engine-work/deps python3 -m pytest tests/test_plugin_router.py -q
```

Covers: manifest parses; `config_schema` accepts a valid rule table and
rejects a bad one; `on_init` accepts valid config and raises
`PluginValidationError` on invalid rules; suspend/resume round-trip;
disabled-by-default via a real loader scan; both routes (happy path,
no-match, default fallback, error payloads) with the primitive stubbed via
`sys.modules` injection; missing-primitive degradation; the boundary test
— classify has zero side effects and no dispatch/execute/enqueue path
exists anywhere on the plugin; `register_tools() == []`.
