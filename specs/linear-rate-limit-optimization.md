# Prismatic Engine: Linear API Rate Limit Optimization Specification

## 1. Goal

To prevent Linear API rate limit exhaustion (2,500 requests/hour on Starter/Free tier) by auditing current consumption, implementing a centralized rate limiting and budget enforcement module, and optimizing high-frequency Linear API calls within the Prismatic Engine and associated orchestrator scripts. This ensures reliable agent operation and predictable Linear API usage.

## 2. LinearBudget Module (`prismatic/linear/budget.py`)

Introduce a new module `prismatic/linear/budget.py` to manage Linear API request consumption. This module will provide a token-bucket or sliding-window rate limiter.

### `LinearBudget` Class API

```python
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

class LinearBudget:
    def __init__(self, db_path: str = "./prismatic_state/linear_budget.db", limit_per_hour: int = 2500):
        self.db_path = db_path
        self.limit_per_hour = limit_per_hour
        self.tokens_per_hour = limit_per_hour
        self.bucket_capacity = limit_per_hour  # Max tokens in the bucket
        self.refill_rate = self.tokens_per_hour / 3600.0  # Tokens per second
        self._ensure_db_schema()

    def _ensure_db_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budget_state (
                    agent_name TEXT PRIMARY KEY,
                    current_tokens REAL NOT NULL,
                    last_refill_at TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budget_logs (
                    timestamp TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    cost INTEGER NOT NULL,
                    action TEXT NOT NULL, -- 'consume' or 'reject'
                    reason TEXT,
                    retry_after INTEGER -- seconds
                )
            """)
            conn.commit()

    def _get_state(self, agent_name: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_tokens, last_refill_at FROM budget_state WHERE agent_name = ?", (agent_name,))
            row = cursor.fetchone()
            if row:
                return {
                    "current_tokens": row[0],
                    "last_refill_at": datetime.fromisoformat(row[1])
                }
            else:
                # Initialize new agent
                now = datetime.now(timezone.utc)
                cursor.execute("INSERT INTO budget_state (agent_name, current_tokens, last_refill_at) VALUES (?, ?, ?)",
                               (agent_name, self.bucket_capacity, now.isoformat()))
                conn.commit()
                return {
                    "current_tokens": self.bucket_capacity,
                    "last_refill_at": now
                }

    def _save_state(self, agent_name: str, current_tokens: float, last_refill_at: datetime):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO budget_state (agent_name, current_tokens, last_refill_at) VALUES (?, ?, ?)",
                           (agent_name, current_tokens, last_refill_at.isoformat()))
            conn.commit()

    def _refill(self, agent_name: str, state: Dict[str, Any]) -> float:
        now = datetime.now(timezone.utc)
        last_refill_at = state["last_refill_at"]
        time_passed = (now - last_refill_at).total_seconds()
        
        refill_amount = time_passed * self.refill_rate
        new_tokens = min(self.bucket_capacity, state["current_tokens"] + refill_amount)
        state["last_refill_at"] = now
        state["current_tokens"] = new_tokens
        return new_tokens

    def check_and_consume(self, agent_name: str, cost: int = 1) -> bool:
        state = self._get_state(agent_name)
        current_tokens = self._refill(agent_name, state)

        if current_tokens >= cost:
            state["current_tokens"] -= cost
            self._save_state(agent_name, state["current_tokens"], state["last_refill_at"])
            self._log_event(agent_name, cost, "consume")
            return True
        else:
            # Calculate retry_after for logging
            needed_tokens = cost - current_tokens
            retry_after = int(needed_tokens / self.refill_rate) + 1 # +1 for buffer
            self._log_event(agent_name, cost, "reject", reason=f"Not enough tokens. Need {cost}, have {current_tokens:.2f}.", retry_after=retry_after)
            return False

    def _log_event(self, agent_name: str, cost: int, action: str, reason: str = None, retry_after: int = None):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO budget_logs (timestamp, agent_name, cost, action, reason, retry_after) VALUES (?, ?, ?, ?, ?, ?)",
                           (datetime.now(timezone.utc).isoformat(), agent_name, cost, action, reason, retry_after))
            conn.commit()

    def get_current_utilization(self, agent_name: str) -> Dict[str, Any]:
        state = self._get_state(agent_name)
        current_tokens = self._refill(agent_name, state) # Refill before reporting
        remaining = current_tokens
        hourly_rate_limit = self.limit_per_hour
        
        # Estimate consumption over the last hour
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        consumed_in_last_hour = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(cost) FROM budget_logs WHERE agent_name = ? AND action = 'consume' AND timestamp >= ?",
                           (agent_name, one_hour_ago.isoformat()))
            row = cursor.fetchone()
            if row and row[0]:
                consumed_in_last_hour = row[0]

        return {
            "agent": agent_name,
            "current_tokens": round(remaining, 2),
            "bucket_capacity": self.bucket_capacity,
            "tokens_per_hour": self.tokens_per_hour,
            "consumed_last_hour": consumed_in_last_hour,
            "hourly_rate_limit": hourly_rate_limit,
            "utilization_percentage": round((consumed_in_last_hour / hourly_rate_limit) * 100, 2) if hourly_rate_limit > 0 else 0,
            "next_reset_estimate": (state["last_refill_at"] + timedelta(seconds=self.bucket_capacity / self.refill_rate)).isoformat() if remaining < self.bucket_capacity else None,
            "retry_after_estimate": int((self.bucket_capacity - remaining) / self.refill_rate) + 1 if remaining < self.bucket_capacity else 0,
        }

# Global instance for easy access
linear_budget = LinearBudget()
```

