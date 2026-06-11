# SignalProvider Drop-In Pattern

## When to use

When a script needs SignalProvider but the full `prismatic-engine` pip package
hasn't been scaffolded yet. Instead of requiring `pip install`, drop a single
`signal_provider.py` module alongside the script that imports it.

## Pattern

```
scripts/
├── agent_dispatcher.py       # from signal_provider import create_signal_provider
├── signal_provider.py        # drop-in module (~180 lines, stdlib only)
└── nudge_executor.py         # detection script: reads SignalPayload, deduplicates
```

The drop-in module implements the SAME interface as the eventual pip package:

```python
# Drop-in (today)
from signal_provider import create_signal_provider, SignalPayload, SignalAction

# Pip package (future)
from prismatic.providers.signals import create_signal_provider, SignalPayload, SignalAction
```

Same `create_signal_provider()` factory. Same `SignalPayload` dataclass. Same `send()`/`poll()`/`acknowledge()` interface. The import path changes; nothing else does.

## What the drop-in must include

1. `SignalPayload` dataclass — `target`, `action`, `issue_id`, `title`, `priority`, `metadata`, `signal_id`, `created_at`
2. `SignalAction` enum — `WORK`, `REVIEW`, `NOTIFY`, `STOP`
3. `SignalProvider` ABC — `send()`, `poll()`, `acknowledge()`
4. `FileSignalProvider` — atomic writes to `/tmp/prismatic/nudge-{target}` with `flock()` locking
5. `FallbackChain` — tries providers in order, optional dead-letter
6. `create_signal_provider()` factory — returns the default provider for this host

## Why atomic writes matter

The old pattern (`with open(nudge_path, "w") as f: f.write(...)`) had two bugs:
- Crash mid-write → agent reads partial file → `json.JSONDecodeError`
- Race condition: dispatcher writes while agent reads → corrupted read

The fix: `tempfile.NamedTemporaryFile()` + `os.rename()` — the file appears
fully-formed or not at all. Plus `fcntl.flock()` for concurrent access safety.

## SignalPayload vs old raw-text nudge

```
BEFORE (raw text):                  AFTER (SignalPayload JSON):
/tmp/nudge-fred                     /tmp/prismatic/nudge-fred
├── GRO-755                         ├── signal_id: "uuid-..."
└── Implement SignalProvider        ├── target: "fred"
                                    ├── action: "work"
                                    ├── issue_id: "GRO-755"
                                    ├── title: "Implement SignalProvider"
                                    ├── priority: 3
                                    └── metadata: {...}
```

The old format had no signal_id (no dedup), no priority (no ordering),
no action type (can't distinguish work from review from stop).

## Location

Full multi-provider package staged at:
`~/work/prismatic-engine-staging/prismatic/providers/signals/`

Drop-in live at:
`~/.hermes/profiles/orchestrator/scripts/signal_provider.py`
