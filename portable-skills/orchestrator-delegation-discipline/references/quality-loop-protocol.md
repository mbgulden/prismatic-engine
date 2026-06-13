# Quality Loop Protocol — Applied Jun 12, 2026

## Rule: Only Fred Sets agent:done

The dispatcher's `AGENT_CONFIG` in `agent_dispatcher.py` enforces:
- ALL agents → `next_label: agent:fred` (Fred reviews before Done)
- Only `agent:fred` → `next_label: agent:done` (Fred is the verification gate)

No agent can mark their own work Done. Every task passes through Fred for artifact verification before closure.

## Why This Exists

Ned marked 5 BUILD issues (GRO-1314–1318) Done, but the pre-push hooks were never deployed to any repo. The artifacts existed in a branch but not where they should be. "Don't trust, verify" required this gate.

## Implementation

```python
# agent_dispatcher.py — AGENT_CONFIG
"agent:ned": {"next_label": "agent:fred"},   # Fred reviews Ned
"agent:agy": {"next_label": "agent:fred"},   # Fred reviews AGY
"agent:jules": {"next_label": "agent:fred"}, # Fred reviews Jules
"agent:kai": {"next_label": "agent:fred"},   # Fred reviews Kai
"agent:fred": {"next_label": "agent:done"},  # Only Fred → Done
```

## Fred's Verification Checklist

When an `agent:fred` issue arrives:
1. Read the Linear comment with the deliverable path
2. Verify the file exists on disk (`ls -la <path>`)
3. Check line count and spot-check content
4. If valid: move to Done with `agent:done`
5. If invalid: post what's missing, return to original agent

## Dispatcher AGY Launch (Fixed Jun 12)

The dispatcher's `launch_agy()` was rewritten from inline `/goal` prompts to the task file pattern:
- Writes `/tmp/agy-dispatch-{ISSUE}.txt` with full context (3000 chars)
- Tells AGY: "Do NOT post Linear comments, do NOT search for context"
- Detects task type from title
- Added `--add-dir /tmp --print-timeout 300s`
- Removed `--dangerously-skip-permissions` (killer flag)

Post-fix results: 56 issues launched, 0 errors, AGY producing 85-150 line deliverables.
