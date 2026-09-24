---
title: Review Verdict Schema
version: v1
rf_slice: jev-validation-loop
status: frozen
---

## Overview
`Verdict` is the replayable record of a review decision. It joins the
deterministic L1 outcome (authoritative), the optional L2 judgment (recorded,
never re-run), and the mechanical final outcome. The mechanical no-downgrade
enforcement itself lives in `judge.py` / `prismatic/jev/gates.py`
(`apply_jev_advice`); the verdict only records the enforced result.

## Schema
- `schema_version` (str, const `verdict-v1`)
- `verdict_id` (str, uuid4 — the verdict's own identity)
- `artifact_id` (str — join key to the `ReviewArtifact`)
- `policy_versions` (object): `{deterministic, calibration, judge}` — the spec
  versions in force at decision time (`calibration` is e.g.
  `merge_bar_calibration_v1`; `judge` is the judge id or `null`)
- `deterministic` (object): `{verdict: CLEAN|REPAIR|REJECT, receipt_ids: [str]}`
- `judgment` (null | object):
  - `judge` (enum: `jev` | `null`)
  - `decision` (enum: `CLEAR` | `PAUSE`)
  - `confidence` (number, 0..1)
  - `reasons` (list of `{question, finding, severity}` — typed reasons)
  - `trace_id` (str — the Jev trace this judgment came from)
- `final` (enum: `CLEAN` | `REPAIR` | `REJECT` | `ESCALATE`)
- `evidence_pointers` (object): `{artifacts: [str], receipts: [str],
  audit_rows: [str]}` — pointers into the artifact store, the receipt store,
  and audit logs
- `decided_at` (str, UTC ISO timestamp)
- `judgment_skipped` (str | null — why no judgment ran, e.g. `tier-0`;
  null when a judgment was recorded)
- `supersedes` (str | null — the `verdict_id` this verdict replaces; a fresh
  judgment is a new verdict, never a rewrite)

## Contract & Invariants
- **Replayability:** `verdict.py::replay(verdict_id)` re-runs the L1
  deterministic layer from the stored artifact and must reproduce
  `deterministic.verdict` byte-identically (compared on the canonical JSON
  form). The judgment is NOT re-run — the recorded judgment trace stands.
- **Judgment is pause-only and CLEAN-only:** a non-null `judgment` on a
  non-CLEAN deterministic verdict is a validation error (L2 only ever runs
  on deterministic-CLEAN). `PAUSE` on CLEAN escalates `final` to `ESCALATE`;
  judgment can never downgrade or override the deterministic floor. `final`
  equals `deterministic.verdict` for `REPAIR`/`REJECT`, and is `CLEAN` or
  `ESCALATE` for `CLEAN`. `ESCALATE` requires a recorded judgment with
  decision `PAUSE`.
- **Supersede, never rewrite:** `rejudge()` creates a new verdict with
  `supersedes` set to the old `verdict_id`. Verdicts are write-once.
- **Reference deterministic runner:** `default_deterministic_runner`
  derives the L1 outcome from the artifact alone — no checks → `REJECT`
  (fail closed); any non-zero check exit → `REPAIR`; all green → `CLEAN`;
  `receipt_ids` are the artifact's `prior_receipts`. Workstream C registers
  the real verifier/novelty/tiering stack as the replay runner.
- **Zero-AI path:** with no judge configured, the verdict records
  `judgment: null` (plus a `judgment_skipped` reason); the pipeline runs
  fully on the deterministic floor.
- **Storage:** append-only JSONL at `~/.prismatic/audit/review-verdicts.jsonl`
  (same convention as the other review-factory audit logs).

## Versioning
v1 is frozen. v2 is a new file, never an edit; `schema_version` selects the
parser and both versions coexist.