**Key features:**

*   **Token-Bucket Algorithm:** Refills at a steady rate (`refill_rate` = `limit_per_hour / 3600`), bursts up to `bucket_capacity`.
*   **Persistence:** Uses SQLite to store `current_tokens` and `last_refill_at` for each agent, surviving dispatcher restarts.
*   **`check_and_consume` API:** Atomic check and consume. Returns `True` if tokens are available and consumed, `False` otherwise.
*   **Logging:** Records all consume/reject events to `budget_logs` table, including `reason` and `retry_after`.
*   **Observability:** `get_current_utilization` provides real-time metrics including tokens remaining, consumed in last hour, and estimated reset/retry times.

## 3. Configuration Schema (`prismatic/config/linear_budget.yaml`)

Define a YAML schema for per-agent/per-script Linear API budgets. This allows operators to fine-tune rate limits based on agent importance or expected workload.

```yaml
# prismatic/config/linear_budget.yaml
# Defines Linear API rate limits for agents and scripts.
# Limits are applied to GraphQL requests (queries/mutations).

# Global default limit (if not overridden by agent-specific budget)
# Uses Linear's Free/Starter tier default: 2500 requests/hour
global_limit_per_hour: 2500

# Per-agent/per-script budgets.
# Keys are unique identifiers (e.g., 'dispatcher.fred', 'cron.becca-recap').
# Values are the allowed requests per hour for that entity.
# If an agent/script is not listed, it defaults to `global_limit_per_hour`.
agent_budgets:
  dispatcher.agent_agy: 800  # AGY tasks can be bursty, allow more
  dispatcher.agent_jules: 200 # Jules CLI review tasks
  dispatcher.agent_fred: 100 # Fred orchestrates, fewer direct Linear calls
  dispatcher.agent_kai: 100  # Kai content generation / orchestration
  dispatcher.agent_ned: 100  # Ned code/infra tasks
  
  cron.comment_trigger_monitor: 20   # Optimized down from 60-120
  cron.kai_callback_monitor: 10    # Optimized down from 90
  cron.prismatic_event_trigger: 5    # Low-frequency alerts
  cron.prismatic_port_progress: 5    # Low-frequency reporting
  cron.second_witness_agy_proxy: 2   # Delta-cached, rarely consumes
  cron.nightly_backlog_delta: 1      # Daily, delta-cached
  cron.action_item_extractor: 20   # Daily, some queries/mutations
  cron.github_pr_monitor: 5          # Infrequent PR checks
  cron.jules_session_watchdog: 5     # Infrequent session checks

# Note: linear_oauth_refresh.py is excluded as it hits the OAuth endpoint, not GraphQL.
```

