# Run receipts — signed proof-of-done

A **run receipt** is the receipt an agent hands the user when a run finishes:
what it did, what it *actually verified* (exact commands and exit codes),
what it could not verify, and a **DONE / NOT DONE** verdict derived from the
done gate — not from the agent's word.

This is the layer that answers Michael's founding complaint: *"the engine
kept telling me it was done when it wasn't."* An agent can claim anything in
chat; a run receipt is signed, persisted, and the NOT-DONE case is
mechanically enforced: when verification fails, the receipt reads
**RECEIPT: NOT DONE** regardless of what the agent claimed.

## Building blocks

- `prismatic/verification/run_receipt.py` — build / sign / verify / persist /
  find / render. Mirrors the `prismatic/verification/merge_receipt.py` pattern
  (Ed25519 over canonical JSON; explicit unsigned state; JSONL persistence).
- It is a presentation layer over `prismatic/execution_evidence.py` — it does
  not replace the evidence contract, it makes it readable and signed.
- `prismatic/cli/receipt.py` — `prismatic receipt {show,list,issue,verify}`.
- Dashboard: the Run Receipts card in the gateway truth panels
  (`/api/gateway/truth`, panel `run_receipts`).

## Issuing a receipt

Receipts are issued explicitly — the prototype wires no automatic emission
into existing run paths (kept inert by default):

```bash
# After a run record exists:
prismatic receipt issue --run-id <run-id> --harness agy-cli --model m1

# Read it back:
prismatic receipt show latest
prismatic receipt show <receipt-id> --json

# Verify its Ed25519 signature:
prismatic receipt verify <receipt-id> --public-key ~/.prismatic/run-receipt.pub
```

`--run-id latest` accepts `latest`, a run id, or a receipt id.

## The verdict rule

The verdict comes from `prismatic.execution_evidence.ExecutionEvidence`'s
own `done_gate_result` field:

- `done` → **RECEIPT: DONE**
- anything else (`not_done`, `not_runnable`, `unknown`) → **RECEIPT: NOT DONE**

An agent that claims done while its check exits non-zero produces a receipt
that says NOT DONE. There is no override path; the demo proves it
(`scripts/demo_run_receipt.py`).

## Signing

Ed25519, canonical-JSON bytes, `schema_version: run-receipt/v1`:

- Key sources, in order: `PRISMATIC_RUN_RECEIPT_SIGNING_KEY` (PEM string),
  `PRISMATIC_RUN_RECEIPT_KEY_FILE` (path),
  `~/.prismatic/run-receipt-signing.key`.
- **No key configured → the receipt is issued explicitly UNSIGNED**
  (`signature_or_attestation: null`, an explicit non-claim is recorded, and
  the readable receipt prints an UNSIGNED warning). This is fail-safe, not
  fail-silent: the absence of a signature is loud.
- The verifier side never imports harness code; verification needs only the
  public key (`PRISMATIC_RUN_RECEIPT_PUBLIC_KEY` /
  `~/.prismatic/run-receipt.pub`).

## The readable receipt

`render_receipt_text()` prints one screen that a non-engineer can read:
verdict line, verifier, exact check rows with `[PASS]`/`[FAIL]` and exit
codes, named blockers, artifacts, wall time, cost, and the signature state.
Example:

```
======================================================================
PRISMATIC RUN RECEIPT — RECEIPT: DONE
======================================================================
  run:            demo-run-real
  agent:          demo-agent
  verifier:       prismatic-run-receipt / fixture verifier (real run)
  status:         verified   | done gate: done
  harness/model:  demo-harness / local

  exact checks:
    [PASS] python3 scripts/verify_execution_evidence_contract.py --output-dir ... --clean (exit=0)

  artifacts:
    - artifacts/evidence/latest/summary.json

  signature: ed25519 signed by key_id=demo-key-01
```

## Persistence

Receipts append to a JSONL log:

- Default: `~/.prismatic/run-receipts.jsonl` (or `$PRISMATIC_STATE_DIR`).
- Override: `PRISMATIC_RUN_RECEIPTS`.
- Rows are marker-tagged (`PRISMATIC_RUN_RECEIPT_OK`) so foreign rows are
  ignored on read; `find_run_receipts()` supports run-id/task-id/agent
  filters and `limit`.
- The dashboard truth probe reads at most the newest 100 receipts and is
  fail-open: no log → the panel shows "no data", never a 500.

## Boundaries (prototype)

- No automatic emission on existing run paths — every receipt is issued
  explicitly via `build_run_receipt()` / `from_run_record()` / the CLI.
- No harness imports: the module depends only on `execution_evidence`,
  `run_records` (for the bridge), and the merge-receipt attestation
  primitives.
- `done_gate` semantics are untouched; the receipt reports the gate, it does
  not redefine it.

## See also

- `docs/execution-evidence-contract.md` — the evidence contract underneath.
- `prismatic/verification/merge_receipt.py` — the signing pattern this mirrors.
- `scripts/demo_run_receipt.py` — the real + adversarial end-to-end demo.
