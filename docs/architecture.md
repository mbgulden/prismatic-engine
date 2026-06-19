# Prismatic Engine — Architecture Reference

**Status:** Tier 4 deliverable. Captures the engine as of 2026-06-19.
**Audience:** Anyone touching `prismatic/`, integrating a new agent, or debugging cross-module behavior.

This doc links out to source files rather than restating them. The engine is the durable source of truth; this is the map of it.

## What is the Prismatic Engine?

A harness-agnostic Python library for orchestrating multi-agent work against Linear. Replaces ad-hoc cron + shell glue with:

- A **state machine** for orchestration modes (`mode_switch.py`).
- A **dispatcher** that pulls issues from Linear, decides which agent handles them, and applies lane transitions (`dispatcher.py`).
- A **provider abstraction** so adding GitHub/Jules/Telegram/etc. is one new file under `prismatic/providers/` and one capability entry.
- **Rate-limit and budget enforcement** so a runaway cron cannot exhaust Linear API quota (`linear_budget.py`).

The engine is not a replacement for Hermes; it is a library that **Hermes profiles call into**. The profile handles auth, model choice, and chat-side coordination; the engine handles Linear state, agent lane transitions, and cross-cutting concerns.

## Module Map

Top-level modules in `prismatic/`:

| Module | Role | Notes |
|---|---|---|
| `__init__.py` | Package entry point | Re-exports core types |
| `admin.py` | Administrative CLI helpers | Workspace setup, config dump |
| `agy_live_parser.py` | Parse live AGY CLI output | Brain-dir transcript → structured events |
| `capabilities/` | Capability registry (engine contract) | `Capability` dataclass; pre-registers `linear`, `vcs.github`, `agy`, `jules`, `telegram`, `schedule`, `artifact` |
| `cli/` | Subcommand CLIs | `prismatic-engine doctor`, `prismatic-linear-budget status` |
| `config/` | Config loaders | Reads `~/.prismatic/config.yaml` and env vars |
| `core/` | Engine primitives | State transitions, run records, lock helpers |
| `credit_policy_engine.py` | Decision: allow / deny an agent launch | Used by dispatcher gate |
| `credit_tracker.py` | Persistent credit-state store | Local SQLite |
| `dedup.py` | Event-level dedup helper | TTL-based; used by dispatcher |
| `dispatcher.py` | **The orchestration event loop** | See "Dispatcher" below |
| `doctor.py` | Health-check + capability report | Always-on sanity check; see "Doctor" below |
| `gateway/` | FastAPI app, event bus, IPC bridge | Optional SSE feed + WebSocket broadcaster |
| `journal.py` | Hermes journal hookup | Surfaces engine events to journaling |
| `linear_budget.py` | **Linear API rate-limit codification** (GRO-2008/2011/2020) | `LinearBudget.check_and_consume()` API |
| `linear/budget.py` | Legacy shim re-exporting `linear_budget` | Keep until cron scripts migrate |
| `local_tasks.py` | Local-only task runner | For non-cloud work |
| `lock.py` | Process-level lock helpers | Used by `launch_*` to prevent duplicate launches |
| `mode_switch.py` | Orchestration mode state machine | `OrchestrationMode` + transitions |
| `providers/` | External system adapters | `signals.py`, `tasks.py`, `github.py` |
| `router.py` | Pipeline templates + agent selection | `score_agent`, `select_agent` |
| `run_records.py` | Per-dispatch-run audit log | Linear-issue-id keyed |
| `schedules.py` | Schedule Observatory | Cron + systemd + AGY/Jules (mocked) |
| `skills.py` | Skill marketplace | Read-only discovery |
| `telemetry.py` | Telemetry collector | Records events to `prismatic_state/` |
| `vertex_telemetry.py` | Vertex-AI specific telemetry | Optional |
| `workspace.py` | Workspace registry | Maps project to repo path |

## Public API Surface

### CLI subcommands

```text
prismatic-engine serve                # start dispatcher event loop
prismatic-engine serve --once         # single cycle, then exit
prismatic-engine init                 # initialize config files
prismatic-engine doctor               # capability + repo health check
prismatic-engine doctor --provider github|linear
prismatic-engine billing-report       # cost attribution report
prismatic-engine skills               # skill marketplace commands

prismatic-linear-budget status        # show current rate-limit state
prismatic-research run --prompt X     # ad-hoc research (DRY_RUN only)

prismatic-doctor                      # alias for `prismatic-engine doctor`
prismatic-admin                       # alias for admin commands
```

