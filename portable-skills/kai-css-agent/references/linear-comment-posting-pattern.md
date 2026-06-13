# Linear Comment Posting — Special Characters in GraphQL

## Problem

When posting Linear comments via `curl` + GraphQL, comment bodies that contain **backticks**, **curly braces**, **CSS syntax** (`:root`, `--blue-600`, `:focus-visible`), or **special characters** cause GraphQL parsing failures with `"Syntax Error: Unterminated string"`.

This affects ALL agents that post rich Linear comments (Kai-CSS, Kai, Fred, Ned, AGY, nudge executor) because bash interprets these characters before the JSON reaches the API.

## Root Cause

Two issues overlap:
1. **bash interprets backticks** — backticks inside the JSON string are treated as command substitution by the shell
2. **GraphQL's JSON parser is strict** — unescaped newlines, braces, and special chars in the `body` field break the string literal

Writing the query inline in bash (`-d '{...}'`) means every special character in the comment body must be JSON-escaped AND shell-escaped. This is error-prone.

## Solution: Python Script

Always write the comment body to a Python script and execute it via `terminal('python3 /tmp/script.py')`. Python handles JSON escaping correctly.

### Template

```python
#!/usr/bin/env python3
import json, os, subprocess

body = (
    "**Kai-CSS self-review: PASS**\n\n"
    "What changed:\n"
    "- Added `--blue-600: #006699` CSS custom property to `:root`\n"
    "- Added `:focus-visible { outline: 3px solid var(--blue-600); }`\n"
    "to all CTA button selectors\n\n"
    "Checks:\n"
    "- 1 file changed, tight scope\n"
    "- No `!important`, no nav files, no brand color changes\n"
)

query = {
    "query": f'mutation {{ commentCreate(input: {{ issueId: "{ISSUE_ID}", body: {json.dumps(body)} }}) {{ success }} }}'
}

key = os.environ["LINEAR_API_KEY"]
result = subprocess.run(
    ["curl", "-s", "-X", "POST", "https://api.linear.app/graphql",
     "-H", "Content-Type: application/json",
     "-H", f"Authorization: {key}",
     "--data-raw", json.dumps(query)],
    capture_output=True, text=True
)
print(result.stdout)
```

### Key details

- **`json.dumps(body)`** handles all escaping — backticks, braces, newlines, emoji — correctly
- **`json.dumps(query)`** wraps the entire payload so no bash interpolation occurs
- **Use `subprocess.run()`** not `os.popen()` — avoids shell interpretation entirely
- **Write to `/tmp/`** and run with `python3` — no chmod needed for write-then-execute via `terminal()`

## When the body is short (no special chars)

For simple status updates with no special characters, the inline `curl` approach works fine:

```bash
curl -s -X POST https://api.linear.app/graphql \
  -H "Content-Type: application/json" \
  -H "Authorization: $LINEAR_API_KEY" \
  -d '{"query": "mutation { commentCreate(input: { issueId: \"ID\", body: \"Kai-CSS executing this - started\" }) { success } }"}'
```

Use this for **Step 1** ("executing this — started") comments. Use the Python script for **Step 2** (self-review with CSS code blocks) and **Step 3** (AGY handoff with code references).

## Pitfalls to avoid

- ❌ **Backticks in bash strings** — bash treats backticks as command substitution. Never put backticks inside `-d '{...}'` payloads
- ❌ **Emoji in bash strings** — emoji (✅, ❌) in bash strings can cause encoding issues. Use plain text markers (PASS/FAIL) instead
- ❌ **Literal newlines in bash strings** — `-d '{"body": "line1\nline2"}'` breaks the JSON. Use `\n` if you must stay in bash, but prefer Python for multi-line bodies
- ❌ **`os.popen()` with f-strings** — `os.popen(f'curl ... {var}')` still passes through bash and can interpret special characters in `var`. Use `subprocess.run()` with `args` array instead
- ❌ **Writing the Python script via heredoc in terminal** — the heredoc itself may be interpreted by bash. Use `write_file()` to create the script, then `terminal('python3 /tmp/script.py')` to run it

## Tested working (Jun 2026)

The pattern above was tested successfully with a comment body containing:
- Backticks: `` `--blue-600`, `:root`, `:focus-visible` ``
- Emoji-adjacent: `✔`
- Newlines: 10+ line multi-paragraph comment
- Special chars: `#`, `.`, `[`, `]`, `(`, `)`
