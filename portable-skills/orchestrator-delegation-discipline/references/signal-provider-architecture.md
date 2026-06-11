# Signal Provider — Agent Communication Layer

SignalProvider is the transport layer that delivers work signals from
the dispatcher to agent lanes. It's the "how does Fred tell Kai to
wake up and work on GRO-735?" answer.

## Relationship to Delegation Discipline

The orchestrator-delegation-discipline skill governs *which* lane gets
*which* work. SignalProvider governs *how the signal gets there*.

```
Dispatcher picks lane (delegation discipline)
       │
       ▼
SignalProvider delivers signal (this doc)
       │
       ▼
Agent lane picks up work (hermes/cron/worker loop)
```

## The Problem It Solved

Before SignalProvider, the dispatcher wrote `/tmp/nudge-fred` — a Unix
file touch. This only worked on Linux, only on the same machine, and
had no way to signal remote agents like Kai (which runs on a different
host).

SignalProvider replaces that with an abstract interface so the
dispatcher calls `send()` without knowing the transport.

## Backend Decision Guide

| Deployment | Primary | Fallback | Dead Letter |
|-----------|---------|----------|-------------|
| Single Linux box (current) | `file` | `http` | `telegram` |
| Docker Compose | `http` | `file` | `telegram` |
| Kubernetes cluster | `redis` | `http` | `telegram` |
| Windows/macOS dev | `http` | `file` | none |

## Staging Location

Code staged at `~/work/prismatic-engine-staging/prismatic/providers/signals/`
pending Phase 1 package scaffold:

- `base.py` — abstract SignalProvider + SignalPayload dataclass
- `file.py` — FileSignalProvider (atomic writes + flock locks)
- `http.py` — HTTPSignalProvider (POST webhooks + retry with backoff)
- `redis.py` — RedisSignalProvider (pub/sub + pending key TTL)
- `__init__.py` — factory + FallbackChain

## Config Reference

`~/work/prismatic-engine-staging/config/agents.yaml`:
```yaml
fred:
  signal:
    primary:    {type: file, directory: /tmp/prismatic}
    fallback:   {type: http, endpoints: {fred: "..."}}
    dead_letter: {type: telegram, chat_id: 8190664947}
```

## FallbackChain — Reusable Resilience Pattern

Every SignalProvider can be wrapped in a `FallbackChain` that tries providers
in order until one succeeds. This pattern is NOT signal-specific — it applies
to ANY provider abstraction (TaskProvider, StorageProvider, AuthProvider, etc.)

```python
# config/agents.yaml
fred:
  signal:
    primary:      {type: file}     # Fast, local — try first
    fallback:     {type: http}     # Remote agent — try second
    dead_letter:  {type: telegram} # Human notification — last resort
```

**Implementation** (from `signals/__init__.py`):
```python
class FallbackChain(SignalProvider):
    def __init__(self, providers, dead_letter=None):
        self._providers = providers
        self._dead_letter = dead_letter

    def send(self, target, payload):
        for provider in self._providers:
            if provider.send(target, payload):
                return True
        return self._dead_letter.send(target, payload) if self._dead_letter else False
```

**Dead letter semantics**: The dead_letter is NOT just another fallback. It signals
"all automated paths failed — a human needs to know." In our swarm, dead_letter
is always Telegram → Michael's DM. If every agent lane is unreachable, Michael
gets a message. Silence = everything is working.

**Generalizing beyond signals**: Any swappable backend benefits from the same pattern.
`TaskProviderFallbackChain(primary=linear, fallback=jira, dead_letter=slack_webhook)`.
The provider interface is the contract; the chain is the resilience wrapper.

## Linear Integration

GRO-755 tracks SignalProvider implementation in the Prismatic Engine project.
Created in the same execution batch as the code:

1. Created Linear project: "Prismatic Engine" (747b3ea8)
2. Created issue: GRO-755 — full description with deliverables, signal flow, fallback chain
3. Labeled `agent:fred` so the dispatcher picks it up
4. Updated `project-registry.json` with new `prismatic-engine` venture

## See Also

- `~/work/agentic-swarm-ops/docs/architecture/prismatic-engine-plan.md` — Layer 2.5
- `~/work/project-registry.json` — prismatic-engine venture entry
- GRO-755 — Linear issue tracking implementation
