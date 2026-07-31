---
name: prismatic-worktree-safety
description: Use this skill before creating, selecting, cleaning, archiving, or deleting Git worktrees used by Prismatic or AGY, especially when ownership or dirty state is uncertain.
---

# Prismatic Worktree Safety

## Default rule

Ambiguity means preserve. A worktree is disposable only when Git and runtime evidence prove it.

## Before work

1. Inventory repository root, branch, head, status, and registered worktrees.
2. Confirm the selected worktree is not the durable runtime checkout, an active release, or another agent's assigned workspace.
3. Preserve unrelated dirty changes. Never reset or clean them to obtain a convenient base.
4. Create a new clean worktree from the exact approved base when isolation is needed.

## Cleanup gate

Do not remove a worktree when any of these are true:

- tracked modifications, staged changes, or untracked files exist;
- unique or unpushed commits exist;
- an active process, lease, admission, receipt, or deployment references it;
- it is canonical, protected, or production-bound;
- ownership or purpose is unclear.

Before mutation, write a manifest containing path, branch, head, status, ahead/behind state, unique commits, and the planned action. Destructive dirty cleanup requires explicit confirmation naming the exact path and preserved destination.

## After cleanup

Re-list registered worktrees, confirm only approved paths changed, verify any archive is readable, and report what was intentionally retained. Never use broad cleanup commands as a substitute for classification.
