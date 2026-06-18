# Implementation Plan: Linear API Rate Limit Codification

This plan breaks down the Linear API rate limit optimization into bite-sized, actionable tasks for the Prismatic Engine. Each task includes file paths, code snippets, and verification commands.

## Task 1: Create `prismatic/linear/budget.py` and `linear_budget.yaml`

**Goal:** Establish the core rate limiting mechanism and its configuration.

**Files:**
*   `prismatic/linear/budget.py`
*   `prismatic/config/linear_budget.yaml`

**Steps:**
1.  Create the `prismatic/linear` directory.
2.  Create `prismatic/linear/budget.py` with the `LinearBudget` class as defined in the spec.
3.  Create `prismatic/config/linear_budget.yaml` with the default global limit and per-agent budgets.

**Code Snippets (for `prismatic/linear/budget.py`):**
```python
# (Refer to the full LinearBudget class definition in the spec document)
# Example snippet for __init__ and _ensure_db_schema
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

class LinearBudget:
    def __init__(self, db_path: str = "./prismatic_state/linear_budget.db", limit_per_hour: int = 2500):
        self.db_path = db_path
        self.limit_per_hour = limit_per_hour
        self.tokens_per_hour = limit_per_hour
        self.bucket_capacity = limit_per_hour
        self.refill_rate = self.tokens_per_hour / 3600.0
        self._ensure_db_schema()
    # ... rest of the class methods ...
```

**Code Snippets (for `prismatic/config/linear_budget.yaml`):**
```yaml
# prismatic/config/linear_budget.yaml
global_limit_per_hour: 2500
agent_budgets:
  dispatcher.agent_agy: 800
  dispatcher.agent_fred: 100
  cron.comment_trigger_monitor: 20
  # ... (rest of the agent budgets)
```

**Verification:**
```bash
ls prismatic/linear/budget.py
ls prismatic/config/linear_budget.yaml
python3 -c "from prismatic.linear.budget import LinearBudget; b = LinearBudget(); print('Budget system init OK')"
```

--- 

## Task 2: Create `prismatic/linear/metrics.py`

**Goal:** Enable detailed tracking of Linear API usage for observability.

**Files:**
*   `prismatic/linear/metrics.py`

**Steps:**
1.  Create `prismatic/linear/metrics.py` with the `LinearMetrics` class as defined in the spec.

**Code Snippets (for `prismatic/linear/metrics.py`):**
```python
# (Refer to the full LinearMetrics class definition in the spec document)
# Example snippet for __init__ and _ensure_db_schema
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any

class LinearMetrics:
    def __init__(self, db_path: str = "./prismatic_state/linear_metrics.db"):
        self.db_path = db_path
        self._ensure_db_schema()
    # ... rest of the class methods ...
```

**Verification:**
```bash
ls prismatic/linear/metrics.py
python3 -c "from prismatic.linear.metrics import LinearMetrics; m = LinearMetrics(); print('Metrics system init OK')"
```

--- 

## Task 3: Integrate `LinearBudget` into `prismatic/providers/tasks/linear.py`

**Goal:** Enforce rate limits at the lowest level of Linear API interaction.

**Files:**
*   `prismatic/providers/tasks/linear.py`

**Steps:**
1.  Import `linear_budget` into `prismatic/providers/tasks/linear.py`.
2.  Modify the `_graphql` method to call `linear_budget.check_and_consume()` before making the HTTP request.
3.  Pass a context-specific `agent_name` (e.g., `prismatic.dispatcher`, `cron.comment_trigger_monitor`) to `check_and_consume` (can be done via a thread-local or passed parameter in higher-level calls).

**Code Snippets:**
```python
# In prismatic/providers/tasks/linear.py
# ... (existing imports)
from prismatic.linear.budget import linear_budget
import os # to get PRISMATIC_CURRENT_AGENT_NAME

class LinearTaskProvider(TaskProvider):
    # ... (existing methods)

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        # ... (existing API key check)

        agent_name = os.environ.get("PRISMATIC_CURRENT_AGENT_NAME", "prismatic.dispatcher")
        cost = 1

        if not linear_budget.check_and_consume(agent_name, cost):
            print(f"[LinearTaskProvider] 🚫 Rate limit exceeded for {agent_name} — skipping Linear API call.")
            return None

        # ... (existing GraphQL request logic)
```

