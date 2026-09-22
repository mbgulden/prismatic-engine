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
- Fallback backend with no defaults → `NoBackendError`.
- `decide(..., on_error="deterministic", defaults={...})` returns the
  caller-supplied defaults on backend failure; missing defaults is itself a
  `DecisionError`. **A failed Jev call never invents a decision.**

## Safety rules (mechanical)

- **No-downgrade:** route every verdict through
  `apply_jev_advice(deterministic, jev_choice)`. Deterministic REPAIR/REJECT
  is final; Jev can only escalate a CLEAN to ESCALATE (pause for human).
- **Per-call-site gating:** `CallSiteGate("<site>")` requires both
  `SWARMJEV_ENABLED` and `SWARMJEV_CALLSITE_<SITE>_ENABLED` to be truthy.
  Both default off. `allow()` fails closed.
- **Credential hygiene:** keys from env only; never in logs, audit dicts,
  error messages, or `repr`s.
- **Audit:** `result.to_audit_dict()` records backend, latency, answers, and
  a sha256 of the canonical state — never state values, never keys.
