# Linear API Exponential Backoff on 429 (GRO-3164)

## Problem
Under load, the Linear API returns `HTTP 429 Too Many Requests`. The previous implementation had no rate-limiting backoff mechanism at the HTTP request layer and failed immediately after 3 immediate retries or raised an error immediately.

## Fix
We implemented a resilient HTTP retry helper with exponential backoff on HTTP 429, supporting up to 10 retries, randomized jitter, and respect for rate limit headers (both `Retry-After` and `X-RateLimit-Requests-Reset`).

### 1. Unified Retry Helper
Created [prismatic/linear/retry.py](file:///home/ubuntu/work/prismatic-engine/prismatic/linear/retry.py) which contains the core request dispatcher:
- **Rate Limit Headers Resolution**: Checks the `Retry-After` header (seconds or HTTP date format) and the `X-RateLimit-Requests-Reset` / `x-ratelimit-reset` headers (epoch milliseconds/seconds), falling back to exponential backoff with jitter if headers are not present.
- **Exponential Backoff**: Generates backoff delay `2 ** attempt + random.uniform(0, 1)` to prevent synchronization.
- **Max Retries Gating**: Caps retries at 10 (11 total attempts).
- **Bubble Up**: Immediately bubbles up non-429 exceptions to keep client exception-handling unchanged.

### 2. Provider Integration
Modified all four urllib-based GraphQL endpoints to use the retry helper:
- [prismatic/providers/tasks/linear.py](file:///home/ubuntu/work/prismatic-engine/prismatic/providers/tasks/linear.py)
- [prismatic/dispatcher.py](file:///home/ubuntu/work/prismatic-engine/prismatic/dispatcher.py)
- [prismatic/journal.py](file:///home/ubuntu/work/prismatic-engine/prismatic/journal.py)
- [prismatic/gateway/event_handlers/dispatch_consumer_v3.py](file:///home/ubuntu/work/prismatic-engine/prismatic/gateway/event_handlers/dispatch_consumer_v3.py)

---

## Verification & Testing
Added a robust test suite at [tests/test_linear_retry.py](file:///home/ubuntu/work/prismatic-engine/tests/test_linear_retry.py) with the following tests:
1. `test_execute_linear_request_success` - Verifies no backoff/retry is run when request succeeds on 1st attempt.
2. `test_execute_linear_request_retry_then_success` - Verifies retrying multiple times on 429 then succeeding.
3. `test_execute_linear_request_retry_after_header` - Verifies the `Retry-After` header is parsed and respected.
4. `test_execute_linear_request_x_ratelimit_reset_header` - Verifies the `X-RateLimit-Requests-Reset` (epoch ms) header is parsed and respected.
5. `test_execute_linear_request_max_retries` - Verifies that the loop fails after exceeding 10 retries.
6. `test_execute_linear_request_other_error` - Verifies that non-429 errors (e.g. 500) fail immediately.

### Test execution output:
```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.14.1
collecting ... collected 6 items                                                              

tests/test_linear_retry.py ......                                        [100%]

============================== 6 passed in 0.14s ===============================
```
