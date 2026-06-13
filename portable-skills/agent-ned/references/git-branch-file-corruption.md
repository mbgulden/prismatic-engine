# Git-Branch File Corruption — Corrupt Stub Detection

**Discovered:** Jun 12 2026, GRO-1479 (Credit Policy Engine)

## The Pattern

A file exists on disk at the expected path and `ls -la` reports a plausible byte count
(98 bytes), but the file's **actual content** is a git error message, not source code.

```bash
$ ls -la /home/ubuntu/work/agentic-swarm-ops/ops/credit_policy_engine.py
-rw-rw-r-- 1 ubuntu ubuntu 98 Jun 12 22:09 ops/credit_policy_engine.py

$ cat ops/credit_policy_engine.py
fatal: path 'ops/credit_policy_engine.py' exists on disk, but not in 'feat/GRO-1222-command-deck'
```

## Root Cause

The file was written via `write_file` tool (or similar) while the git repo was on a
non-master feature branch (`feat/GRO-1222-command-deck`). Git's internal index tracking
produced a write that placed the literal error message in the file instead of the
intended content. The file at the OS level exists but git refuses to acknowledge it
as versioned content.

## Detection (Critical — `ls` is NOT enough)

```bash
# WRONG — only checks existence, misses corruption:
ls -la /path/to/file.py

# RIGHT — checks actual content:
python3 -c "
with open('/path/to/file.py', 'rb') as f:
    content = f.read()
if content.startswith(b'fatal:'):
    print('CORRUPT — git error text in file')
elif len(content) < 200 and b'def ' not in content and b'class ' not in content:
    print(f'SUSPICIOUS — only {len(content)} bytes, no function/class definitions')
else:
    print(f'OK — {len(content)} bytes')
"
```

## When This Happens

- Prior agent session committed/wrote files while on a different branch
- After `git stash`, `git checkout`, or branch switching, files get orphaned
- The agent's completion comment describes the intended content, not the actual bytes
- `ls -la` shows a plausible size, masking the corruption

## Fix

1. **Remove the corrupt file:** `rm /path/to/corrupt_file.py`
2. **Switch to correct branch:** `git checkout main; git stash`
3. **Re-implement the file** from scratch (the reference source was also non-existent)
4. **Verify with content check** before posting completion

## Real Example (GRO-1479, Jun 2026)

- Prior Ned session posted: "✅ Credit policy engine implemented — 5 public API functions,
  syntax verified, 3 integration points wired into agent_dispatcher.py"
- `ls -la` showed 98 bytes — suspiciously small for a 5-function module
- `python3 -c "open(...).read()"` revealed the literal git error text
- Reference source at `prismatic-validation-pipeline/references/credit-policy-engine.py`
  also did NOT exist — the entire task was built on hallucinated foundation
- The `agent_dispatcher.py` import fell through to `CREDIT_POLICY_AVAILABLE = False` because
  the corrupt module couldn't be imported

## Prevention

- After any `write_file` for code: immediately verify with `cat <file> | head -5` or the
  Python content check above
- Pay attention to byte counts — a 5-function credit policy engine should be 5-15 KB,
  not 98 bytes
- Cross-reference `git log --oneline --grep="ISSUE_ID"` before trusting completion claims
