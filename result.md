# Sandbox Isolation Verification (GRO-3179)

## Issue / Requirement
The `--sandbox` flag runs agent tasks inside containerized or gVisor environments. However, volume mount path validation allowed mounting any sub-directory under `/home`, which failed to protect sensitive directories like `/home/ubuntu/mounts/` and `/home/ubuntu/.gemini/`.

The task required blocking all 5 sensitive paths and adding tests in the test suite to prove denial.

The 5 sensitive paths are:
1. `~/.ssh` (or `/home/ubuntu/.ssh`)
2. `~/.aws` (or `/home/ubuntu/.aws`)
3. `~/.kube` (or `/home/ubuntu/.kube`)
4. `~/.gemini` (or `/home/ubuntu/.gemini`)
5. `/home/ubuntu/mounts` (or `~/mounts`)

---

## Fixes Implemented

### 1. Code Hardening
In [prismatic/plugins/sandbox_pod_manager.py](file:///home/ubuntu/work/prismatic-engine/prismatic/plugins/sandbox_pod_manager.py#L596-L617), we updated the `_validate_volume_mount` method to explicitly resolve the host path of the volume mount spec and reject it if it resolves to or is a descendant of any of the 5 sensitive paths (resolving relative to `Path.home()` and `/home/ubuntu/` fallback):
```python
        # Block sensitive paths: ~/.ssh, ~/.aws, ~/.kube, ~/.gemini, ~/mounts
        home = Path.home().resolve()
        sensitive_bases = [
            home / ".ssh",
            home / ".aws",
            home / ".kube",
            home / ".gemini",
            home / "mounts",
            Path("/home/ubuntu/.ssh"),
            Path("/home/ubuntu/.aws"),
            Path("/home/ubuntu/.kube"),
            Path("/home/ubuntu/.gemini"),
            Path("/home/ubuntu/mounts"),
        ]
        for sb in sensitive_bases:
            if resolved == sb or str(resolved).startswith(str(sb) + os.path.sep):
                raise PodManagerError(
                    f"Access to sensitive path blocked: {volume_spec!r} "
                    f"resolves to {str(resolved)!r} which is inside a forbidden sensitive path."
                )
```

### 2. Test Verification

#### A. Security Audit Tests
We added `test_traversal_sensitive_paths` in [scripts/test_sandbox_security.py](file:///home/ubuntu/work/prismatic-engine/scripts/test_sandbox_security.py#L192-L215) and registered it inside the test runner function. Running the script proves all 21 sandbox security audits pass:
```bash
python3 scripts/test_sandbox_security.py --verbose
```
```text
  ✅ Path Traversal: blocks sensitive paths
  ...
  ============================================================
    Sandbox Security Audit Report
  ============================================================
    Tests run: 21
    Passed: 21
    Failed: 0
    Errors: 0

    ✅ ALL TESTS PASSED
  ============================================================
```

#### B. Pytest Unit Tests
We added `TestSandboxVolumeValidation` inside [tests/test_sandbox_agent.py](file:///home/ubuntu/work/prismatic-engine/tests/test_sandbox_agent.py#L75-L110) which validates volume mounts against both sensitive (blocked) and allowed paths.
Running the unit test shows:
```bash
python3 -m pytest tests/test_sandbox_agent.py -v
```
```text
tests/test_sandbox_agent.py::TestSandboxVolumeValidation::test_validate_volume_mount_allowed_paths PASSED [ 75%]
tests/test_sandbox_agent.py::TestSandboxVolumeValidation::test_validate_volume_mount_sensitive_paths PASSED [100%]
```

---

## Conclusion
The test suite now explicitly covers and proves sandbox isolation blocking for all 5 sensitive paths. No files or directories outside allowed limits can be mounted inside the agent sandbox workspace.
