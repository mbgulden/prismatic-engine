# Linear API Rate Limit Audit — Orchestrator Profile

## Overview

This document audits Linear API consumption within the `/home/ubuntu/.hermes/profiles/orchestrator/` profile, focusing on identifying the heaviest consumers and proposing optimizations to prevent hitting Linear's 2,500 requests-per-hour rate limit. Recent outages (June 2026) confirmed "Only 2500 requests are allowed per 1 hour" errors. The orchestrator profile's cron-driven dispatcher is the immediate hotspot.

## Inventory of Linear API Consumers

| File Path                                                               | Per-invocation Cost (GraphQL queries/mutations) | Frequency          | Has State Cache/Dedup? | Replaceable by Engine? | Estimated Requests/Hour |
|-------------------------------------------------------------------------|-------------------------------------------------|--------------------|------------------------|------------------------|-------------------------|
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py` | 30+ queries + (1-3 mutations/dispatched issue)  | Every 2 minutes    | Yes (partial)          | Yes (Prismatic Dispatcher) | 900-1000                |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/comment_trigger_monitor.py` | 1 query + (1-2 mutations/triggered comment)     | Every 1 minute     | Yes (processed comments) | Yes                    | 60-120                  |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/kai_callback_monitor.py` | 3 queries                                       | Every 2 minutes    | No                     | Yes                    | 90                      |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_event_trigger.py` | 1 query                                         | Every 2 minutes    | Yes (alerted states)   | Yes                    | 30                      |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/agent_output_validator.py` | 12 queries + (2-4 mutations/validated task)     | Every 2 minutes (via dispatcher) | No                     | Yes                    | 10-20                   |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py` | 1 query                                         | Every 15 minutes   | No                     | Yes                    | 4                       |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/kai_delta_dispatcher.py` | 1 query                                         | Every 15 minutes   | Yes (delta cache)      | Yes                    | 4                       |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/ned_delta_dispatcher.py` | 1 query                                         | Every 15 minutes   | Yes (delta cache)      | Yes                    | 4                       |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/second_witness_agy_proxy.py` | 1 query                                         | Every 30 minutes   | Yes (delta cache)      | Yes                    | 2 (often 0)             |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_port_progress.py` | 1 query                                         | Every 30 minutes   | Yes (alerted states)   | Yes                    | 2                       |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/nightly_backlog_delta.py` | 1 query                                         | Daily (4 AM)       | Yes (delta cache)      | Yes                    | 1                       |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/action_item_extractor.py` | 5-20 queries + (N mutations)                    | Daily (00:30 UTC)  | Yes (commitment store) | Yes                    | 1-2                     |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/github_pr_monitor.py` | 1 query (for each of 4 repos) + (1 mutation/PR created) | Every 4 hours      | Yes (tracked PRs)      | Yes                    | 1-3                     |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/jules_session_watchdog.py` | 1 query                                         | Every 15 minutes   | No                     | Yes                    | 4                       |

**Total Estimated Requests/Hour (Baseline without optimizations): ~1100 - 1300 queries/hour, not accounting for burst dispatches.**

## Top Offenders Ranked

1.  **`/home/ubuntu/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py`** (900-1000 requests/hour)
    *   **Reason:** This is the core orchestrator, polling all 28 `agent:*` labels every 2 minutes. Each poll involves `get_issues_with_label` which makes a Linear GraphQL query. It also calls `setup_pipeline_issues` which further queries for `pipeline:*` triggers.
    *   **File/Line References:** `agent_dispatcher.py:2015` (`for label_name, config in AGENT_CONFIG.items()`), `agent_dispatcher.py:439` (`get_issues_with_label`), `agent_dispatcher.py:330` (`setup_pipeline_issues`).

2.  **`/home/ubuntu/.hermes/profiles/orchestrator/scripts/kai_callback_monitor.py`** (90 requests/hour)
    *   **Reason:** Runs every 2 minutes and performs 3 distinct Linear GraphQL queries to check various states related to Kai's sub-agents and their parent issues. No in-script caching to prevent repeat queries if no state has changed.
    *   **File/Line References:** `kai_callback_monitor.py:27` (query for `agent:kai-css/content/js`), `kai_callback_monitor.py:39` (query for `agent:agy` issues with Kai parent), `kai_callback_monitor.py:58` (query for `agent:kai` parent issues).

3.  **`/home/ubuntu/.hermes/profiles/orchestrator/scripts/comment_trigger_monitor.py`** (60-120 requests/hour)
    *   **Reason:** Runs every minute. Makes one query for `get_recent_comments` (first 40 issues updated in the last 2 hours), and then potentially 1-2 mutations for every triggered comment. The `processed_comments` state file provides some dedup, but the initial query always runs.
    *   **File/Line References:** `comment_trigger_monitor.py:57` (`get_recent_comments`), `comment_trigger_monitor.py:70` (`create_agy_task`), `comment_trigger_monitor.py:88` (comment reply).

