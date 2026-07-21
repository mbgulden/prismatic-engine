# Evidence Retention Contract

**Status:** Canonical baseline
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-21

## Principles

Evidence must be revision-bound, value-safe, recoverable, and sufficient to replay the acceptance decision. A mutable path without a digest is not durable evidence.

## Minimum classes

| Class | Examples | Minimum disposition |
|---|---|---|
| Promotion | packet, source, gate, proof manifest | Retain with merged task lineage |
| Security | scanner result, policy decision, redacted finding | Mode-restricted; never retain matched secrets |
| CI/build | check run, wheel/container digest | Bind to exact revision/artifact |
| Runtime | deploy manifest, health/API/browser proof | Bind to release/runtime checkout |
| Historical failure | rejected candidate and review | Preserve enough to prove repair and prevent recurrence |

## Required metadata

Owner, creation time, task/revision, artifact digest, evidence digest, proof class, retention class, sensitivity, expiry/review date, supersession, and restore/replay instructions.

## Fail closed

Reject promotion when evidence is missing, mutable without a digest, secret-bearing, tied to a different revision, oversized beyond policy without an external retained artifact, or not reproducible under the declared environment.

## Open operational decisions

Exact retention durations, backup tiers, legal hold, GC, and restore frequency require an accepted ADR before production enforcement. Until then, merged promotion evidence must not be silently deleted.
