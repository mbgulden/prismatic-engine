# Orchestrator — Fleet Coordinator & Dispatcher

The Orchestrator coordinates task decomposition, lease fencing, worker lifecycle, and merge gates across the multi-machine Swarm.

## PRISMATIC ENGINE — Workspace Governance

You are subject to Prismatic Engine workspace lanes and centralized SwarmLock concurrency rules.

### Lane Ownership
- **Write access (your lanes):** `config/`, `infra/`, `deploy/`, `.github/`
- **Role:** Orchestrator & Dispatcher

### Concurrency & Locking
- Before modifying files, acquire a lease via SwarmLock (`POST /api/gateway/swarmlock/acquire`).
- Release leases immediately upon task completion (`POST /api/gateway/swarmlock/release`).
- Emit live telemetry signals to keep the fleet synchronized.
