# Infrastructure Capabilities

How Prismatic Engine decides what belongs in the kernel, what belongs in
a capability plugin, and what belongs in a harness. One page; read it
before proposing a new primitive or reviewing a plugin PR.

## The boundary rule: dumb pipes, smart policy

**The kernel moves data and enforces policy. It never decides what an
agent should do next.**

- The engine kernel owns the transaction path: lock (`swarmlock`) → saga
  (`swarmsaga`) → ledger → policy gate (`swarmgate`). It records what
  happened, enforces budgets and approvals, meters spend, and injects
  secrets at the boundary. It holds no conversation state, makes no
  tool-call decisions, and choreographs nothing.
- A **capability plugin** provides infrastructure a harness can call:
  addressing and presence (mesh), policy-based ingress routing (router),
  a registry of merge strategies guests can invoke (merge), governance
  quorums such as 2-of-3 approvals (consensus), and *advisory* capability
  recommendations (curator). Anything the plugin does must stay on the
  "dumb pipes, smart policy" side: it provides the pipe and the policy
  hooks, never the agent's brain.
- A **harness** owns everything that smells like an agent's brain: which
  tool to call, what the next step is, conversation memory, prompt
  construction, LLM calls. Harnesses live outside the engine — LangGraph,
  CrewAI, raw SDK calls — and talk to the engine through its API and
  plugin routes. The engine governs them; it does not become them.

The sentence that settles arguments: *if the code decides what an agent
does next, it is a harness concern and does not belong in the engine or
in a plugin.*

## The capability-plugin contract

Every capability plugin is a `PrismaticPlugin`
(`prismatic/interface/plugin.py`) discovered by `PluginLoader`
(`prismatic/core/registry.py`). It must satisfy the lifecycle contract:

1. **Manifest** (`plugin-manifest.yaml`, schema 1.0.0): `name`, `version`,
   `entry_point`, `core_version_constraint`, `dependencies.pip`
   (capability plugins depend on their PyPI primitive, e.g.
   `swarmcron>=0.3.0` — never a git URL), `tags` including
   `"infrastructure"` and `"optional"`, and an optional `config_schema`
   (JSON Schema) that the loader validates at attach time.
2. **Disabled by default**: `auto_enable: false`. The operator enables a
   capability explicitly; core never imports a capability plugin.
3. **Lifecycle hooks**: `on_init()` to register tools/routes/MCP servers;
   `on_suspend() -> dict` to return JSON-serializable state (cursors,
   counters — never secrets); `on_resume(state)` to restore it.
4. **State location**: suspend state persists at
   `$PRISMATIC_HOME/plugin-state/<name>/state.json` as
   `{"version": 1, "saved_at": <utc iso>, "state": <dict>}`. The loader
   writes it; the plugin only supplies the dict.
5. **Runtime control**: `PluginLoader.enable(name)` resumes with preserved
   state; `disable(name)` suspends, persists, and stops hook fan-out;
   `unload(name)` additionally drops the instance and removes its
   registered routes, tools, MCP servers, and artifact types. Suspend and
   resume run in try/except isolation — a crashing plugin can never break
   the loader.
6. **No agent tools unless the pipe needs them**: infrastructure plugins
   expose status through their own registered API routes and return `[]`
   from `register_tools()` unless a tool is genuinely part of the pipe
   (e.g. a mesh peer lookup).

Reference implementation: `prismatic-cron`
(`prismatic/shipped_plugins/cron/`). It fires jobs on 5-field cron
schedules, keeps run receipts under
`$PRISMATIC_HOME/plugin-state/prismatic-cron/runs.jsonl`, and its
`prompt` handler only records a *prompt-dispatched* receipt — the harness
picks the prompt up. The plugin never calls an LLM. That restraint is the
whole contract in miniature.

## Classifier: capability plugin vs. harness

Ask the three questions in order. The first "yes" wins.

1. **Does it decide what an agent does next** — pick tools, sequence
   steps, hold conversation state, call an LLM? → **Harness.**
   It does not belong in the engine or in a plugin.
2. **Must every engine install have it for the transaction path to
   work?** (Would the ledger, locking, or policy gate break without it?)
   → **Core.** It ships in base dependencies and the kernel may import it.
3. **Otherwise** — it is infrastructure some workloads want and others
   don't → **Capability plugin.** Disabled by default, operator-enabled,
   state preserved across disable/enable.

Worked examples:

- **prismatic-cron (the cron plugin)** → capability plugin. Scheduling is
  infrastructure: it fires timers and records receipts. Its `prompt`
  handler stops at the boundary — it records that a prompt is due and the
  harness decides what to do with it. It registers no agent tools.
- **swarmlock** → core. The hypervisor's transaction path acquires
  hierarchy locks on every transaction; a base install without it cannot
  import `prismatic.core`. That is the test: base-install breakage.
- **A hypothetical "agent team planner"** that assigns tasks to agents,
  sequences their tool calls, and remembers what each agent said →
  **harness**. It decides what agents do next, so it lives outside the
  engine. If it wants governance, it calls the engine's API from the
  outside — the engine never absorbs it.
- **A "capability curator"** that suggests which registered capability
  fits a request → capability plugin **only if it stays advisory**: it may
  return recommendations (`/api/curator/suggest`) and must never
  auto-attach, auto-select, or rewire a workload. The moment it acts on
  its own recommendations, it has crossed into harness territory.

## Scope guards (non-negotiable in review)

- **Curator stays advisory-only.** Recommendations, never auto-attach.
  No code path may select or attach a capability without an explicit
  operator or harness decision.
- **Merge stays a guest-callable registry.** Merge strategies are
  functions guests invoke; merge is never driven by the kernel and never
  holds kernel state.
- **`SubprocessJail` is a policy-enforced runner, not a security
  boundary.** It applies resource limits, timeouts, network policy, and
  env scrubbing to a subprocess. It is not a sandbox in the security
  sense and must never be described as one.
- **Trust tiers are stated plainly.** `untrusted` means namespace or
  container jail, no network, scrubbed environment. `trusted` means
  wrapped-but-unjailed: still ledger-, meter-, and vault-wrapped, with
  no isolation claims. Never call the trusted tier "sandboxed."
- **Workloads that cannot be jailed run as `trusted`.** GPU passthrough
  and local model servers (including Hermes/vLLM setups) cannot run
  inside a jail; they are metered at the HTTP boundary instead. The tier
  label must always say what is actually true.

## Further reading

- `docs/plugin-lifecycle.md` — the generic lifecycle manager in detail.
- `docs/single-node-boundaries.md` — what the engine deliberately does
  not do, and what would have to change to lift each boundary.
- `docs/sandbox-hardening.md` — jail internals and hardening notes.
- `prismatic/interface/plugin.py` — the `PrismaticPlugin` ABC.
