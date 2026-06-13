# Quality Loop — Lane Handoff Enforcement (Updated Jun 12, 2026)

Michael's directive: "There needs to be an improvement loop for each task no matter what." No agent may mark their own work Done.

## Rule

Only Fred sets `agent:done`. All other agents route through `agent:agy` for peer review FIRST, then `agent:fred` for final verification.

## Dispatcher Config (Updated Jun 12, 2026 — AGY review inserted)

In `agent_dispatcher.py` AGENT_CONFIG:

```
ned → agent:agy → agent:fred → agent:done
kai → agent:agy → agent:fred → agent:done
jules → agent:agy → agent:fred → agent:done
codex → agent:agy → agent:fred → agent:done
agy → agent:fred → agent:done
autobot → agent:fred → agent:done
fred → agent:done
```

This gives us: **Worker → AGY peer review → Fred verification → Done.**

## Review Loop

1. Worker (Ned/Kai/Jules/Codex) completes work → self-review
2. Dispatcher transitions label to `agent:agy`
3. AGY reviews the work (code, content, docs)
4. If AGY finds problems → label back to worker with feedback
5. If AGY approves → label to `agent:fred`
6. Fred verifies artifact exists on disk, spot-checks quality
7. Fred marks `agent:done`

## Fred's Verification Checklist

When reviewing an agent's work:
1. **Verify the artifact exists on disk** — `ls -la <path>`, check line count
2. **Verify it's not empty or broken** — spot-check content
3. **Post verification comment** on Linear
4. **Move to Done + agent:done label**

If artifact is missing or broken: move back to agent's lane with feedback comment.

## Pitfall: Ned's Premature Done

Ned marked GRO-1315 (pre-push hooks) Done, but the hooks didn't exist in any repo. Fixed by inserting AGY review between Ned and Fred — AGY verifies code changes before Fred signs off.
