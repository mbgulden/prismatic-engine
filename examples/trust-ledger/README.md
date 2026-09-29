# Trust ledger example

`run.py` demonstrates `prismatic/review_factory/trust.py` — the append-only
audit trail behind earned autonomy. It records one `merge_completed` event,
reads it back, and prints the current tier.

The example uses a disposable database in a temp dir; it never touches
production ledger state.

```bash
python3 examples/trust-ledger/run.py
```

Expected output ends with `TRUST_LEDGER_EXAMPLE_OK`.
