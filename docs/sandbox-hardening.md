# Sandbox hardening

The plugin sandbox manager applies three independent isolation layers when the host supports them:

1. OCI runtime isolation (`runsc`/gVisor when available, otherwise Docker/k3s/simulated fallback).
2. Docker/k3s resource limits plus best-effort Linux cgroup enforcement.
3. A deny-by-default seccomp profile and host-volume validation before mounts are passed to the runtime.

`CgroupEnforcer` is intentionally best-effort: local development, CI, and containers without writable cgroupfs log and continue instead of failing sandbox startup. Runtime limits remain the primary portable enforcement layer.

Run the hardening smoke tests with:

```bash
python3 scripts/test_sandbox_security.py
python3 -m pytest tests/test_gvisor_runtime.py tests/test_plugin_lifecycle.py tests/test_sandbox_agent.py -q
```
