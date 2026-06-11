# Orchestration governance control plane

Use this reference when the task is about routing work across lanes, not just doing a single implementation.

## Durable pattern
- Maintain a machine-readable session manifest as the control plane for active work.
- Keep source-of-truth roles explicit, e.g. GitHub for PR/CI state, Linear for ordered phases, Hermes for routing and verification.
- Represent routing policy in the manifest: spawn strategy, poll interval, max wait, nudge timing, escalation targets.
- Enforce the manifest/schema/doc contract in CI so the governance layer fails fast when drift appears.

## Multi-lane execution pattern
- Hermes: decomposition, lane assignment, verification, synthesis.
- AGY/Antigravity: broad research, synthesis, uncertainty reduction.
- Jules: bounded implementation and PR-producing changes.
- Codex/local agent: deterministic local debugging, verification, conflict resolution.

## Good orchestration behavior
- Prefer spawning a lane immediately when there is actionable work; do not wait for a human to discover the gap.
- Keep each lane self-contained and named with identity + role + persona.
- Verify claimed side effects independently before reporting success.
- If any open PR is not on track and no lane exists to handle it, treat that as an orchestration gap and add a lane or escalation rule.

## Useful prompts
- "Review the current orchestration state and identify the next highest-leverage lane to spawn."
- "Implement the smallest governance artifact that makes routing/escalation machine-readable and CI-enforced."
- "Return exact files, status, and next step; do not self-verify without an external check."