**Verification:**
```bash
# Manually test by setting a very low limit and making a call:
# Temporarily modify linear_budget.yaml agent_budgets.prismatic.dispatcher: 1
# Run a dispatcher cycle that hits Linear twice. The second call should be rejected.
PRISMATIC_CURRENT_AGENT_NAME=prismatic.dispatcher python3 -c "from prismatic.providers.tasks.linear import LinearTaskProvider; p = LinearTaskProvider(); p.get_issues_with_label('foo'); p.get_issues_with_label('bar')"
```

--- 

## Task 4: Integrate `LinearMetrics` into `prismatic/providers/tasks/linear.py`

**Goal:** Record every Linear API request for observability.

**Files:**
*   `prismatic/providers/tasks/linear.py`

**Steps:**
1.  Import `linear_metrics` into `prismatic/providers/tasks/linear.py`.
2.  Modify the `_graphql` method to call `linear_metrics.record_request()` after each Linear API call, indicating success/failure.

**Code Snippets:**
```python
# In prismatic/providers/tasks/linear.py
# ... (existing imports)
from prismatic.linear.metrics import linear_metrics
# ...

class LinearTaskProvider(TaskProvider):
    # ...
    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        # ... (rate limit check logic)
        
        success = False
        result = None
        try:
            # ... (existing urllib.request.urlopen logic to get result)
            # result = json.loads(body)
            success = True # If no exception and no GraphQL errors in result
            if "errors" in result:
                success = False
            return result
        except Exception as exc:
            # ... (error handling)
            success = False
            return None
        finally:
            # *** NEW: Record metrics ***
            agent_name = os.environ.get("PRISMATIC_CURRENT_AGENT_NAME", "prismatic.dispatcher")
            operation_type = "query" if query.strip().startswith("query") else "mutation"
            linear_metrics.record_request(agent_name, operation_type, cost=1, success=success)
```

**Verification:**
```bash
# Run a dispatcher cycle, then query the metrics database:
sqlite3 ./prismatic_state/linear_metrics.db "SELECT agent_name, operation, cost, success FROM linear_requests ORDER BY timestamp DESC LIMIT 5"
```

--- 

## Task 5: Implement Batched `get_issues_by_labels` in `LinearTaskProvider`

**Goal:** Reduce `agent_dispatcher.py`'s primary Linear API consumption by fetching all agent-labeled issues in one batched query.

**Files:**
*   `prismatic/providers/tasks/linear.py`

**Steps:**
1.  Add a new method `get_issues_by_labels(self, labels: list[str]) -> list[Issue]` to `LinearTaskProvider`.
2.  This method will construct a single GraphQL query using the `labels: { name: { in: [...] } }` filter.

**Code Snippets:**
```python
# In prismatic/providers/tasks/linear.py
class LinearTaskProvider(TaskProvider):
    # ... (existing methods)

    def get_issues_by_labels(self, labels: list[str]) -> list[Issue]:
        """Query all open issues that have any of the given labels."""
        query = """
        query IssuesByLabels($labels: [String!]!) {
          issues(
            filter: {
              labels: { name: { in: $labels } }
              state: { type: { neq: "completed" } }
            }
            first: 250 # Max page size
          ) {
            nodes {
              id
              identifier
              title
              description
              state { name }
              labels { nodes { name } }
              team { name }
            }
          }
        }
        """
        variables = {"labels": labels}
        data = self._graphql_data(query, variables)
        if data is None:
            return []
        nodes = data.get("issues", {}).get("nodes", [])
        return [self._node_to_issue(n) for n in nodes]
```

**Verification:**
```bash
python3 -c "from prismatic.providers.tasks.linear import LinearTaskProvider; p = LinearTaskProvider(); issues = p.get_issues_by_labels(['agent:agy', 'agent:fred']); print(f'Found {len(issues)} issues')"
```

--- 

## Task 6: Migrate `agent_dispatcher.py` calls to use `LinearTaskProvider` and batched query

**Goal:** Consolidate Linear API access and reduce queries in the main dispatcher.

**Files:**
*   `<orchestrator-profile>/scripts/agent_dispatcher.py` (eventually decommissioned/replaced by `prismatic/dispatcher.py`)
*   `prismatic/dispatcher.py`

