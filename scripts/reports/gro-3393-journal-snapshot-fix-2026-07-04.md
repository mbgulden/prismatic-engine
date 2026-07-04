# GRO-3393 — Hermes daily journal snapshot fix verification

Date: 2026-07-04
Agent: Ned

## Root cause

`prismatic/journal.py::extract_golden_thread_summary()` assumed `project-registry.json` `_last_sync` was always a dict. Live profile data can contain a scalar/string `_last_sync`, which caused `AttributeError: 'str' object has no attribute 'get'` in cron job `ce3dd849ede5` (`Hermes daily journal snapshot`).

## Change

- `prismatic/journal.py` now checks `isinstance(sync, dict)` before reading Linear/GitHub counters.
- Non-dict `_last_sync` values render as `Last sync: ...` instead of crashing.
- `tests/test_journal.py` covers string-shaped `_last_sync`.
- `docs/journal-continuity-engine-phase1.md` documents runtime schema tolerance for journal snapshots.

## Live evidence

```text
python3 -m pytest tests/test_journal.py -q
..... [100%]
5 passed

python3 -m py_compile prismatic/journal.py tests/test_journal.py
# exit 0

PYTHONPATH=/tmp/ned-gro-3393 /home/ubuntu/.local/bin/prismatic-journal-snapshot --force
{
  "changed": true,
  "signals": 219,
  "today_file": "/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-04.md",
  "lines": 38
}
```

## Disposition

Ready for review. This is the same root-cause family as the duplicate daily-journal snapshot tickets (GRO-3384/GRO-3387/GRO-3390/GRO-3393); this pass fixes/verifies the live failure path for GRO-3393.
