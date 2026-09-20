---
title: Workspace Acceptance Protocol Specification
version: v1
workstream: WA-9
status: accepted
last_verified: 2026-08-01
---

# Workspace Acceptance Protocol Specification V1

This specification documents the acceptance protocol enforced by the **Curated Workspace Plugin** (`prismatic/workspace/acceptance.py`).

## Overview

To ensure high documentation quality across live Prismatic Engine deployments, every markdown file served by the workspace plugin passes through an automated acceptance validation pipeline.

## Acceptance Criteria (§6.1)

1. **Frontmatter Present**: Document must begin with valid YAML frontmatter (`--- ... ---`).
2. **Frontmatter Schema Valid**: `status` $\in$ {`proposed`, `accepted`, `deprecated`, `superseded`}. If `last_verified` is present, it must be an ISO8601 date string.
3. **H1 Heading Present**: First `# heading` must exist and match `frontmatter.title` (or title must be explicitly defined).
4. **Relative Markdown Links**: All internal relative links `[text](path.md)` must resolve to existing files in the release directory.
5. **Linear Issue Reference**: If `linear_issue` is set, it must match `GRO-XXXX` format.
6. **Deprecation Check**: `status != "deprecated"`. Marked deprecated documents fail acceptance.
7. **Superseded Chain**: If `status == "superseded"`, `superseded_by` must point to a valid target document ID.

## Frontmatter Skip Reasons Override

Documents can bypass specific checks by listing skip reasons in frontmatter:

```yaml
---
title: Research Notes
type: research
status: accepted
acceptance:
  skip_reasons:
    - missing-frontmatter
    - skip-link-check
---
```

Available skip reason tags:
- `missing-frontmatter`
- `invalid-status`
- `missing-title`
- `skip-link-check`
- `invalid-linear-issue`
- `deprecated-doc`
- `missing-replacement`

## Operator Manual Override Runbook

If a document fails automated acceptance checks but needs to be displayed to operators (e.g. legacy research notes or draft specs):

1. **Frontmatter Override Tag**: Add `acceptance.skip_reasons` tags directly into document frontmatter.
2. **REST API Manual Override**:
   ```bash
   curl -X POST "http://localhost:8000/api/workspace/acceptance/override?path=docs/my-doc.md&reason=Approved+by+Operator"
   ```
3. **Share Link Revocation**:
   ```bash
   curl -X DELETE "http://localhost:8000/api/workspace/share/<TOKEN>"
   ```

## Execution Engine

Implemented in `prismatic.workspace.acceptance.AcceptanceProtocol`. Validation runs statelessly and idempotently with a 60-second tree cache.
