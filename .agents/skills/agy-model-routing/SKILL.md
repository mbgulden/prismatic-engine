---
name: agy-model-routing
description: Definitive AGY model routing — Linear labels → CLI strings → upstream engines. Use when dispatching AGY tasks with model-specific labels.
version: 1.0.0
---

# AGY Model Routing — Definitive Reference

Source: Gemini via Michael (Jun 14, 2026). The `agy models` CLI lists DISPLAY NAMES, not CLI strings. Use these exact strings with `--model`/`-m`.

## Label → CLI String → Upstream Engine

| Linear Label | `--model` Flag | Upstream Engine |
|---|---|---|
| `agent:agy` | `gemini-3.5-flash-high` | Default AGY Flash High lane (current dispatcher source of truth) |
| `agent:agy-lite` | `gemini-3.1-flash-lite` | Lower-cost/lite fallback lane |
| `agent:agy-pro` | `gemini-3.1-pro-high` | Pro/deep-context lane |

## Removed Labels (Jun 16, 2026)
The following labels were removed when Michael dropped Claude and gemini-3.5 models:
- `agent:agy-flash-high` — removed
- `agent:agy-sonnet` — removed (was Claude Sonnet)
- `agent:agy-thinking` — removed (was Claude Opus)

## CLI Usage
```bash
# Default (routine tasks, workers)
agy --model gemini-3.5-flash-high --print "..."

# Fast/cheap
agy -m gemini-3.1-flash-lite -p "..."

# Deep context / plan writing / code analysis
agy -m gemini-3.1-pro-high -p "Write implementation plan..."
```

## Config: ~/.antigravity/config.json

This file MUST exist for the dispatcher's model routing to work.  If missing,
``_load_agy_model_config()`` returns an empty dict and no ``--model`` flag is
injected (AGY uses its own default).

```json
{
  "model_bindings": {
    "agent:agy": "gemini-3.5-flash-high",
    "agent:agy-lite": "gemini-3.1-flash-lite",
    "agent:agy-pro": "gemini-3.1-pro-high",
    "agent:antigravity-cli": "gemini-3.5-flash-high"
  },
  "fallback_chain": ["gemini-3.5-flash-high", "gemini-3.1-flash-lite"],
  "default_model": "gemini-3.5-flash-high"
}
```

**fallback_chain** is used by ``_get_agy_fallback_model()`` when a premium
model needs to be demoted.  Entries are MODEL STRINGS (not label names).
**default_model** is the absolute fallback when the chain is exhausted.

## Routing Logic (Updated Jun 16, 2026)
1. **Routine / workers?** → `agent:agy` (`gemini-3.5-flash-high`)
2. **Fast / cheap?** → `agent:agy-lite` (gemini-3.1-flash-lite)
3. **Massive context / plan writing / code analysis?** → `agent:agy-pro` (gemini-3.1-pro-high)
4. **Complex orchestrator work needing GPT?** → Use Codex CLI directly (not AGY): `codex exec -c 'model="gpt-5.5"'`

## Linear Labels Reference
| Label | ID |
|---|---|
| `agent:agy` | 1b69d9c0-20a8-45b3-a594-771b8cba75a7 |
| `agent:agy-pro` | 368360fc-bb54-431a-aeba-8015ba6ffabe |
| ~~`agent:agy-flash-high`~~ | 951ea858-72e2-47ca-aaff-29995f53ffe1 (removed) |
| ~~`agent:agy-sonnet`~~ | fc66a68c-3af6-4701-8ceb-f908fe8411dd (removed) |
| ~~`agent:agy-thinking`~~ | a1dc669e-af19-4eb3-abc6-042e99f6bba2 (removed) |
| ~~`agent:agy-opus`~~ | 34bb6a11-60f3-42e3-b36f-54663b974d88 (removed) |
| ~~`agent:agy-gpt-oss`~~ | 80d638be-2ec3-40b3-bf02-b7a8f2b61766 (removed) |
| ~~`agent:agy-gemini-pro`~~ | 0592be99-e84e-49f5-b7df-a90c95b2c103 (removed) |

## Implementation (GRO-1675 — complete as of Jun 15, 2026)

The routing is implemented in ``prismatic/dispatcher.py``.  Four pieces:

### ``_load_agy_model_config() → dict``
Reads ``~/.antigravity/config.json``.  Returns ``{}`` on any failure (missing
file, invalid JSON) — never raises.  Callers handle the empty dict gracefully.

### ``get_agy_model_from_labels(labels: list[str]) → str | None``
Iterates the issue's Linear labels and returns the first ``--model`` CLI string
found in ``model_bindings``.  Returns ``None`` when no AGY model label matches
(→ AGY uses its own default, no ``--model`` flag injected).

### ``_get_agy_fallback_model(labels: list[str]) → str``
Used when a premium model fails.  Walks ``fallback_chain``, skips the currently
assigned model, and returns the first match.  Falls back to ``default_model``
when the chain is exhausted.

### ``launch_agy(issue_id, task=\"\", labels=None)``
Now accepts an optional ``labels`` parameter.  When ``labels`` is ``None``
(the common case from the dispatch loop), it queries the Linear API via
``get_issue_labels()`` to resolve them automatically.

The ``--model`` flag is injected *before* the circuit breaker check, so
``check_and_route_agy()`` can override it if quotas are exhausted.

Commit: ``20abe23`` on ``ned/gro-1675-agy-model-routing`` (prismatic-engine).

See ``references/dispatcher-integration.md`` for the full wiring diagram,
design decisions, and testing patterns (including the local-import patching
caveat for ``check_and_route_agy``).

## Pitfalls
- ``agy models`` lists DISPLAY names — do NOT use those strings for ``--model``
- Always use the CLI strings from this table
- The deprecated labels exist but should be removed from active routing
- **CRITICAL (Jun 15, 2026): ``agent:agy-pro`` was mapped to ``gemini-3.5-pro`` — a PHANTOM MODEL that doesn't exist.** Gemini 3.5 Pro is not a real model. The correct string is ``gemini-3.1-pro-high``. Every ``agent:agy-pro`` task was silently falling back to flash models. The dispatcher's ``LABEL_TO_MODEL`` dict and the AGY config's ``model_bindings`` must keep in sync with this skill — if one is updated, update all three.
- **Fallback chain format (Jun 15, 2026):** The `fallback_chain` in `config.json` must use MODEL STRINGS (e.g. `"gemini-3.1-flash"`) not label references (e.g. `"agent:agy"`). Label references resolve incorrectly because the fallback logic dereferences the chain entries as model names, not label names.
- **Config file must exist on disk**: if ``~/.antigravity/config.json`` is
  missing, the dispatcher silently skips model routing and AGY uses its own
  default. No error is raised — the model label on the Linear issue is simply
  ignored.  Create the file with the five bindings above before relying on
  label-based routing.
- **Local import caveat**: ``check_and_route_agy`` is imported inside
  ``launch_agy()`` via ``from prismatic.core.router import check_and_route_agy``.
  When unit-testing model routing, patch ``prismatic.core.router.check_and_route_agy``
  directly — patching ``prismatic.dispatcher.check_and_route_agy`` has no effect
  because the import binds to a local variable inside the function body.
