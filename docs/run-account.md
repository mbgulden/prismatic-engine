# Run account — "what did my agents do?"

`prismatic/run_account.py` renders one agent run's record as a human-readable
account: header (run, agent, task, times, duration), the verification commands
it executed (command → outcome → output excerpt), artifacts and files it
touched, and a verification footer (status, failure category, blocker,
done-gate result).

## Input contract

`render_run_account(record)` takes the exact dict shape produced by
`prismatic.gateway.server._run_record_to_dict` — i.e. what `GET /runs/{run_id}`
returns: `run_id`, `agent_name`, `issue_id`, `status`, `started_at`,
`completed_at`, `error_message`, `evidence` (the `ExecutionEvidence` dict with
`commands`, `artifacts`, `files_changed`, `summary`, `blocker`, ...),
`verification_status`, `verification_scope`, `failure_category`,
`cleanup_status`, `done_gate_result`, `done_gate_errors`.

## Guarantees

- **Traceable:** every timeline line cites its source as `<run_id>#cmd-<index>`.
  The account is a view over the record — no dropped commands, no invented lines.
- **Honest:** missing data renders as "not recorded". An unrecorded exit code is
  never mapped to ok/failed; an unparseable timestamp never invents a duration.
- **Deterministic:** same record → byte-identical output.
- **Read-only:** the module never touches the run store.

## Schema note

The trust ledger (`prismatic.review_factory.trust`) records merge/autonomy
events (merges, tier changes, brake pulls) — not per-agent activity. The
readable account renders from the **run record + execution evidence**, which is
where per-run commands, artifacts, and verification verdicts live. The two
surfaces are complementary, not interchangeable.

## Seam with run receipts (C1)

A signed run receipt (proof) should carry the story (account), not just the
verdict. Proposed seam: the receipt schema gains an `account` field holding
`render_run_account(... )`'s structured dict, or a `run_account_ref` if
receipts must stay small. Whichever PR merges second wires it.

## Follow-ups (not in this PR)

- Dashboard truth panel rendering `to_text()` output per run.
- `prismatic run-account <run_id>` CLI backed by `GET /runs/{run_id}`.
- A gateway endpoint returning the rendered account directly.
