---
type: Integration
title: Webhook Handler Test Pattern
description: How to write HMAC-validated webhook handler tests. The pattern used by `tests/test_webhook_handler.py` for `prismatic/gateway/server.py::linear_webhook`.
resource: okf/integrations/webhook-handler-test-pattern.md
tags: [integration, webhook, testing, hmac, fastapi, linear]
timestamp: 2026-06-19T12:30:00Z
linear_issue: GRO-2047
git_repo: mbgulden/prismatic-engine
git_path: tests/test_webhook_handler.py
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Webhook Handler Test Pattern

**Status:** Established with `tests/test_webhook_handler.py` (9 tests passing).
**Refs:** GRO-2047 (webhook handler), GRO-2048 (event-driven dispatch).

## Why this pattern

Webhook handlers sit at the public boundary of the engine. They:

1. Validate HMAC signatures from a third party (Linear).
2. Parse untrusted JSON payloads.
3. Decide event handling based on payload shape.
4. Trigger side effects (dispatch) that can't be rolled back easily.

A bad handler is a security hole (HMAC bypass → forge events), a budget leak (every event triggers a Linear lookup), or a silent failure (events dropped without trace). Tests must cover all four concerns.

## Test setup template

```python
import unittest, os, sys, json, hmac, hashlib, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


def _sign(body: bytes, secret: str) -> str:
    """Compute Linear-Signature header value (HMAC-SHA256 hex)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class TestLinearWebhook(unittest.TestCase):
    def setUp(self):
        # Use a temp dir for state so we don't pollute real prismatic_state
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        self.env_patches = [
            patch.dict(os.environ, {
                "PRISMATIC_LINEAR_WEBHOOK_SECRET": "test_secret_xyz",
                "PRISMATIC_STATE_DIR": str(self.state_dir),
            }),
        ]
        for p in self.env_patches:
            p.start()

        # Mock the dispatcher so we don't hit Linear API
        self.dispatch_patcher = patch("prismatic.dispatcher.dispatch_once")
        self.mock_dispatch_once = self.dispatch_patcher.start()
        self.mock_dispatch_once.return_value = {
            "dispatched": 1, "pipeline_setup": 0,
            "stale_killed": 0, "errors": 0,
        }

        from prismatic.gateway.server import app
        self.client = TestClient(app)

    def tearDown(self):
        self.dispatch_patcher.stop()
        for p in self.env_patches:
            p.stop()
        self._tmp.cleanup()
```

## Test categories (9 tests in canonical pattern)

### 1. Happy path

```python
def test_dispatches_with_agent_label(self):
    """agent:* label on Issue/update → direct dispatch."""
    event = {
        "type": "Issue", "action": "update",
        "data": {"identifier": "GRO-1", "labels": [{"name": "agent:agy"}]},
    }
    resp = self._post(event)
    self.assertEqual(resp.status_code, 200)
    self.assertEqual(resp.json()["status"], "dispatched")
    self.mock_dispatch_once.assert_called_once()
```

### 2. Queue path (no agent label)

```python
def test_queues_without_agent_label(self):
    """Events without agent:* labels go to SQLite queue."""
    event = {
        "type": "Issue", "action": "update",
        "data": {"identifier": "GRO-2", "labels": [{"name": "type:docs"}]},
    }
    resp = self._post(event)
    self.assertEqual(resp.status_code, 200)
    self.assertEqual(resp.json()["status"], "queued")
    self.mock_dispatch_once.assert_not_called()
    # Verify queue DB
    import sqlite3
    with sqlite3.connect(str(self.state_dir / "linear_webhook_queue.db")) as c:
        rows = c.execute("SELECT identifier FROM linear_webhook_queue").fetchall()
    self.assertEqual(len(rows), 1)
```

### 3. Security: bad HMAC

```python
def test_rejects_bad_signature(self):
    """401 when HMAC signature is wrong."""
    event = {...}
    resp = self._post(event, secret="wrong_secret")  # wrong secret in signing
    self.assertEqual(resp.status_code, 401)
    self.assertEqual(resp.json()["status"], "rejected")
    self.mock_dispatch_once.assert_not_called()
```

### 4. Security: missing signature

```python
def test_rejects_missing_signature(self):
    """401 when Linear-Signature header is missing."""
    resp = self._post(event, include_sig=False)
    self.assertEqual(resp.status_code, 401)
```

### 5. Bad input: malformed JSON

```python
def test_rejects_bad_json(self):
    """400 when payload is not valid JSON."""
    headers = {
        "Linear-Signature": _sign(b"not json", "test_secret_xyz"),
        "Content-Type": "application/json",
    }
    resp = self.client.post("/api/gateway/linear",
                            content=b"not json", headers=headers)
    self.assertEqual(resp.status_code, 400)
```

