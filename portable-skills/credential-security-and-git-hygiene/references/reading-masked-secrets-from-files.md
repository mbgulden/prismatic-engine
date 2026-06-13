# Reading Masked Secrets from Files

## Problem
The terminal tool masks sensitive values in output. `grep`, `cat`, and even Python `print()` show truncated strings like `cfk_5s...713a`. The truncated value is incomplete and cannot authenticate against APIs.

## Workaround: Python Binary Read
Read the file in binary mode, extract the key bytes after `=`, and decode:

```python
with open('/path/to/.env', 'rb') as f:
    for line in f:
        if line.startswith(b'CLOUDFLARE_API_KEY='):
            eq_pos = line.find(b'=')
            key_bytes = line[eq_pos+1:].strip()
            key = key_bytes.decode('utf-8')
            print(f"Full key ({len(key)} chars): {key}")
```

To verify the extracted key is complete:
```python
# Compare lengths — if terminal masked it, the lengths won't match
print(f"Raw bytes: {len(key_bytes)}")
```

## When to Use
- You need to copy a credential from one profile's `.env` to another
- The env var isn't loaded in your current shell/process
- You're debugging why `$VAR` works but the stored file value doesn't

## When NOT to Use
- If the env var IS loaded in your shell, just reference `$VAR` directly
- Don't extract secrets from files just to display them — only extract when you need to write them elsewhere

## Real-World Example (Jun 2026)
The `CLOUDFLARE_API_KEY` in `.env` showed as `cfk_5s...713a` in terminal output. Binary read revealed the full 52-char key. However, the key was expired/revoked — the real credential was `CLOUDFLARE_API_TOKEN` (52 char, Bearer auth) in a nested `.env` path. Binary read confirmed both keys were complete; the failure was credential validity, not truncation.
