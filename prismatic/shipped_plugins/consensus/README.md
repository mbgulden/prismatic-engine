# prismatic-consensus

Quorum capability plugin for the Prismatic Engine — a governance quorum
primitive (e.g. 2-of-3 approvals) that the gate evaluator's escalation path
can **consult**.

It opens quorum questions (subject, required approvals, expiry), records
approvals with caller identity (no self-approval, no duplicate approval),
and reports quorum state (`pending` / `met` / `failed`) with expiry
evaluated at query time. Quorum state persists via `on_suspend` /
`on_resume` under `$PRISMATIC_HOME/plugin-state/prismatic-consensus/`.

Quorum governance is **infrastructure, not an agent tool**: the plugin
registers no agent tools and exposes state only through its own API
routes. It never edits `prismatic/gateway/server.py` or any other core
file.

> **Disabled by default.** `auto_enable: false` in the manifest: the plugin
> registers on attach but starts DISABLED. An operator must explicitly enable
> it (`PluginLoader.enable("prismatic-consensus")`) before any quorum can
> be opened.

## The boundary (non-negotiable)

**The plugin never approves or denies anything itself; it only records
approvals. It never writes gate decisions and never calls into the gate.
The kernel reads the plugin; the plugin never drives the kernel.**

The plugin holds read-only gate-config knowledge: the `quorum_policy`
config maps gate decision names to the approvals they require, so the
plugin knows what needs quorum. It reads this config; it never writes
gate state. A dedicated boundary test greps the plugin source for
gate-write symbols and asserts none are present.

## Attach

Drop (or symlink) this directory on the plugin discovery path as
`consensus/` (the directory name must match the first segment of
`entry_point`: `consensus.plugin:ConsensusPlugin`), then attach:

```
prismatic plugins attach ./plugins/consensus --config ./consensus-config.yaml
prismatic plugins enable prismatic-consensus
```

`swarmconsensus>=0.1.0` is declared in `dependencies.pip` as metadata (it
is not on PyPI yet). The plugin imports it **lazily** at use time via
`swarmconsensus.quorum.evaluate_quorum()`; when the primitive is missing,
route handlers return a structured `primitive_not_installed` response
instead of a traceback, and the loader scan is unaffected. On enable, the
loader validates the config against the manifest's `config_schema`;
`ConsensusPlugin.on_init()` re-validates defensively and raises
`PluginValidationError` on any violation.

## Config syntax

```yaml
# consensus-config.yaml
default_required_approvals: 2   # used when a proposal sets no explicit count
max_required_approvals: 9       # upper bound for any single proposal
default_expiry_sec: 86400       # 1 day, when the request sets no expiry
max_expiry_sec: 2592000         # 30 days, upper bound

# Read-only gate-config knowledge: decision name -> required approvals.
# The plugin reads this to know what needs quorum; it never writes it.
quorum_policy:
  deploy-prod:
    required_approvals: 3
    description: "Production deploys need 3 approvals"
```

## Operator API

Plugin-registered routes (declared in `register_api_routes()`, no core edits):

- `POST /api/consensus/propose` → open a quorum question.
  Body: `{subject, proposer, required_approvals?, decision?, expires_in_sec? | expires_at?, metadata?}`.
  When `decision` names a `quorum_policy` entry, its `required_approvals`
  apply (an explicit count that conflicts is rejected).
- `POST /api/consensus/approve` → record one approval.
  Body: `{id, approver}`. The proposer cannot approve their own proposal;
  each caller identity may approve a proposal at most once; approvals are
  rejected once the proposal is `met` or `failed`.
- `GET /api/consensus/status/{id}` → `{status: pending|met|failed,
  required_approvals, approvals: [{approver, at}], expires_at, ...}`.
  State is computed at query time by the `swarmconsensus` primitive —
  event-driven on read; there is no background sweeper.

## State & lifecycle

- State root: `$PRISMATIC_HOME/plugin-state/prismatic-consensus/`
  (`Path(os.environ.get("PRISMATIC_HOME") or Path.home())`).
  - `state.json` — suspend snapshot (`{"proposals": [...]}`), written
    belt-and-braces by `on_suspend()` alongside the loader's own
    persistence; merged by `on_resume()` without clobbering live state.
- `on_suspend()` returns the JSON-serializable proposal store. Approval
  records carry caller identities and timestamps only — never secrets.
- `on_resume(state)` restores open quorums (malformed records are
  ignored); a resumed quorum can still reach `met` afterwards.

## Tests

```
~/work/prismatic-engine/.venv/bin/python -m pytest tests/test_plugin_consensus.py -q
```

Covers: manifest validity; `config_schema` accept/reject; `on_init`
valid/invalid config; loader scan registers-but-disabled and core never
imports the plugin; propose/approve/status happy path to `met`;
`failed` on expiry (evaluated at query time); self-approval and duplicate
approval rejected; `quorum_policy` decision mapping; suspend/resume
round-trip of open quorums; missing-primitive degradation (clear
`RuntimeError` at use time, structured route error, scan unaffected);
the boundary test (no gate-write symbols in source, interface-only
imports); `register_tools() == []`.
