# GRO-3384 journal snapshot silent-failure fix — 2026-07-03

## Issue

`Hermes daily journal snapshot` (`ce3dd849ede5`, Fred profile) was failing with:

```text
AttributeError: 'str' object has no attribute 'get'
prismatic/journal.py:474 extract_golden_thread_summary()
```

The live `project-registry.json` had a string-shaped `_last_sync`; the snapshot renderer assumed `_last_sync` was always a mapping.

## Fix

Updated `prismatic/journal.py` so `extract_golden_thread_summary()` tolerates non-dict `_last_sync` values by rendering them as a `Last sync:` summary line before the numeric Linear/GitHub counters.

Ned did not commit test-file changes because `tests/` is outside Ned's push lane; verification used the existing journal test suite plus an ad hoc live probe of the failing script path.

## Verification

Run from `/home/ubuntu/work/prismatic-engine` on 2026-07-03:

```text
$ python3 -m pytest tests/test_journal.py -q
.....                                                                    [100%]
5 passed in 0.09s
```

Live probe of the failing Fred script path:

```text
$ PYTHONPATH=/home/ubuntu/work/prismatic-engine python3 /home/ubuntu/.hermes/profiles/fred/scripts/journal_snapshot.py --force
{
  "changed": true,
  "signals": 785,
  "today_file": "/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-03.md",
  "lines": 47
}
```

## Notes

This fixes the code path behind [GRO-3384](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3384). The duplicate journal snapshot tickets ([GRO-3387](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3387), [GRO-3390](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3390), [GRO-3393](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3393)) should verify against the next scheduled run rather than receive separate code changes.