## Concrete Optimization Plan

### 1. Refactor `agent_dispatcher.py` to use Prismatic Engine's native dispatcher

*   **Optimization:** Replace the legacy Python cron job with the `prismatic-engine`'s dispatcher, which has a more robust event loop, built-in dedup, and will eventually integrate the `LinearBudget` policy.
*   **Action:** Decommission the `e2f1a3b4c5d6 Unified Agent Dispatcher` cron job (`agent_dispatcher.py`). Start the `prismatic.dispatcher` (which defaults to polling every 30s) as the canonical dispatcher.
*   **Estimated Reduction:** This is a shift rather than a pure reduction in *requests* (the new dispatcher will still poll), but it centralizes the logic and makes future budget enforcement effective. The default 30s polling interval means the new dispatcher will run twice as often as the old one (120 cycles/hr vs 30 cycles/hr). The immediate benefit is **consolidation and enablement of central budget management**.

### 2. Implement a unified, batched `get_issues_with_labels` in Prismatic Linear Provider

*   **Optimization:** Instead of 28 separate `get_issues_with_label` queries in `agent_dispatcher.py` (and similar patterns in `kai_callback_monitor`), make a *single* batched GraphQL query to retrieve all issues with any `agent:*` label. Then, filter client-side.
*   **Action:** Modify `prismatic/providers/tasks/linear.py` to add a `get_issues_by_labels(labels: list[str]) -> list[Issue]` method that uses a single GraphQL query with a `labels: { name: { in: [...] } }` filter. All dispatcher loops should then use this batched method.
*   **Estimated Reduction:** Reduces `agent_dispatcher.py` from 28+ queries/cycle to **1 query/cycle**. This reduces its baseline from ~900 to **~30 requests/hour**. This is the single largest reduction.
*   **Risk Assessment:** Low. This is a read-only change. Requires careful GraphQL filter construction.

### 3. Add Delta Cache to `kai_callback_monitor.py`

*   **Optimization:** Introduce a delta cache (similar to `ned_delta_dispatcher.py` and `kai_delta_dispatcher.py`) to `kai_callback_monitor.py` so it only issues GraphQL queries when there are actual changes in Linear issue states or PRs.
*   **Action:** Integrate `delta_cache.DeltaCache` into `kai_callback_monitor.py` to track the "signature" of relevant Linear issues and open PRs. Exit silently if no changes are detected.
*   **Estimated Reduction:** Reduces `kai_callback_monitor.py` from 90 requests/hour to **~5-10 requests/hour** (only when changes occur).
*   **Risk Assessment:** Low. Read-only, proven pattern.

### 4. Optimize `comment_trigger_monitor.py` query and polling interval

*   **Optimization:** The `get_recent_comments` query fetches the *first 40 issues updated in the last 2 hours*, regardless of whether they have relevant comments. This is too broad. Narrow the query to issues with comments *containing trigger phrases* AND increase the polling interval.
*   **Action:**
    *   Change `comment_trigger_monitor.py` to use a more targeted GraphQL filter for comment bodies or issue descriptions if Linear's API supports it directly. If not, reduce the `first` parameter and the `updatedAt` window.
    *   Increase the cron interval from 1 minute to 5 minutes.
*   **Estimated Reduction:** Reduces `comment_trigger_monitor.py` from 60-120 requests/hour to **~10-20 requests/hour**.
*   **Risk Assessment:** Medium. Changing query logic might miss some edge cases. Increasing interval might delay trigger detection slightly.

### Estimated Total Requests/Hour AFTER Optimizations:

*   `agent_dispatcher.py` (Prismatic Dispatcher): ~30 requests/hour (after batching)
*   `comment_trigger_monitor.py`: ~10-20 requests/hour
*   `kai_callback_monitor.py`: ~5-10 requests/hour
*   Other existing scripts: ~15-20 requests/hour (already optimized or low frequency)

**New Estimated Total: ~60-80 requests/hour.** This is well within the 2,500 requests/hour limit.

## Risk Assessment

*   **Low Risk:**
    *   **Consolidating to Prismatic Dispatcher:** This is primarily a migration to a more robust platform. The new dispatcher itself requires rate-limit enforcement.
    *   **Batched `get_issues_with_labels`:** Read-only change to the Linear provider. Requires thorough testing of the GraphQL query, but overall low risk.
    *   **Adding Delta Cache to `kai_callback_monitor.py`:** Proven pattern, read-only impact.
*   **Medium Risk:**
    *   **Optimizing `comment_trigger_monitor.py` query:** The current `get_recent_comments` is broad. Narrowing it might miss certain comment triggers if the filter is too aggressive. Careful testing is needed to ensure no triggers are missed. Increasing the interval delays responsiveness.

---
