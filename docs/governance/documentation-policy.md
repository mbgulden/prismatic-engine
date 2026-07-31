# Documentation Governance Policy

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-21

## Purpose

Prismatic is intended to remain understandable and maintainable across long-running agent work. Documentation is part of the verification system, not decorative prose.

## Required metadata

Canonical documents state status, owner, and last-verified date. Decisions additionally state decision status and supersession. Historical reports remain immutable except for a non-destructive metadata banner or index entry.

## Classes

- **Normative:** defines required behavior, schema, policy, ownership, or evidence.
- **Reference/read model:** explains or visualizes normative stores without replacing them.
- **Research:** informs decisions; claims retain evidence-quality labels.
- **Historical:** records what was observed or decided at a past revision.

## Change contract

Every material change must identify:

- why the component exists;
- owner and system of record;
- inputs, outputs, state transitions, and failure behavior;
- agent-level and systemic verification;
- exact-artifact/evidence requirements;
- rollback/recovery and retention;
- affected ADR and OKF entries.

## Prohibited ambiguity

Do not call a dashboard, chat message, producer result, mutable log, branch name, provider check/status icon, or unpinned external path the source of truth. They may be operator views or evidence references only. Source adapters bind source identity and acquisition but cannot authorize a merge. GitHub Actions and other CI execution environments are verifier backends only when explicitly approved, and their output is trusted only through the accepted provider-neutral receipt contract.

## Supersession

A replacement must name what it supersedes, preserve historical evidence, update `docs/index.md` and applicable ADR/OKF records, and pass `scripts/validate_okf_docs.py`.
