# Pull Request Template: Linear API Rate Limit Impact Assessment

This template is **MANDATORY** for any pull request that touches, modifies, or adds a script, cron job, or provider that pings the Linear API.

---

## 📋 Rate Limit Impact Assessment

### 1. Estimates Table
Please estimate the Linear API request volume introduced or modified by this PR:

| Metric | Estimated Value | Notes / Calculation |
| :--- | :--- | :--- |
| **GraphQL Requests per Cycle (Req/Cycle)** | | *(e.g. number of queries + mutations run in one invocation)* |
| **Run Frequency (Cycles/Hour)** | | *(e.g. run every 1m = 60 cycles/hour, every 2m = 30)* |
| **Total Requests per Hour (Req/Hour)** | | *(Req/Cycle × Cycles/Hour)* |

*   **Maximum Hard Cap**: Total requests/hour for this script must stay strictly within its allocated budget in `prismatic/config/linear_budget.yaml`.

### 2. Implementation Check
- [ ] **LinearBudget Integration**: Have you wrapped all low-level API calls with `LinearBudget.check_and_consume()` or routed them through `LinearTaskProvider`?
- [ ] **Context Identification**: Is the `PRISMATIC_CURRENT_AGENT_NAME` environment variable set or is a unique agent/script name specified in the budget checks?
- [ ] **State Cache / Delta Cache**: Have you implemented a delta cache or similar mechanism to exit early if no changes have occurred?
- [ ] **Batched Queries**: If querying multiple issues or labels, have you used the batched query method (`get_issues_by_labels`) rather than looping individual queries?

---

## 🔍 Detailed Impact Analysis

*   **Peak Burst Potential**: *(Describe what happens when there is a high queue depth or bulk update event. How many requests will be sent?)*
*   **Failed API Call Resilience**: *(Explain how the cron job handles rate limit rejections or `LinearBudgetExhaustedError` exceptions. Does it gracefully resume or does it crash/retry?)*
*   **Testing Output**: *(Provide verification command outputs or logs demonstrating request counts during local testing.)*
