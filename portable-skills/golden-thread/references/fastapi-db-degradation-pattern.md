# FastAPI DB Degradation Pattern

## Problem

When building FastAPI services that should work with or without a database, a common mistake is checking DB availability at module load time:

```python
# WRONG — engine creation doesn't actually connect to the DB
engine = create_async_engine(DATABASE_URL)  # always succeeds
_DEV_MODE = async_session_factory is None   # always False if engine created
```

`create_async_engine()` only validates the URL format — it never opens a connection. The engine object exists,`async_session_factory` is not None, but `await session.execute(...)` will fail with a connection error when PostgreSQL isn't running.

## Solution

Check DB availability at **runtime** with a real query, and cache the result:

```python
from typing import Optional
from sqlalchemy import func, select

_db_available: Optional[bool] = None

async def _db_is_available() -> bool:
    """Check if PostgreSQL is actually reachable (not just configured)."""
    global _db_available
    if _db_available is not None:
        return _db_available

    if async_session_factory is None:
        _db_available = False
        return False

    try:
        async with async_session_factory() as session:
            await session.execute(select(func.now()))
        _db_available = True
        return True
    except Exception:
        _db_available = False
        return False
```

Usage in route handlers:

```python
@router.post("/register")
async def register(body: RegisterRequest):
    if not await _db_is_available():
        # In-memory fallback
        _memory_store[key_hash] = {...}
    else:
        # PostgreSQL path
        async with async_session_factory() as session:
            ...
```

## Why This Works

- `async_session_factory is None` still catches the case where the engine couldn't even be created (bad URL, missing driver)
- `SELECT now()` catches the case where the engine exists but PostgreSQL isn't reachable
- The result is cached in `_db_available` so subsequent calls are instant
- Both paths use the same `await` pattern — the route handler doesn't need to know which backend is active

## When To Use

- Any FastAPI service that should start and serve health checks even when its database is down
- Services that use in-memory storage during development and PostgreSQL in production
- APIs that want zero-downtime during DB restarts (routes that don't need the DB keep working)

## Pitfall: Don't Use Module-Level Flags

The approach of `_DEV_MODE = engine is None` at module load time only catches the case where the URL is so malformed that even the engine creation fails. In practice, PostgreSQL being down doesn't prevent engine creation — it only prevents actual queries.

## Real Example

The HD Engine API (`hd-platform/api/routes/keys.py`) uses this pattern. On the homelab host (no PostgreSQL installed), `create_async_engine()` succeeds with the default connection string. The static `_DEV_MODE` flag was `False`, so the register endpoint tried to connect to PostgreSQL and returned 503. Switching to the runtime `_db_is_available()` check fixed it — the API now uses in-memory storage transparently.
