# prismatic.jev — typed-decision primitive

Jev (TypeSafe's "System One" model) answers typed questions — `Choice`,
`Score`, `Noul` — in parallel, returning calibrated probabilities. This
package owns transport, schema validation, and safety plumbing. Question sets
live with the (future) call sites; this package ships **zero** call sites.

## Quick start

```python
from prismatic.jev import DecisionClient, Choice, Noul

client = DecisionClient()  # backend from SWARMJEV_BACKEND; default: fallback
result = client.decide(
    state={"pr_title": "...", "ci_status": "green"},
    questions=[Noul("deserves_review", "Does this need a deep review?")],
)
result.answers["deserves_review"].probability
```

## Backends

`SWARMJEV_BACKEND` selects the backend (`typesafe` | `openrouter` |
`fallback`; default `fallback`).

| Backend | Key env var | Endpoint |
|---|---|---|
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/alpha/decisions` |
| `typesafe` | `JEV_API_KEY` | `JEV_API_URL` or `https://api.typesafe.ai/v1/decisions` (provisional — TypeSafe direct API was waitlist-only at implementation time) |
| `fallback` | none | no network |

Optional: `SWARMJEV_MODEL` (default `typesafe/jev-1.13`).

## Fail-closed matrix

- Missing key for a network backend → `MissingCredentialError` naming the
  env var (never the value).
- Timeout, HTTP error, bad JSON, missing/malformed answer → `DecisionError`.
- Unknown response `schema_version` → `DecisionError` (never parsed hopefully).
- Circuit breaker open → `CircuitOpenError` (fail fast, no network call).
- Cost/latency budget exceeded → `BudgetExceededError`, routed through the
  same `on_error` handling as any backend failure.
- Fallback backend → `NoBackendError` (no network access; deterministic
  defaults live in the client, not the backend).
- `decide(..., on_error="deterministic", defaults={...})` returns the
  caller-supplied defaults on backend failure; missing defaults is itself a
  `DecisionError`. **A failed Jev call never invents a decision.**

## Resilience (production-grade)

Every network call runs inside a fixed layering:

```
bulkhead (per-backend concurrency cap)
  → circuit breaker (per backend+model: closed → open → half-open)
    → classified retry (transient errors only: 408/429/5xx, timeouts,
       connection failures; never 4xx) with backoff+jitter, Retry-After honored
      → timeout
```

- Retries share the caller's `idempotency_key` and stay inside the call's
  cost/latency budget.
- 429s back off; they do not trip the breaker (throttling is not an outage).
  5xx/timeouts/connection failures do.
- A breaker "open" state maps onto the zero-AI deterministic fallback.

Tune via `SWARMJEV_TIMEOUT_S`, `SWARMJEV_MAX_ATTEMPTS`,
`SWARMJEV_BACKOFF_BASE_S`, `SWARMJEV_BACKOFF_CAP_S`,
`SWARMJEV_BREAKER_THRESHOLD`, `SWARMJEV_BREAKER_RESET_S`,
`SWARMJEV_MAX_CONCURRENT`. Invalid values raise `DecisionError` at startup —
the primitive never runs on misunderstood configuration.

## Budgets and idempotency

```python
result = client.decide(
    state, questions,
    cost_budget=0.05,          # USD; pre-call gate + post-hoc accounting
    latency_budget_ms=2000,     # wall-clock across retries and repairs
    idempotency_key="evt-123",  # sent as Idempotency-Key; retries share it
)
```

The pre-call gate estimates worst-case cost (retries × repairs) and fails
closed when it exceeds the budget — or when the backend has no known price
(unknown price + budget = refuse to proceed blind). Post-call, actual
`tokens_in`/`cost_usd` land on the result and the trace.

## Bounded schema repair

A malformed answer gets at most `SWARMJEV_MAX_REPAIR_ATTEMPTS` (default 1,
max 2) resends with the validation error attached — inside the latency
budget, through the same redacted payload path. Persistent violations raise.

## Trace (one record per decision)

Every `decide()` emits one `TraceRecord`: trace_id, timestamps, schema and
prompt versions (`prompt_id@version#sha256`), state hash + keys (never raw
state values), per-phase latencies, attempts, repair attempts, tokens, cost,
answer distributions, and flags (`fallback_used`, `deterministic`,
`abstained`, `budget_exceeded`, `repaired`, `memo_hit`). Set
`SWARMJEV_TRACE_PATH` for a JSONL sink.

Telemetry is the single fail-open seam: a broken sink produces an
observability gap, never a blocked decision.

## First-class abstain

```python
Choice("verdict", "Triage.", options=["CLEAN", "REPAIR"], abstain_below=0.7)
```

When confidence (or a documented uncertainty proxy when the backend returned
none) falls below the floor, the answer is marked `abstained` with a reason —
raw probabilities kept for provenance. The primitive never escalates on
abstain; the caller decides:

```python
final = apply_jev_advice("CLEAN", advice_choice(result.answers["verdict"]))
# advice_choice returns None for abstains — the deterministic verdict stands
```

`maybe_decide(state, questions, gate=...)` runs the zero-network pre-check
(call-site gate, breaker state) before spending anything.

## Provenance and redaction

State passes through one chokepoint (`redact.serialize_state`) immediately
before the HTTP call: mark untrusted spans with `Untrusted("...")` — they get
per-call nonce delimiters (`<data_{nonce}>…</data_{nonce}>`), delimiter-like
tags are stripped so input cannot break out of the span, and PII
(emails, phones, SSNs, cards, secret assignments) is redacted from all
strings. Trusted strings are redacted too, but not delimited.

## Prompt registry

Prompts are versioned files, not inline strings. `prompts/registry.json` maps
`prompt_id → {version, file}`; traces record `prompt_id@version#sha256`.
Questions take `prompt_id="triage_v1"`; unknown ids fail closed. The package
ships an empty registry — callers register their own.

## Memoization (opt-in)

`SWARMJEV_MEMOIZE=1` enables exact-hash memoization: the key is the SHA-256
of (schema version, backend, model, question wire shapes, canonical state).
Exact matches only — similar state never hits. No semantic caching, by
design (the no-invention rule forbids it).

## Offline eval harness

```bash
python -m prismatic.jev.eval.run
```

Runs recorded fixtures (schema conformance, no-downgrade adversarial cases),
reports Brier score and ECE for calibration, and computes Platt/temperature
recalibration parameters **report-only** — recalibration never alters the
primitive's output. No network, safe in CI.

## Safety rules (mechanical)

- **No-downgrade:** route every verdict through
  `apply_jev_advice(deterministic, jev_choice)`. Deterministic REPAIR/REJECT
  is final; Jev can only escalate a CLEAN to ESCALATE (pause for human).
- **Per-call-site gating:** `CallSiteGate("<site>")` requires both
  `SWARMJEV_ENABLED` and `SWARMJEV_CALLSITE_<SITE>_ENABLED` to be truthy.
  Both default off. `allow()` fails closed.
- **Credential hygiene:** keys from env only; never in logs, audit dicts,
  traces, error messages, or `repr`s.
- **Audit:** `result.to_audit_dict()` records backend, latency, answers, and
  a sha256 of the canonical state — never state values, never keys.
- **No auto-escalation:** the primitive never switches to a stronger model
  on its own; no request hedging; no semantic caching.
