# Prismatic-Insight: Telemetry & Observability Module — Design Spec

**Author:** Fred (Lead Data Architect)
**Date:** 2026-06-13
**Linear:** GRO-1511 (child of GRO-1493)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                 Prismatic Dispatcher Process                │
│                                                             │
│  dispatch_once()  ──►  TelemetryCollector.push(event)       │
│  recover_stalled() ──►  TelemetryCollector.check_breaker()  │
│  agent_subprocess ──►  TelemetryCollector.record_tokens()   │
│                         │                                   │
│                         ▼ (queue.Queue — non-blocking)      │
│                  ┌──────────────┐                           │
│                  │ Writer Thread │──► SQLite (event_router) │
│                  │  (daemon)     │                           │
│                  └──────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

The telemetry module lives at `prismatic/telemetry.py`. It has zero impact on the dispatch hot path — all writes are pushed onto a `queue.Queue` and processed by a single daemon thread. If the queue is full (10,000 item cap), writes are silently dropped.

---

## 2. Database Schema

Three new tables in the existing `event_router.db` SQLite database:

### 2.1 Loop Architecture (`telemetry_loop_events`)

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| run_id | TEXT | FK to run_records |
| issue_id | TEXT | Linear issue |
| agent | TEXT | fred, ned, kai, agy, jules, codex |
| loop_type | TEXT | micro_review, macro_handoff, circuit_breaker |
| trigger | TEXT | lint_failure, self_correction, agy_review, stall_recovery |
| resolved | INTEGER | 1 = resolved successfully |
| depth | INTEGER | Handoff depth for macro loops |
| parent_id | TEXT | Parent run_id for nested loops |

### 2.2 Circuit Breakers (`telemetry_circuit_breakers`)

| Column | Type | Description |
|--------|------|-------------|
| issue_id | TEXT PK | Linear issue |
| agent | TEXT | Agent name |
| micro_count | INTEGER | Self-review loops without progress |
| macro_count | INTEGER | Orchestrator handoff loops |
| tripped | INTEGER | 1 = breaker tripped, needs human |

**Default thresholds (env-overridable):**
- `PRISMATIC_BREAKER_MICRO_MAX=5` — trip after 5 micro-review loops
- `PRISMATIC_BREAKER_MACRO_MAX=3` — trip after 3 macro handoff loops

### 2.3 Token Metrics (`telemetry_token_metrics`)

| Column | Type | Description |
|--------|------|-------------|
| run_id | TEXT | FK to run_records |
| agent | TEXT | Agent name |
| provider | TEXT | deepseek, google-antigravity, local-llm, claude-code |
| model | TEXT | deepseek-v4-flash, hermes-70b, qwen-2.5 |
| prompt_tokens | INTEGER | Tokens in |
| completion_tokens | INTEGER | Tokens out |
| ttft_ms | REAL | Time-to-first-token |
| tps | REAL | Tokens per second |
| context_pct | REAL | Context window utilization % |
| vram_mb | INTEGER | VRAM at inference (local only) |

### 2.4 Validation Events (`telemetry_validation_events`)

| Column | Type | Description |
|--------|------|-------------|
| run_id | TEXT | FK to run_records |
| agent | TEXT | Agent name |
| event_type | TEXT | test_run, compile, lint, rollback, watchdog_fire |
| total_tests | INTEGER | Total tests run |
| passed | INTEGER | Passed |
| failed | INTEGER | Failed |
| sandbox_id | TEXT | Git SHA or container ID |
| rollback | INTEGER | 1 if auto-rollback triggered |
| watch_sec | REAL | Seconds before watchdog fired |

---

## 3. Core Module API

```python
# prismatic/telemetry.py

class TelemetryCollector:
    """Non-blocking telemetry collector. All writes go through a queue."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._queue = queue.Queue(maxsize=10000)
        self._db_path = db_path
        self._writer = threading.Thread(target=self._drain, daemon=True)
        self._writer.start()

    def record_loop(self, run_id, issue_id, agent, loop_type, trigger=None,
                    resolved=False, depth=0, parent_id=None):
        """Push a loop event onto the queue."""

    def record_tokens(self, run_id, agent, provider, model=None,
                      prompt_tokens=0, completion_tokens=0,
                      ttft_ms=0.0, tps=0.0, context_pct=0.0, vram_mb=0):
        """Push token metrics onto the queue."""

    def record_validation(self, run_id, agent, event_type,
                          total=0, passed=0, failed=0,
                          sandbox_id=None, rollback=False, watch_sec=0.0):
        """Push validation event onto the queue."""

    def check_circuit(self, issue_id, agent, micro_count, macro_count) -> bool:
        """Check and update circuit breaker. Returns True if tripped."""

    def get_dashboard_data(self, hours: int = 24) -> dict:
        """Query recent telemetry for dashboard display."""

# Singleton — one instance per dispatcher process
_collector: TelemetryCollector | None = None

def get_collector() -> TelemetryCollector:
    """Get or create the global telemetry collector."""
```

