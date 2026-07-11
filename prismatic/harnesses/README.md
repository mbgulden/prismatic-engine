# Agent Harness Registry

Prismatic Engine exposes agent runtimes through a small `AgentHarness` base class and a JSON registry.

- `prismatic.harnesses.base.AgentHarness` defines the common dispatch, status, cancel, logs, cost, and health contract.
- `prismatic/harnesses/registry.json` lists available adapters and whether they are enabled.
- `GET /api/harnesses` returns the registry entries for dashboard and dispatcher discovery.

The registry is engine-owned and harness-agnostic: concrete adapters live behind module paths such as `prismatic.harnesses.agy_cli` or `prismatic.harnesses.hermes`, but callers should discover enabled harnesses through the gateway route rather than hard-coding module names.