### Env vars

| Var | Purpose | Default |
|---|---|---|
| `LINEAR_API_KEY` | Linear GraphQL auth | required |
| `PRISMATIC_TEAM_ID` | Linear team UUID | required |
| `GITHUB_TOKEN` / `GH_TOKEN` / `PRISMATIC_GITHUB_TOKEN` | GitHub VCS | required for `vcs.github` |
| `AGY_TOKEN` | AGY CLI auth | required for `agy` capability |
| `JULES_TOKEN` | Jules API auth | optional (deep-link fallback) |
| `TELEGRAM_BOT_TOKEN` / `PRISMATIC_TELEGRAM_BOT_TOKEN` | Telegram chat relay | optional |
| `PRISMATIC_HOME` | Engine install root | `~/.prismatic` |
| `PRISMATIC_STATE_DIR` | Where state DBs live | `${PRISMATIC_HOME}/prismatic_state` |
| `PRISMATIC_CRON_JOBS` | Path to cron `jobs.json` | profile default |
| `PRISMATIC_VENV` | Engine virtualenv | `${HOME}/.prismatic/venv_stable` |
| `PRISMATIC_STATE_DIR` | Shared state DB | orchestrator path |

### Capability names

Pre-registered: `linear`, `vcs.github`, `agy`, `jules`, `telegram`, `schedule`, `artifact`. See `prismatic/capabilities/registry.py`.

## Two-Dispatcher Model

The orchestrator profile and the engine repo **both run a dispatcher today**. They are not duplicates; they cover different scopes.

### Profile dispatcher (`~/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py`)

- **Authoritative for the review loop.** Holds the GRO-2024 bypass-detection that ensures no agent reaches `agent:done` without Fred verification.
- Wired to Hermes cron `e2f1a3b4c5d6` (every 5 min).
- Routes via `next_label` chain in `AGENT_CONFIG`.
- Hermes-specific event emission (SSE feed).

### Engine dispatcher (`prismatic/dispatcher.py`)

- Canonical home for new dispatch logic. Pipeline-templated, telemetry-rich.
- Currently invoked manually via `bin/prismatic-dispatcher-wrapper.sh` (Tier 2).
- Future: cron-shared shadow mode for parity comparison.

**Single source of truth for the loop:** the orchestrator profile, until the engine dispatcher ports bypass detection.

See `docs/profile-vs-engine-dispatch.md` for the full coexistence doc.

## Review Loop Codification (GRO-2024)

```text
Worker (Ned, Kai, Jules, Codex, Autobot, AGY sub-lanes)
   ↓
AGY peer review (different agent, read-only)
   ↓
Fred verification (orchestrator confirms review artifact + walkthrough)
   ↓
agent:done → Done state
```

Only `agent:fred` may move to `agent:done`. The dispatcher enforces this at every transition point. See `skills/orchestration/orchestrator-delegation-discipline/references/review-loop-canonical-codification.md` (canonical codification doc).

**Enforcement code:** `agent_dispatcher.py` lines ~2134-2151.

## Linear API Rate-Limit Codification

`prismatic/linear_budget.py::LinearBudget`:

```python
budget = LinearBudget(limit_per_hour=2500)
if budget.check_and_consume("cron.agent_dispatcher"):
    response = linear_call(...)
```

Persistence: `prismatic_state/linear_budget.db` (SQLite).

**Lint script:** `scripts/check_linear_cron_rate.sh` — fails CI if total expected cron usage > 2000 req/hour.

**Doctor integration:** `prismatic doctor` always prints `[Linear] Rate limit` section.

## AGY Integration

AGY CLI cannot write artifacts to `/tmp/` (sandbox restriction). The wrapper at `bin/launch_agy_with_artifact.py` handles this:

1. Launches AGY inside `tmux` (the only reliable transport; non-PTY `--print` stalls).
2. AGY saves `result.md` to `~/.gemini/antigravity-cli/brain/<conversation-id>/`.
3. Wrapper copies the file to `/tmp/agy-dispatch-GRO-XXXX-result.md` after AGY exits.
4. Returns 0 only if AGY produced a non-empty result.md.