### 6. Resilience: dispatch failure → queue

```python
def test_queues_on_dispatch_failure(self):
    """If dispatch_once raises, the event is queued (don't lose it)."""
    self.mock_dispatch_once.side_effect = RuntimeError("linear rate limit")
    resp = self._post(event_with_agent_label)
    self.assertEqual(resp.status_code, 200)
    self.assertEqual(resp.json()["status"], "queued")
```

### 7. Filtering: non-Issue events

```python
def test_non_issue_events_are_queued(self):
    """Comment events (not Issue) bypass dispatch and go to queue."""
    resp = self._post({**event, "type": "Comment", "action": "create"})
    self.assertEqual(resp.status_code, 200)
    self.assertEqual(resp.json()["status"], "queued")
    self.mock_dispatch_once.assert_not_called()
```

### 8. Dev mode: no secret skips HMAC

```python
def test_no_secret_skips_hmac_validation(self):
    """If PRISMATIC_LINEAR_WEBHOOK_SECRET is unset, validation is skipped."""
    with patch.dict(os.environ, {"PRISMATIC_LINEAR_WEBHOOK_SECRET": ""}):
        resp = self._post(event_with_agent_label, include_sig=False)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "dispatched")
```

### 9. Idempotency: duplicate events

```python
def test_duplicate_event_is_idempotent(self):
    """Same body twice should only queue once (event_id dedup)."""
    event = _make_event(labels=["type:docs"])
    resp1 = self._post(event)
    resp2 = self._post(event)
    self.assertEqual(resp1.status_code, 200)
    self.assertEqual(resp2.status_code, 200)
    import sqlite3
    with sqlite3.connect(str(self.state_dir / "linear_webhook_queue.db")) as c:
        count = c.execute("SELECT count(*) FROM linear_webhook_queue").fetchone()[0]
    self.assertEqual(count, 1, f"expected 1 row, got {count}")
```

## Common pitfalls

### Pitfall 1: Mocking before env

`os.environ` patches must come **before** importing the FastAPI app, because `PRISMATIC_LINEAR_WEBHOOK_SECRET` is read at module load (or first request). The setUp pattern above does this correctly.

### Pitfall 2: Forgetting `Linear-Signature` in dev

If you test with `PRISMATIC_LINEAR_WEBHOOK_SECRET` unset, the handler skips HMAC validation. That's intentional for dev. In production, **always set the secret**.

### Pitfall 3: Polling the SQLite queue

Tests that read the queue DB must close the connection after reading — the handler holds a write lock during request handling. The `with sqlite3.connect(...)` pattern in the tests above handles this.

### Pitfall 4: Forgetting `httpx` deprecation warning

`fastapi.testclient` uses `starlette.testclient` which deprecates `httpx` in favor of `httpx2`. The warning is harmless but noisy. Suppress with pytest filter:

```python
# pyproject.toml
[tool.pytest.ini_options]
filterwarnings = [
    "ignore::DeprecationWarning:starlette.testclient",
]
```

## Why these 9 tests specifically

| Concern | Test |
|---|---|
| Functional correctness | `test_dispatches_with_agent_label` |
| Backpressure (queue path) | `test_queues_without_agent_label` |
| Authentication | `test_rejects_bad_signature`, `test_rejects_missing_signature` |
| Input validation | `test_rejects_bad_json` |
| Reliability (no event loss) | `test_queues_on_dispatch_failure` |
| Filtering (only Issue events dispatch) | `test_non_issue_events_are_queued` |
| Dev convenience | `test_no_secret_skips_hmac_validation` |
| Idempotency under retries | `test_duplicate_event_is_idempotent` |

## Reference implementation

Full source: `prismatic-engine/tests/test_webhook_handler.py` (9 tests, all passing in 1.64s).

## Adoption checklist for new webhook handlers

1. Mock `PRISMATIC_*_WEBHOOK_SECRET` env var in setUp.
2. Mock the side-effect target (dispatcher, queue writer, etc.).
3. Use `tempfile.TemporaryDirectory` for state, never touch real `prismatic_state/`.
4. Cover all 9 categories above — at minimum.
5. Verify queue DB writes when relevant (SQLite direct read).
6. Run `tests/test_webhook_handler.py -v` before every PR.

## Related docs

- `okf/standards/dispatch-architecture.md` — overall architecture this test pattern verifies
- `okf/standards/review-loop-canonical.md` — review-loop enforcement (test changes go through this)
- `okf/standards/agy-peer-review.md` — AGY reviews tests too