**Steps:**
1.  In `prismatic/dispatcher.py`, initialize `LinearTaskProvider` and `LinearBudget`.
2.  Modify the main loop to use `linear_provider.get_issues_by_labels()` with all relevant agent labels instead of looping `get_issues_with_label` for each. Filter client-side.
3.  Ensure all Linear API interactions in `prismatic/dispatcher.py` (e.g., `add_comment`, `set_labels`, `transition_label`) use the `LinearTaskProvider` methods, which now have built-in rate limiting.
4.  Decommission the old cron job (`e2f1a3b4c5d6 Unified Agent Dispatcher`).

**Code Snippets (for `prismatic/dispatcher.py`):**
```python
# In prismatic/dispatcher.py (main_loop or dispatch_once)
from .providers.tasks.linear import LinearTaskProvider
from .linear.budget import linear_budget

# ... inside main_loop() or dispatch_once()
linear_provider = LinearTaskProvider(team_id=TEAM_ID)

all_agent_labels = [f"agent::{name}" for name in AGENT_CONFIG.keys() if name != "done"] # Exclude "agent:done"
all_issues = linear_provider.get_issues_by_labels(all_agent_labels)

# Now filter all_issues client-side for each agent_name
for agent_name, config in AGENT_CONFIG.items():
    if agent_name == "done": continue

    agent_specific_issues = [
        issue for issue in all_issues
        if any(label == f"agent::{agent_name}" for label in issue.labels)
    ]
    
    for issue in agent_specific_issues:
        # Check if already dispatched this cycle and honor budget
        # The linear_provider.get_issues_by_labels will now check_and_consume once.
        # Additional per-issue mutations (add_comment, set_labels) will also call check_and_consume.
        if dedup.is_processed(issue.id, f"agent::{agent_name}", cycle_id):
            continue

        # Set PRISMATIC_CURRENT_AGENT_NAME env var for sub-calls if needed
        os.environ["PRISMATIC_CURRENT_AGENT_NAME"] = f"dispatcher.{agent_name}"

        # ... existing dispatch logic for this issue ...
        # e.g., result = launcher(issue.id, title=issue.title)
        # Make sure add_comment and set_labels also use linear_provider
        # linear_provider.add_comment(issue.id, body)
        # linear_provider.set_labels(issue.id, new_label_ids)

    # Unset env var
    if "PRISMATIC_CURRENT_AGENT_NAME" in os.environ:
        del os.environ["PRISMATIC_CURRENT_AGENT_NAME"]
```

**Verification:**
```bash
# Run the prismatic dispatcher, check logs for "Rate limit exceeded" or "Skipping" messages.
# Observe the Linear API request count decrease significantly.
./bin/prismatic-engine --once # (or in a loop for multiple cycles)
sqlite3 ./prismatic_state/linear_metrics.db "SELECT agent_name, SUM(cost) FROM linear_requests WHERE agent_name LIKE 'dispatcher.%' GROUP BY agent_name"
```

--- 

## Task 7: Decommission old orchestrator cron jobs, convert to Prismatic Engine native

**Goal:** Centralize dispatch and rate limit enforcement under the Prismatic Engine.

**Files:**
*   `<orchestrator-profile>/cron/jobs.json`
*   Various scripts in `<orchestrator-profile>/scripts/`

**Steps:**
1.  **Decommission `agent_dispatcher.py`:** Remove the cron entry for `e2f1a3b4c5d6 Unified Agent Dispatcher` from `jobs.json`.
2.  **Integrate `kai_callback_monitor.py` into Prismatic:** Convert `kai_callback_monitor.py` to a `prismatic.dispatch_once` (if possible) or ensure it properly integrates with the `LinearBudget` if kept as a standalone cron.
3.  **Integrate `comment_trigger_monitor.py` into Prismatic:** Convert `comment_trigger_monitor.py` to a `prismatic.dispatch_once` or ensure `LinearBudget` integration and optimize its query/frequency as per audit.
4.  **Review remaining scripts:** Review `prismatic_event_trigger.py`, `prismatic_port_progress.py`, `agy_sandbox_event_supervisor.py`, `jules_session_watchdog.py`, `github_pr_monitor.py`, `action_item_extractor.py`, `agent_output_validator.py` to either:
    *   Integrate `LinearBudget` for their Linear API calls.
    *   Convert them to `prismatic.dispatch_once` calls via the Prismatic Engine, benefiting from its central budget management.
    *   Increase their polling intervals significantly if they are low-priority and not easily converted.

**Code Snippets (example for decommissioning):**
```json
// In <orchestrator-profile>/cron/jobs.json
// Remove the entire job entry for ID "e2f1a3b4c5d6"
```

