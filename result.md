# GRO-3207: Bus WAL checkpoint (prevent unbounded WAL growth)

## Summary of Changes
SQLite databases operating in WAL mode can suffer from unbounded WAL file growth if checkpoints are not periodically executed to commit WAL changes back to the main database file.
To resolve this, we have:
1. Implemented periodic SQLite WAL checkpointing in `EventBus` (`prismatic/gateway/event_bus.py`).
2. Added `start_checkpoint_task()`, `stop_checkpoint_task()`, `_checkpoint_loop()`, and `_checkpoint_db()` methods to the `EventBus` class.
3. Configured `_checkpoint_db` to run `PRAGMA wal_checkpoint(TRUNCATE)` using the thread-safe `_sqlite_lock`.
4. Wired the `start_checkpoint_task` call into the gateway server startup hook (`prismatic/gateway/server.py:startup()`) and the `stop_checkpoint_task` call into the shutdown hook (`prismatic/gateway/server.py:shutdown()`).
5. Added unit tests in `tests/test_event_bus_checkpoint.py` verifying method existence, periodic task lifecycle, and successful WAL file truncation to 0 bytes.

## Code Changes
### 1. event_bus.py (`prismatic/gateway/event_bus.py`)
- Initialized `_checkpoint_task` to track the asyncio task.
- Added public API: `start_checkpoint_task` and `stop_checkpoint_task`.
- Added internal methods: `_checkpoint_loop` (sleeping 3600s/1 hour iteratively) and `_checkpoint_db` executing `PRAGMA wal_checkpoint(TRUNCATE)`.

### 2. server.py (`prismatic/gateway/server.py`)
- Started the checkpoint task on gateway startup:
  ```python
  bus = get_event_bus()
  bus.start_checkpoint_task()
  ```
- Stopped/cancelled the checkpoint task on gateway shutdown:
  ```python
  bus = get_event_bus()
  bus.stop_checkpoint_task()
  ```

## Test Evidence
All unit tests in `tests/test_event_bus_checkpoint.py` passed successfully:
```
tests/test_event_bus_checkpoint.py::test_checkpoint_methods_exist PASSED
tests/test_event_bus_checkpoint.py::test_checkpoint_task_lifecycle PASSED
tests/test_event_bus_checkpoint.py::test_checkpoint_db_execution PASSED
3 passed in 0.19s
```

## Verification
- Verified that the `PRAGMA wal_checkpoint(TRUNCATE)` query successfully forces all WAL frame content to the main database file and reduces the WAL file size on disk to `0` bytes (verified via TDD assertion `assert wal_path.stat().st_size == 0`).
