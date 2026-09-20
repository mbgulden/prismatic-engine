---
name: prismatic-evidence-and-review
description: Use this skill when verifying AGY or other Prismatic work, preparing proof packets, reviewing exact artifacts, or deciding whether a candidate is acceptable.
---

# Prismatic Evidence and Review

## Proof layers

Keep these claims separate:

1. static/configuration inspection;
2. focused local tests;
3. canonical repository suite;
4. clean-room installed wheel or distribution proof;
5. independent exact-head review;
6. browser/rendered proof;
7. deployed runtime proof;
8. production/public proof.

Passing one layer does not imply another.

## Exact-artifact review

- Record commit and tree identity before review.
- Review the exact candidate, not a stale predecessor or mutable branch name.
- Recheck identity immediately before acceptance or merge.
- A stale but valid finding blocks use until repaired and independently re-reviewed clean.
- Producer self-review is useful evidence but is not independent verification.

## Compact proof packet

```text
COMMAND=<exact command or grouped summary>
RESULT=<PASS|FAIL|BLOCKED>
LOG=<durable path>
SCOPE=<paths/behavior covered>
AD_HOC_OR_CANONICAL=<ad-hoc targeted|canonical suite|clean-room distribution|browser|production>
NOT_CLAIMING=<explicit non-claims>
HEAD=<commit when applicable>
TREE=<tree when applicable>
MARKER=<stable marker>
```

## Fail closed

Do not call work complete when logs are missing, commands were not executed, the result marker is absent, changed paths exceed the frozen scope, or the reviewed artifact differs from the acceptance candidate. Preserve the candidate and report the exact repair gate.