**Verification:**
```bash
# After modifying jobs.json:
grep "e2f1a3b4c5d6" <orchestrator-profile>/cron/jobs.json
# This should return nothing

# For each integrated script, verify its Linear API calls now pass through `linear_budget.check_and_consume`.
# Check logs for warnings about rate limiting.
```

--- 

## Task 8: Implement `prismatic-engine doctor linear-budget` CLI

**Goal:** Provide operators with a real-time view of Linear API rate limit status and usage.

**Files:**
*   `prismatic/admin.py` (where `doctor` command lives)

**Steps:**
1.  Modify `prismatic/admin.py` to add a `linear-budget` subcommand to the `doctor` command.
2.  This subcommand will interact with `linear_budget.get_current_utilization()` and query the `budget_logs` SQLite table to format the output as specified in the spec.

**Code Snippets (in `prismatic/admin.py`):**
```python
# In prismatic/admin.py
import argparse
from prismatic.linear.budget import linear_budget

def add_doctor_commands(subparsers):
    doctor_parser = subparsers.add_parser("doctor", help="Diagnose engine health")
    doctor_subparsers = doctor_parser.add_subparsers(dest="doctor_command", required=True)

    # Existing doctor commands...

    linear_budget_parser = doctor_subparsers.add_parser("linear-budget", help="Show Linear API rate limit status")
    linear_budget_parser.set_defaults(func=run_linear_budget_doctor)

def run_linear_budget_doctor(args):
    print("\n## Linear API Budget Status (Current Hour)\n")
    # Global summary
    global_status = linear_budget.get_current_utilization("global") # Assuming a 'global' agent for aggregate stats
    print(f"*   **Global Limit:** {global_status['hourly_rate_limit']} requests/hour")
    print(f"*   **Total Consumed:** {global_status['consumed_last_hour']} / {global_status['hourly_rate_limit']} ({global_status['utilization_percentage']}%) ")
    # ... calculate next reset based on global_status ...
    print("\n### Agent Breakdown\n")
    print("| Agent/Script                     | Consumed (Last Hour) | Remaining (Bucket) | Utilization (%) | Next Refill (s) |")
    print("|----------------------------------|----------------------|--------------------|-----------------|-----------------|")
    # Fetch all agent names from budget_state table and iterate, call get_current_utilization for each
    # ... (code to iterate agents and print rows)

    print("\n### Recent Rate Limit Rejections (Last 24h)\n")
    # Query budget_logs table for rejections
    # ... (code to query and print log entries)
```

**Verification:**
```bash
./bin/prismatic-engine doctor linear-budget
# Expected output: a formatted table showing current Linear API budget status per agent.
```

---

## Rollout & Landing Status (GRO-1974 Update)

The following components have successfully landed in the current commit:

1. **LinearBudget Module:** Implemented `LinearBudget` class in `prismatic/linear/budget.py` to enforce a token-bucket rate limiter backed by SQLite.
2. **Dispatcher Integration:** Integrated `LinearBudget.check_and_consume()` checks inside the core GraphQL `gql()` transport in `prismatic/dispatcher.py`. Any exhausted budget triggers a deduped warning (at most once every 5 minutes per agent context) and raises a `LinearBudgetExhaustedError`, allowing the dispatcher loop to gracefully skip processing for that agent.
3. **Doctor CLI Updates:** Extended the Pure doctor module (`prismatic/doctor.py`) and presentation layer (`prismatic/cli/doctor.py`) to output Linear rate limit utilization details when the Linear API is queried. Added corresponding test coverage to verify this output.

### Remaining Work for Green/Blue Rollout

To complete the optimization plan, the following tasks are scheduled for the next phase of rollout:

* **Task 2 & 4 (LinearMetrics):** Centralized request metrics database creation and integration for granular observability.
* **Task 5 & 6 (Batched Provider Integration):** Migrate all legacy inline `gql()` calls in `prismatic/dispatcher.py` to `LinearTaskProvider` using the new batched `get_issues_by_labels` method to reduce primary Linear API overhead.
* **Task 7 (Cron Decommissioning):** Clean up the legacy orchestrator cron jobs (e.g. `agent_dispatcher.py`) and replace them with native, rate-limited Prismatic Engine schedulers.
* **Task 8 (Linear-Budget subcommand):** Implement detailed `doctor linear-budget` CLI reporting.