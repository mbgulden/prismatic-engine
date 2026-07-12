# Capability registry and capacity-aware router

GRO-3551 adds a harness-agnostic routing layer at
`prismatic/capability_router.py`.  The dispatcher can now ask for a worker by
capability and live capacity instead of baking task ownership into agent names.

## Concepts

- **`TaskRequest`**: normalized work item with title, description, labels,
  required capabilities, GPU requirement, and priority.
- **`AgentCapability`**: worker lane metadata: Linear label, capability set,
  concurrency limit, current load, GPU support, availability, and stable
  tie-break priority.
- **`CapabilityRegistry`**: mutable registry of worker lanes.  Runtime callers
  can overlay live load snapshots with `with_loads()` or reserve capacity after
  selecting a route.
- **`CapacityAwareRouter`**: filters ineligible workers, then chooses the
  eligible worker with the lowest load ratio and most remaining capacity.

## Capability matching

Routing accepts explicit `capability:<name>` labels.  If no explicit capability
labels are present, the router infers coarse capabilities from title,
description, and labels:

- `code`: build, fix, implement, refactor, tests, router/registry work
- `docs`: docs, documentation, readme, specs
- `review`: review, audit, validate, second witness
- `content`: copy, blog, article, landing-page content
- `deploy`: deploy, Cloudflare, Workers, DNS, SSL
- `gpu`: GPU, CUDA, Ollama, VRAM, local-model serving

A task requiring GPU is eligible only for `gpu_capable` workers.

## Dispatcher integration

The legacy `AGENT_CONFIG` remains the source of launch configuration.  The
registry can be constructed from that mapping, with per-agent overrides for
`capabilities`, `max_concurrent`, `gpu_capable`, `available`, and `priority`.
This keeps launch mechanics separate from routing policy.

```python
from prismatic.capability_router import default_capability_registry, route_issue
from prismatic.dispatcher import AGENT_CONFIG

registry = default_capability_registry(AGENT_CONFIG).with_loads({"agy": 2, "ned": 0})
decision = route_issue(issue, registry)
if decision.selected:
    print(decision.selected.label)
```

## Operational guardrails

- No Hermes dependency: this is pure Prismatic Engine code.
- Capacity is advisory in the registry; the dispatcher is still responsible for
  deduplication and actual agent launch.
- If no eligible worker exists, callers get a `RouteDecision` with
  `selected=None` and a reason string.  Headless dispatchers should log/comment
  rather than falling back to a hardcoded agent.
