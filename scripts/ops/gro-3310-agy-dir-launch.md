# GRO-3310 — AGY sandbox supervisor launch contract

Runtime source of truth: `/home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py`.

The event supervisor must launch AGY with a filesystem-scoped command, not by pasting signed payloads or file blocks over stdin.

Required launch shape:

```python
cmd = [
    AGY_BIN,
    "--dir", str(sandbox),
    "--print", prompt,
    "--dangerously-skip-permissions",
    "--print-timeout", PRINT_TIMEOUT,
    "--sandbox",
    "--model", model,
]
subprocess.Popen(..., stdin=None, cwd=str(sandbox), ...)
```

Rules:

1. `--dir` must be present on every AGY launch and relaunch.
2. The `--dir` value must be `str(sandbox)`.
3. `cwd` must also be `str(sandbox)`.
4. The bounded instruction prompt may reference `AGY_TASK.md` by path, but the supervisor must not stream the task as a signed stdin payload.
5. Legacy markers such as `INJECTED_VIA_STDIN`, `payload_data = json.dumps(...)`, and `proc.stdin.write(payload_data...)` should remain absent.

Verification:

```bash
python3 scripts/verify_gro_3310_agy_dir_launch.py
```

The verifier reads the live orchestrator script, parses it with `ast`, and asserts the launch/relaunch sites use `--dir str(sandbox)`, `--print prompt`, `stdin=None`, and `cwd=str(sandbox)`.
