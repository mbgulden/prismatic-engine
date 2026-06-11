# Prismatic Engine — Commit Prefix Convention

**Version:** 1.0  
**Date:** 2026-06-11  
**Phase:** 1 (Convention Layer)

---

## Standard Format

```
[AGENT] description (#ISSUE)
```

## Agent Prefixes

| Agent | Prefix     | Role                          | Example Commit                                          |
|-------|-----------|-------------------------------|---------------------------------------------------------|
| Fred  | `[Fred]`  | Orchestrator & Infrastructure  | `[Fred] Add pre-push hook for lane validation (#GRO-1215)` |
| Kai   | `[Kai]`   | Content Writer                 | `[Kai] Add mokulua islands tour page (#GRO-1215)`       |
| AGY   | `[AGY]`   | Designer & Researcher          | `[AGY] Design new landing page mockup (#GRO-1215)`      |
| Jules | `[Jules]` | PR Agent & Code Reviewer       | `[Jules] Fix null pointer in auth middleware (#GRO-1215)` |
| Ned   | `[Ned]`   | Code Execution & Task Agent    | `[Ned] Implement Phase 1 lane governance (#GRO-1215)`   |

## Rules

1. **Every commit MUST include the agent prefix.** This is enforced manually in Phase 1 and will be validated by pre-push hooks in Phase 2.

2. **Issue reference is strongly recommended.** Use `(#ISSUE-ID)` format (e.g., `(#GRO-1215)`).

3. **Description should be imperative and concise.** "Add" not "Added" / "Fix" not "Fixed".

4. **One logical change per commit.** Don't batch unrelated edits in a single commit.

5. **The agent prefix maps to the agent who authored the change.** If Fred merges Kai's branch, the merge commit can use `[Fred]`, but Kai's original commits in the branch must use `[Kai]`.

## Git Configuration

Each agent should set their committer identity:

```bash
# Fred
git config user.name "Fred (Hermes Swarm)"
git config user.email "fred@hermes-swarm.local"

# Kai
git config user.name "Kai (Hermes Swarm)"
git config user.email "kai@hermes-swarm.local"

# AGY
git config user.name "AGY (Hermes Swarm)"
git config user.email "agy@hermes-swarm.local"

# Jules
git config user.name "Jules (Hermes Swarm)"
git config user.email "jules@hermes-swarm.local"

# Ned
git config user.name "Ned (Hermes Swarm)"
git config user.email "ned@hermes-swarm.local"
```

## Validation (Phase 2+)

Future pre-push hooks will validate:
- Commit message starts with a recognized `[AGENT]` prefix
- The pushing agent's identity matches the commit prefix
- Branch prefix matches the agent's assigned branch convention

---

*This document is part of PRISMATIC_ENGINE.yaml Phase 1 governance. See repo root for the full lane assignment and locking protocol.*
