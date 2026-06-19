import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

class LinearBudget:
    def __init__(self, db_path: str | None = None, limit_per_hour: int = 2500):
        if db_path is None:
            state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
            os.makedirs(state_dir, exist_ok=True)
            db_path = os.path.join(state_dir, "linear_budget.db")
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

    def check_and_consume(self, agent: str, cost: int = 1) -> bool:
        agent_name = agent
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


class LinearBudgetExhaustedError(RuntimeError):
    """Raised when Linear API rate limit budget is exhausted."""
    pass


def _record_call_count(bucket: str):
    import json
    import os
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
    try:
        os.makedirs(state_dir, exist_ok=True)
        counts_file = os.path.join(state_dir, "linear_call_counts.json")
        counts = {}
        if os.path.exists(counts_file):
            try:
                with open(counts_file, "r") as f:
                    counts = json.load(f)
            except Exception:
                counts = {}
        counts[bucket] = counts.get(bucket, 0) + 1
        
        # Atomic write
        tmp_file = counts_file + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(counts, f, indent=2)
        os.replace(tmp_file, counts_file)
    except Exception as exc:
        print(f"[LinearBudget] WARNING: Failed to record call count for {bucket}: {exc}")


def linear_call(
    bucket: str,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a GraphQL query/mutation against the Linear API under the budget bucket.
    
    Checks and consumes rate limit token from the budget before making the call.
    Sleeps and retries on RATELIMITED extensions.
    Records actual call counts to a state file.
    """
    import urllib.request
    import urllib.error
    import json
    import os
    import time

    # 1. Record call count
    _record_call_count(bucket)

    # 2. Setup payload
    payload = json.dumps({
        "query": query,
        "variables": variables or {},
    }).encode("utf-8")

    api_key = os.environ.get("LINEAR_API_KEY", "")
    if not api_key:
        raise RuntimeError("LINEAR_API_KEY environment variable is required")

    # Linear GraphQL endpoint
    url = "https://api.linear.app/graphql"

    max_retries = 3
    for attempt in range(max_retries):
        # 3. Check rate limit budget
        if not linear_budget.check_and_consume(bucket, cost=1):
            raise LinearBudgetExhaustedError(f"Linear API rate limit budget exhausted for {bucket}")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                result = json.loads(body)
                
                # Check for GraphQL level RATELIMITED error code
                if "errors" in result:
                    rate_limited = False
                    duration_hint_ms = None
                    for err in result["errors"]:
                        extensions = err.get("extensions", {})
                        if isinstance(extensions, dict) and extensions.get("code") == "RATELIMITED":
                            rate_limited = True
                            duration_hint_ms = extensions.get("duration")
                            break
                    
                    if rate_limited:
                        sleep_time = (duration_hint_ms / 1000.0) if duration_hint_ms else 60.0
                        sleep_time += 1.0
                        print(f"[linear_call] Rate limited by Linear. Sleeping for {sleep_time:.2f}s before retry (attempt {attempt+1}/{max_retries})...")
                        time.sleep(sleep_time)
                        continue # retry

                return result

        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                    result = json.loads(body)
                    duration_hint_ms = None
                    if "errors" in result:
                        for err in result["errors"]:
                            extensions = err.get("extensions", {})
                            if isinstance(extensions, dict) and extensions.get("code") == "RATELIMITED":
                                duration_hint_ms = extensions.get("duration")
                                break
                except Exception:
                    duration_hint_ms = None
                
                sleep_time = (duration_hint_ms / 1000.0) if duration_hint_ms else 60.0
                sleep_time += 1.0
                print(f"[linear_call] HTTP 429 Rate Limited. Sleeping for {sleep_time:.2f}s before retry (attempt {attempt+1}/{max_retries})...")
                time.sleep(sleep_time)
                continue # retry
            else:
                body = exc.read().decode(errors="replace")[:500]
                raise RuntimeError(f"Linear API HTTP {exc.code}: {body}")
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            time.sleep(1.0)
            continue
            
    raise RuntimeError("Linear API call failed after max retries due to rate limiting")


__all__ = ["LinearBudget", "linear_budget", "LinearBudgetExhaustedError", "linear_call"]
