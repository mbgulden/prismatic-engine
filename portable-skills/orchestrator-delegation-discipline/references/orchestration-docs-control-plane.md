# Orchestration docs control plane

Use this reference when the next valuable step is to turn scattered orchestration guidance into durable docs before adding more behavior.

## What to promote into docs first
- Source-of-truth hierarchy
- Lane responsibilities and handoff boundaries
- Lifecycle / state transitions for orchestration units
- Research-lane output contracts
- Routing and escalation rules
- Session manifest or other machine-readable governance artifacts

## Durable pattern from the docs-first phase
- Compile the existing guidance into a canonical operator handbook before farming out more research.
- Split implicit concerns into separate docs when each has a different consumer:
  - playbook for operators
  - state machine for lifecycle rules
  - research-lane contract for research output shape
- Keep the canonical doc set linked from setup/workflow/README anchors so the control plane is easy to find.
- Treat these docs as one bounded phase and validate them together in CI / PR review.

## Signals that the docs-first move is the right next step
- The workflow is already functioning, but roles and transitions are only implicit.
- Multiple lanes are involved and handoff quality is inconsistent.
- The team keeps asking the same routing or escalation questions.
- A new governance rule is needed, but there is no clear authoritative place for it.

## After the docs are in place
- Move the next bounded phase to enforcement: schema, manifest, validation hooks, or workflow automation.
- Avoid adding more research until the contract is readable and reviewable.
