# Proof policy: missing `file:` targets

The SwarmProof verification barrier runs at Step 4 of every hypervisor
transaction on a `file:` resource. The `proof_on_missing` policy controls
what happens when the target file is missing or was deleted before the
barrier runs.

## Default: fail closed (`deny`)

```python
hypervisor = PrismaticHypervisor(
    journal_db_path="...",
    ledger_db_path="...",
    proof_on_missing="deny",  # this is the default
)
```

A missing target **fails verification** and the transaction cannot commit.
The kernel hands the missing target to `SwarmproofBridge.verify_and_settle`,
which reports a `FileNotFoundError` diagnostic, and the kernel raises
`SwarmProof Invariant Failure`. The attempt is named in the audit ledger as
a `PROOF` node with:

```json
{"status": "PROOF_TARGET_MISSING", "policy": "deny", "target": "<path>", "tx_id": "<id>"}
```

This closes the old bypass, where a missing target skipped verification
entirely and the transaction could commit unverified. It also covers the
TOCTOU case: a file deleted between the kernel's existence check and
verification fails inside the bridge's own existence check.

## Override for create-flows (`allow`)

Transactions that legitimately *create* the target (the file does not exist
yet at barrier time) can opt out explicitly:

```python
hypervisor = PrismaticHypervisor(
    journal_db_path="...",
    ledger_db_path="...",
    proof_on_missing="allow",
)
```

With `allow`, a missing target skips verification exactly as before — but
the skip is **auditable**: the kernel appends a `PROOF` ledger node with
`status: PROOF_TARGET_MISSING` and `policy: allow` (and logs the skip).

Any other value for `proof_on_missing` raises `ValueError` at construction.

## Choosing

- Creating files, scaffolding new modules, first-run setup flows: `allow`.
- Everything else: leave the default `deny`.

The policy is per hypervisor instance. If one workload creates files and
another must never skip verification, construct two hypervisors with
separate journals/ledgers.