---

## 4. Integration Points

### 4.1 dispatch_once() — Loop Events (dispatcher.py ~line 1460)

After `launcher()` returns successfully:
```python
from .telemetry import get_collector
telemetry = get_collector()

# After agent launch
telemetry.record_loop(
    run_id=run_id,
    issue_id=issue_id,
    agent=agent_name,
    loop_type='dispatch',
    trigger='linear_label_match'
)
```

### 4.2 recover_stalled_agy() — Circuit Breaker (dispatcher.py ~line 941)

After incrementing cycle count:
```python
tripped = telemetry.check_circuit(
    issue_id=issue_id,
    agent='agy',
    micro_count=cycle_count,
    macro_count=escalation_count
)
if tripped:
    # Already handled: comment posted, label transitioned
    pass
```

### 4.3 credit_policy_engine.evaluate() — Token Budget (credit_policy_engine.py ~line 260)

After policy decision:
```python
telemetry.record_tokens(
    run_id=thread_id,
    agent=agent_label,
    provider=self.provider,
    prompt_tokens=estimated_cost,  # proxy until real token counts available
)
```

### 4.4 Subprocess Exit Watcher (new, dispatcher.py)

Background thread added to `launch_agy/jules/codex`:
```python
def _watch_subprocess(proc, run_id, agent, issue_id):
    proc.wait()
    telemetry = get_collector()
    telemetry.record_validation(
        run_id=run_id,
        agent=agent,
        event_type='exit',
        total=1,
        passed=1 if proc.returncode == 0 else 0,
        failed=1 if proc.returncode != 0 else 0,
    )
```

---

## 5. Token Capture Strategy

| Provider | Method | Source |
|----------|--------|--------|
| DeepSeek | Parse `x-usage-*` response headers | AGY/Jules subprocess stderr or API client log |
| Google Antigravity | `ToolContext.get_state('last_generation_cost')` | Already in credit_policy_engine |
| Local LLM (Hermes 70b) | Subprocess wrapper writes `/tmp/prismatic/token_<run_id>.json` | Post-run read |
| Local LLM (Qwen 2.5) | Same pattern as Hermes 70b | Post-run read |
| Claude Code | Estimate from input/output byte counts | Log output |

---

## 6. Dashboard Extension

`scripts/pipeline_dashboard.py` gains a `--telemetry` flag:

```
$ python scripts/pipeline_dashboard.py --telemetry

╔══════════════════════════════════════════════════════════╗
║           PRISMATIC-INSIGHT TELEMETRY                    ║
╠══════════════════════════════════════════════════════════╣
║  Last 24 Hours                                           ║
║  ─────────────                                           ║
║  Dispatches:  47    Stalled:  2    Blocked:  1           ║
║  Tokens Used: 284,500    Avg TPS:  47.3                 ║
║  Context Heat (Hermes 70b):  62%                         ║
║  Context Heat (Qwen 2.5):    38%                         ║
║  Tests:  142 passed / 3 failed (97.9%)                   ║
║                                                          ║
║  Circuit Breakers:  None tripped                         ║
║  Rollbacks:  0                                           ║
╚══════════════════════════════════════════════════════════╝
```

---

## 7. Development Steps

1. **Create `prismatic/telemetry.py`** — TelemetryCollector class with queue + writer thread
2. **Run DB migration** — Add 4 tables to `event_router.db` (idempotent: CREATE TABLE IF NOT EXISTS)
3. **Wire dispatch hooks** — 3-4 lines each in dispatcher.py integration points
4. **Circuit breaker thresholds** — Configurable via env vars, default 5 micro / 3 macro
5. **Subprocess watcher** — Background thread for exit code capture
6. **Token capture** — Start with estimates from credit_policy_engine, add real parsing in follow-up
7. **Dashboard extension** — Add `--telemetry` flag to pipeline_dashboard.py
8. **AGY review** — Run through Second Witness for schema and integration review
