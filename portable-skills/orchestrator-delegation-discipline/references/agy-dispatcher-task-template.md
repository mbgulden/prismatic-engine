# AGY Dispatcher Task File Template

This is the template used by `agent_dispatcher.py` `launch_agy()` and the Fred Autonomous Session to create task files for AGY. Adapted from the Jun 12 dispatcher fix.

## Template

```
TASK FROM LINEAR {ISSUE}: {TITLE}

TYPE: {research|review|generate|design}

DESCRIPTION:
{full issue description, up to 3000 chars}

RECENT COMMENTS:
- {comment 1}
- {comment 2}

INSTRUCTIONS:
1. Read this entire file to understand the task.
2. Do the work described in the DESCRIPTION above.
3. Save ALL output to /tmp/agy-dispatch-{ISSUE}-result.md
4. At the end, output a one-line summary: DONE: <what was accomplished>
5. Do NOT attempt to post Linear comments or change issue labels — you don't have API access.
6. Do NOT search around for issue details — everything you need is in this file.
```

## Critical Rules

- **Never tell AGY to post Linear comments** — it has no API key and will waste time trying
- **Never use `/goal` prefix in `--print` mode** — AGY treats it as a shell command
- **Keep the inline prompt short** — one sentence pointing at the task file
- **Always add `--add-dir /tmp`** — AGY cannot write files without it
- **Use `--print-timeout 300s`** — not `10m`, not bare `300`

## Invocation

```bash
cd /tmp && agy --print \
  "Read /tmp/agy-dispatch-{ISSUE}.txt. Follow ALL instructions exactly. Save results and output DONE." \
  --print-timeout 300s --add-dir /tmp
```