**Model routing** (canonical):

| Label | Model |
|---|---|
| `agent:agy` | `gemini-3.5-flash-high` |
| `agent:agy-pro` | `gemini-3.1-pro-high` |
| `agent:agy-lite` | `gemini-3.1-flash-lite` |

Synced in `agent_dispatcher.py::LABEL_TO_MODEL`, `agent_dispatcher.py::AGENT_CONFIG`, `agy_sandbox_event_supervisor.py::DEFAULT_MODEL`, and native AGY config.

## Tier 1/2/3/4 Outcomes

| Tier | Outcome | Linear |
|---|---|---|
| 1a | Ned sub-labels wired | Tier 1 |
| 1b | Deterministic dedup-eviction helper (`unmark_issue_dispatched`) | Tier 1 |
| 1c | Linear API rate-limit codification | GRO-2008/2010/2020 |
| 2 | Dispatcher wrapper + coexistence docs | GRO-2030 (PR #27) |
| 3 | In-flight inventory refreshed | GRO-2031 (this doc) |
| 4 | Architecture reference | GRO-2032 (this doc) |
| 1c enforcement | Review loop codification + bypass detection | GRO-2024 |

## Adding a New Agent

1. Add a label in Linear: `agent:foo`.
2. Add a row to `prismatic/capabilities/registry.py::pre_register` if it needs an env-var check.
3. Add to `AGENT_CONFIG` in `agent_dispatcher.py` (profile) with `mode: signal` or `mode: print`, `next_label: agent:fred` (for peer-review chains) or `next_label: agent:agy` (for worker chains).
4. If it has a CLI: wire `AGENT_LAUNCHERS["agent:foo"] = launcher_fn`.
5. Add a label-id entry in the orchestrator profile's `KNOWN_LABEL_IDS` cache.
6. Update `agent-label-catalog.md` and `skills/orchestration/orchestrator-delegation-discipline/references/quality-loop-enforcement.md`.

## Adding a New Capability

1. Create `prismatic/capabilities/<name>.py` exposing a `<Name>Capability` class.
2. Implement `has_credentials(self) -> bool`, `verify(self) -> bool`, `emit_event(self, event_type, payload) -> None`.
3. Register in `prismatic/capabilities/__init__.py` and `pre_register()`.
4. Verify with `prismatic doctor --provider <name>`.

## Adding a New Provider

1. Create `prismatic/providers/<name>.py` with a class implementing the provider protocol.
2. Wire into `prismatic/providers/__init__.py`.
3. Add capability entry if it represents a new external system.

## Cross-Reference Index

| Doc | Path |
|---|---|
| Review loop canonical codification | `~/.hermes/profiles/orchestrator/skills/orchestration/orchestrator-delegation-discipline/references/review-loop-canonical-codification.md` |
| Tier 1/2/3 status | `~/.hermes/profiles/orchestrator/skills/agent-orchestration/prismatic-engine-operations/references/in-flight-engine-inventory.md` |
| Profile ↔ engine dispatcher | `docs/profile-vs-engine-dispatch.md` (this repo) |
| Prismatic Engine operations | `~/.hermes/profiles/orchestrator/skills/agent-orchestration/prismatic-engine-operations/SKILL.md` |
| Subagent-driven development | `~/.hermes/profiles/orchestrator/skills/software-development/subagent-driven-development/SKILL.md` |
| Validation pipeline (provider-agnostic) | `~/.hermes/profiles/orchestrator/skills/agent-orchestration/prismatic-validation-pipeline/SKILL.md` |

## Known Gaps

- `prismatic/chat/` package is a capability check; no real AGY chat adapter.
- `get_agy_schedules()` and `get_jules_schedules()` are mocked until AGY/Jules expose real `/schedule` endpoints.
- Engine dispatcher bypass-detection is not yet ported from profile dispatcher.

## Change Protocol

This doc follows the additive workflow:

1. Update doc in the same PR as the engine change.
2. Re-run `find prismatic tests -maxdepth 3 -type f -name '*.py' | grep -v __pycache__` and update the module map.
3. Run `prismatic doctor` before pushing.
4. Open PR; AGY peer review before merge.

Last updated 2026-06-19 by Fred, post-Tier 1/2/3 work (GRO-2032).