## 4. Integration with `prismatic.dispatcher.dispatch_once()`

The `dispatch_once()` function (and its caller `main_loop()`) in `prismatic/dispatcher.py` will integrate with the `LinearBudget` module.

### Proposed Changes in `prismatic/dispatcher.py`

1.  **Import `LinearBudget`:**
    ```python
    # In prismatic/dispatcher.py
    from .linear.budget import linear_budget, LinearBudget
    from .config.loader import load_config # New config loader for linear_budget.yaml
    ```

2.  **Initialize/Load Budgets (in `main_loop` or `__init__` of a dispatcher class):**
    ```python
    # Inside main_loop() or a dispatcher class __init__
    # Load custom budgets from prismatic/config/linear_budget.yaml
    budget_config = load_config("linear_budget.yaml", default_path="prismatic/config")
    global_limit = budget_config.get("global_limit_per_hour", 2500)
    linear_budget_instance = LinearBudget(limit_per_hour=global_limit)
    
    for agent, limit in budget_config.get("agent_budgets", {}).items():
        linear_budget_instance.set_agent_limit(agent, limit) # New method on LinearBudget
    
    # Pass linear_budget_instance to dispatch_once or make it globally accessible
    ```

3.  **Integrate `check_and_consume` Before Linear API Calls:**
    Modify `get_issues_with_label` and other Linear API wrappers (e.g., `gql` in the old dispatcher, or `_graphql` in `LinearTaskProvider`) to call `linear_budget_instance.check_and_consume`.

    *Example for `LinearTaskProvider._graphql` (which `dispatcher.py` now uses):*
    ```python
    # In prismatic/providers/tasks/linear.py
    # Modify _graphql to integrate with LinearBudget
    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self._api_key:
            print("[LinearTaskProvider] No API key — skipping request")
            return None

        # *** NEW: Rate limit check and consume ***
        # Determine agent_name context (e.g., from a thread-local or passed parameter)
        # For now, use a generic 'prismatic.dispatcher'
        agent_name = os.environ.get("PRISMATIC_CURRENT_AGENT_NAME", "prismatic.dispatcher")
        cost = 1 # Each GraphQL query/mutation counts as 1 request

        if not linear_budget.check_and_consume(agent_name, cost):
            print(f"[LinearTaskProvider] 🚫 Rate limit exceeded for {agent_name} — skipping Linear API call.")
            return None # Or raise a specific RateLimitExceeded exception

        # ... existing GraphQL request logic ...
    ```

4.  **On Rejection, Log and Notify:**
    When `check_and_consume` returns `False` (rate limit exceeded), the dispatcher should:
    *   Log a warning (already handled by `LinearBudget._log_event`).
    *   Skip the current dispatch cycle for that agent/script.
    *   Post a single, deduped Telegram/Autobot message (e.g., every 15-30 minutes) about the rate limit issue, including `retry_after` info.

    ```python
    # In prismatic/dispatcher.py (inside dispatch_once loop, around issue processing)
    # ... before calling launcher(issue_id, ...)
    agent_budget_name = f"dispatcher.{agent_name}" # Or derive from issue labels/metadata
    if not linear_budget.check_and_consume(agent_budget_name, cost=estimated_linear_queries_for_this_issue):
        print(f"[dispatcher] 🚫 Skipping {issue['identifier']} for {agent_name}: Linear API rate limit exceeded. Check `prismatic-engine doctor linear-budget`.")
        # Add a mechanism to trigger a deduped Telegram alert about rate limits
        trigger_deduped_telegram_alert(f"Linear API rate limit hit for {agent_budget_name} (retry in {linear_budget.get_current_utilization(agent_budget_name)['retry_after_estimate']}s)")
        continue # Skip this issue, try next cycle
    
    # ... existing launcher logic ...
    ```

## 5. `prismatic-engine doctor linear-budget` Section

Extend the `prismatic-engine doctor` CLI command to include a `linear-budget` subcommand. This will provide real-time visibility into Linear API rate limit utilization.

### Proposed CLI Output

