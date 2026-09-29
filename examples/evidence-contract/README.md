# Evidence contract example

`run.py` demonstrates `prismatic/execution_evidence.py` — the rule that an
agent saying "done" is not evidence:

- a `SELF_REPORTED` claim is **rejected** by `done_gate`
- the same claim backed by a real `CommandEvidence` run is **accepted**

```bash
python3 examples/evidence-contract/run.py
```

Expected output ends with `EVIDENCE_CONTRACT_EXAMPLE_OK`.
