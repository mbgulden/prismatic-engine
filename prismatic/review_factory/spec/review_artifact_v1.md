---
title: Review Artifact Schema
version: v1
rf_slice: jev-validation-loop
status: frozen
---

## Overview
`ReviewArtifact` is the portable unit of the Jev validation loop: every harness
adapter (L0) produces exactly this record, and the deterministic floor (L1),
the judgment layer (L2), and the learn loop consume nothing else. It is
content-addressed — `artifact_id` is the sha256 of the canonical JSON of every
other field — and write-once: a stored artifact is never mutated. `artifact_id`
is the join key for verification receipts, verdicts, and learn-loop outcomes.

## Schema
- `schema_version` (str, const `artifact-v1`)
- `artifact_id` (str, sha256 hex of the canonical JSON of all fields below it)
- `harness_id` (enum: `claude-code-cli` | `gemini-cli` | `codex-cli` | `hermes` | `github-pr` | `manual`)
- `harness_run_id` (str | null — opaque id from the harness, for traceability)
- `submitted_at` (str, UTC ISO timestamp)
- `intent` (object):
  - `plan_ref` (str | null — path/URL to the plan this work implements)
  - `brief` (str | null — the task text the agent was given)
  - `goals` (list[str] — structured acceptance criteria, machine-readable)
- `diff` (object):
  - `base_tree` (str | null — sha256 of the base tree)
  - `head_tree` (str | null — sha256 of the head tree)
  - `unified` (str | null — diff text, size-capped; truncated payloads carry the `... [diff truncated: exceeded size cap] ...` marker)
  - `files` (list of `{path, change_type, lines_added, lines_removed}`; `change_type` in `added` | `modified` | `deleted` | `renamed`; line counts are non-negative integers)
- `checks` (list of `{name, exit_code, log_sha256, ran_at}` — deterministic results, not claims; `log_sha256` / `ran_at` nullable)
- `prior_receipts` (list[str] — receipt ids of earlier verification attempts)
- `novelty_context` (object): `first_seen_paths` (list[str] — feeds novelty.py)
- `explicit_gaps` (list[str] — dotted paths of every field emitted as `null`)

## Canonical JSON
`json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
over all fields except `artifact_id`, encoded UTF-8, then sha256. Byte-identical
for identical logical content on any platform.

## Contract & Invariants
- **Boundary validation (fail-closed):** `artifact.py::validate()` rejects
  unknown fields (at any depth), oversize diffs (1 MiB cap on `unified`,
  10,000-file cap, 1,000-check cap, 1,000-goal cap), type violations, and
  `artifact_id` mismatches.
- **No fabrication:** any field an adapter cannot produce is emitted as
  `null` AND listed under `explicit_gaps`. A null field not listed under
  `explicit_gaps` — or an `explicit_gaps` entry naming a non-null field —
  fails validation. Adapters never fabricate values.
- **Immutability:** artifacts are write-once and append-only; re-storing the
  same `artifact_id` is a no-op.
- **Storage:** append-only JSONL at `~/.prismatic/audit/review-artifacts.jsonl`
  (same convention as the other review-factory audit logs).

## Versioning
v1 is frozen. v2 is a new file, never an edit; `schema_version` selects the
parser and both versions coexist.