```bash
$ prismatic-engine doctor linear-budget

## Linear API Budget Status (Current Hour)

*   **Global Limit:** 2500 requests/hour
*   **Total Consumed:** 120 / 2500 (4.80%)
*   **Next Reset:** 42 minutes, 15 seconds

### Agent Breakdown

| Agent/Script                     | Consumed (Last Hour) | Remaining (Bucket) | Utilization (%) | Next Refill (s) |
|----------------------------------|----------------------|--------------------|-----------------|-----------------|
| `dispatcher.agent_agy`           | 100 / 800            | 750                | 12.50%          | 0               |
| `dispatcher.agent_jules`         | 10 / 200             | 190                | 5.00%           | 0               |
| `dispatcher.agent_fred`          | 5 / 100              | 95                 | 5.00%           | 0               |
| `cron.comment_trigger_monitor`   | 15 / 20              | 5                  | 75.00%          | 15              |
| `cron.kai_callback_monitor`      | 8 / 10               | 2                  | 80.00%          | 8               |
| ... (other configured agents) ... |                      |                    |                 |                 |

### Recent Rate Limit Rejections (Last 24h)

| Timestamp (UTC)      | Agent/Script                 | Cost | Reason                                     | Retry After (s) |
|----------------------|------------------------------|------|--------------------------------------------|-----------------|
| 2026-06-18T04:29:00Z | `cron.comment_trigger_monitor` | 1    | Not enough tokens. Need 1, have 0.75.      | 2               |
| 2026-06-18T04:27:00Z | `dispatcher.agent_agy`       | 5    | Not enough tokens. Need 5, have 2.30.      | 15              |
```

**Implementation details:**

*   The `linear_budget.py` module will expose `get_current_utilization(agent_name)` and potentially a global status. The CLI command will query this.
*   The `budget_logs` table (SQLite) will store rejection events, which the CLI will query and display.

## 6. Observability and Metrics (`prismatic/linear/metrics.py`)

Introduce a `prismatic/linear/metrics.py` module to record detailed per-script Linear API request counts, distinct from the rate-limiting budget. This is for long-term analysis and dashboarding.

### `LinearMetrics` Class API

```python
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any

class LinearMetrics:
    def __init__(self, db_path: str = "./prismatic_state/linear_metrics.db"):
        self.db_path = db_path
        self._ensure_db_schema()

    def _ensure_db_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS linear_requests (
                    timestamp TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    operation TEXT NOT NULL, -- 'query', 'mutation', 'unknown'
                    cost INTEGER NOT NULL,
                    success INTEGER NOT NULL -- 1 for success, 0 for failure
                )
            """)
            conn.commit()

    def record_request(self, agent_name: str, operation: str, cost: int = 1, success: bool = True):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO linear_requests (timestamp, agent_name, operation, cost, success) VALUES (?, ?, ?, ?, ?)",
                           (datetime.now(timezone.utc).isoformat(), agent_name, operation, cost, 1 if success else 0))
            conn.commit()

    def get_hourly_summary(self) -> Dict[str, Any]:
        summary = {}
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT agent_name, operation, SUM(cost) as total_cost, SUM(success) as success_count
                FROM linear_requests
                WHERE timestamp >= ?
                GROUP BY agent_name, operation
            """, (one_hour_ago.isoformat(),))
            for row in cursor.fetchall():
                agent, op, total_cost, success_count = row
                if agent not in summary:
                    summary[agent] = {"total_requests": 0, "successful_requests": 0, "operations": {}}
                summary[agent]["total_requests"] += total_cost
                summary[agent]["successful_requests"] += success_count
                summary[agent]["operations"][op] = {"cost": total_cost, "success_count": success_count}
        return summary

# Global instance for easy access
linear_metrics = LinearMetrics()
```

**Integration:**

*   `prismatic/providers/tasks/linear.py` (`_graphql` method) will call `linear_metrics.record_request()` after each Linear API call (whether successful or failed, but not rate-limited).
*   The `prismatic-engine doctor linear-budget` command can also pull summary metrics from `LinearMetrics` to show actual consumption alongside budget usage.

This comprehensive spec ensures both proactive rate limit prevention and reactive monitoring, allowing for granular control and visibility of Linear API usage.