# Phantom Work Detection

When an issue describes removing, refactoring, or cleaning up something that was planned but **never actually implemented**.

## Trigger

Issue title/description contains one of:
- "Remove backward-compat" / "Remove legacy X"
- "Clean up Y after migration" / "Delete deprecated Z"
- "Remove shim for" / "Delete dead code for"
- "Remove X once all agents use Y"

These issues describe a **planned transition** — the feature was designed to have a backward-compat phase followed by removal. The removal step may exist as an issue before the backward-compat shim was ever committed.

## Detection Pattern

Do NOT assume the thing-to-remove actually exists. Verify:

```
1. Git log: git log --all --oneline -- <relevant-file.py>
   → Single commit = feature created, never had the shim
   → Multiple commits = check for "add shim" / "backward compat" in commit messages

2. Grep for the pattern: grep -rn '<pattern>' <project-dir>/ --include='*.py'
   → Zero matches = shim was never implemented

3. Read the send() method of FileSignalProvider (or equivalent):
   → Does it write to BOTH paths? If only one path, no shim exists
```

## GRO-760 Canonical Example (Jun 2026)

Issue GRO-760: "Remove backward-compat nudge shim once all agents read SignalPayload"

Description said: `FileSignalProvider.send()` currently writes to BOTH:
- /tmp/prismatic/nudge-{target} (SignalPayload JSON)
- /tmp/nudge-{target} (legacy bare text)

**Reality:** `FileSignalProvider.send()` only wrote to `/tmp/prismatic/nudge-{target}` from its first and only commit (GRO-678). The `/tmp/nudge-{target}` legacy path was **never implemented**.

Evidence:
- `git log --all --oneline -- prismatic/providers/signals/file.py` → 1 commit (GRO-678 — initial implementation)
- Grep for `/tmp/nudge-` in the file → zero matches
- Read the `send()` method → only one `os.rename()` call, writing to `self._dir / f"nudge-{target}"` where `_dir` is `/tmp/prismatic`

## Four Outcomes When Detecting Phantom Work

| Evidence | Action |
|----------|--------|
| The thing-to-remove was never implemented (our case) | Document findings. Clean up any stale legacy files on disk. Mark issue Done. |
| The thing-to-remove exists but is no longer referenced by any agent | Remove it, verify with tests/health checks, mark Done. |
| The thing-to-remove exists and agents still depend on it | Blocked — needs the agent migration to complete first. |
| The thing-to-remove existed, was already removed, but the issue stayed open | Post "already done by prior session" comment, move to Done. |

## Pitfalls

- ❌ **Assuming issue descriptions are factual** — They describe a DESIGNED state, not necessarily the CURRENT state. An issue may be created weeks before the shim was implemented, and the shim may never have been added.
- ❌ **Assuming git blame tells you the entire story** — Check git log for the SPECIFIC file that would contain the shim. A commit titled "add backward compat" may have touched a different file than expected.
- ❌ **Cleanup without verification** — Even if the shim never existed, check for stale files on disk that match the legacy pattern. In GRO-760, `/tmp/nudge-gro25-complete.py` (2571 bytes, Jun 6) was found and cleaned.
- ❌ **Rebuilding what's already correct** — If all agents already read from the new path (`/tmp/prismatic/nudge-*` via `PRISMATIC_NUDGE_DIR`), the migration is already done even though the removal step is still on Linear. Document this and move on.
