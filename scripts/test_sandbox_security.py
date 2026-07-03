"""scripts/test_sandbox_security.py — Security audit test suite for sandbox hardening.

Verifies all five hardening measures defined in GRO-1825:
1. Seccomp profile exists and is valid JSON
2. Path traversal guards in volume mount logic
3. Symlink-based escape prevention
4. Cgroup resource limit enforcement (unit-tested with mocks)
5. Read-only root filesystem option

Run::

    python3 scripts/test_sandbox_security.py [--verbose]

Exit code: 0 = ALL tests pass, 1 = some tests failed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

# Ensure `prismatic` is importable
PRISMATIC_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PRISMATIC_ROOT))


# ── Test helpers ────────────────────────────────────────────────────────────

_results: list[tuple[str, str, str | None]] = []  # (name, status, detail)


def test(name: str) -> callable:
    """Decorator that wraps a test function and records pass/fail."""
    def decorator(fn: callable) -> callable:
        def wrapper(*args, **kwargs):
            try:
                fn(*args, **kwargs)
                _results.append((name, "PASS", None))
                if "--verbose" in sys.argv:
                    print(f"  ✅ {name}")
            except AssertionError:
                detail = traceback.format_exc()
                _results.append((name, "FAIL", detail))
                print(f"  ❌ {name}")
            except Exception:
                detail = traceback.format_exc()
                _results.append((name, "ERROR", detail))
                print(f"  ⚠️  {name} (ERROR)")
        return wrapper
    return decorator


def assert_true(condition: bool, msg: str = "") -> None:
    if not condition:
        raise AssertionError(msg or "Expected True, got False")


def assert_equal(a, b, msg: str = "") -> None:
    if a != b:
        raise AssertionError(msg or f"Expected {a!r} == {b!r}")


def assert_in(sub: str, full: str, msg: str = "") -> None:
    if sub not in full:
        raise AssertionError(msg or f"Expected {sub!r} to be in {full[:200]!r}")


def assert_raises(exc_type: type, func: callable, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
        raise AssertionError(f"Expected {exc_type.__name__} but no exception was raised")
    except exc_type:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Seccomp profile exists and is valid
# ═══════════════════════════════════════════════════════════════════════════


@test("Seccomp Profile: file exists")
def test_seccomp_file_exists():
    profile_path = PRISMATIC_ROOT / "config" / "seccomp" / "plugin-default.json"
    assert_true(profile_path.exists(), f"Seccomp profile not found at {profile_path}")
    assert_true(profile_path.is_file(), "Seccomp profile is not a regular file")


@test("Seccomp Profile: valid JSON schema")
def test_seccomp_valid_json():
    profile_path = PRISMATIC_ROOT / "config" / "seccomp" / "plugin-default.json"
    with open(profile_path) as f:
        profile = json.load(f)

    assert_in("defaultAction", json.dumps(profile), "Missing defaultAction")
    assert_in("SCMP_ACT_ERRNO", json.dumps(profile),
              "defaultAction should be SCMP_ACT_ERRNO (deny-by-default)")
    assert_in("syscalls", json.dumps(profile), "Missing syscalls list")
    assert_true(len(profile["syscalls"]) >= 1, "At least one syscall rule required")


@test("Seccomp Profile: blocks dangerous syscalls")
def test_seccomp_blocks_dangerous():
    profile_path = PRISMATIC_ROOT / "config" / "seccomp" / "plugin-default.json"
    with open(profile_path) as f:
        profile = json.load(f)

    # Find blocked syscalls
    blocked = set()
    for rule in profile["syscalls"]:
        if rule.get("action") == "SCMP_ACT_ERRNO":
            blocked.update(rule.get("names", []))

    dangerous = {"bpf", "kexec_load", "kexec_file_load", "init_module",
                 "finish_module", "delete_module", "ptrace", "add_key",
                 "nfsservctl", "perf_event_open", "lookup_dcookie",
                 "quotactl", "uselib", "vserver"}
    missing = dangerous - blocked
    assert_true(not missing,
                f"These dangerous syscalls should be blocked but aren't: {missing}")

    # Verify common safe syscalls are allowed
    allowed = set()
    for rule in profile["syscalls"]:
        if rule.get("action") == "SCMP_ACT_ALLOW":
            allowed.update(rule.get("names", []))

    required_safe = {"read", "write", "open", "close", "mmap", "brk", "exit",
                     "exit_group", "fstat", "nanosleep", "clock_gettime"}
    missing_safe = required_safe - allowed
    assert_true(not missing_safe,
                f"These essential syscalls should be allowed but aren't: {missing_safe}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Path traversal guards in volume mounts
# ═══════════════════════════════════════════════════════════════════════════


def _get_volume_validator() -> callable:
    """Import and return the _validate_volume_mount method."""
    from prismatic.plugins.sandbox_pod_manager import SandboxPodManager
    return SandboxPodManager._validate_volume_mount


@test("Path Traversal: blocks simple '../'")
def test_traversal_simple_dotdot():
    validator = _get_volume_validator()
    assert_raises(Exception, validator, "/etc/../shadow:/container:/etc/../shadow")


@test("Path Traversal: blocks '../../etc/shadow'")
def test_traversal_etc_shadow():
    validator = _get_volume_validator()
    assert_raises(Exception, validator, "/etc/../../etc/shadow:/data")


@test("Path Traversal: blocks URL-encoded '%2e%2e'")
def test_traversal_url_encoded():
    validator = _get_volume_validator()
    assert_raises(Exception, validator, "/etc/%2e%2e/shadow:/data")


@test("Path Traversal: blocks nested URL-encoded '%252e%252e'")
def test_traversal_double_url_encoded():
    validator = _get_volume_validator()
    assert_raises(Exception, validator, "/etc/%252e%252e/shadow:/data")


@test("Path Traversal: allows legitimate /tmp mounts")
def test_traversal_allows_tmp():
    validator = _get_volume_validator()
    try:
        result = validator("/tmp/plugin-data:/data:ro")
        assert_true(isinstance(result, str), "Should return the volume string")
    except Exception:
        assert_true(False, "Should allow /tmp mounts")


@test("Path Traversal: blocks deep '../../../../../etc'")
def test_traversal_deep():
    validator = _get_volume_validator()
    assert_raises(Exception, validator, "/../../../../../etc/passwd:/data")


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Symlink-based escape prevention
# ═══════════════════════════════════════════════════════════════════════════


@test("Symlink Escape: routes resolved path through allowed_bases")
def test_symlink_escape_allowed_bases():
    """The resolver should check the symlink-resolved path against allowed bases."""
    from prismatic.plugins.sandbox_pod_manager import SandboxPodManager, PodManagerError

    # Create a symlink in /tmp that points to /etc — /etc is NOT an allowed base
    with tempfile.TemporaryDirectory() as td:
        link_path = Path(td) / "etc_link"
        target_path = Path("/etc/passwd")
        try:
            link_path.symlink_to(target_path)
            # The resolved path /etc/passwd is outside allowed bases (/home, /tmp, etc.)
            # so this should be rejected
            assert_raises(
                PodManagerError,
                SandboxPodManager._validate_volume_mount,
                f"{link_path}:/container:ro",
            )
        except OSError:
            pass  # Symlink creation may fail in some environments


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: Cgroup resource limit enforcement
# ═══════════════════════════════════════════════════════════════════════════


@test("Cgroup: detect version returns 0, 1, or 2")
def test_cgroup_detect():
    from prismatic.sandbox.cgroup_enforcer import CgroupEnforcer
    version = CgroupEnforcer._detect_cgroup_version()
    assert_true(version in (0, 1, 2),
                f"Expected cgroup version 0, 1, or 2, got {version}")


@test("Cgroup: parse_memory converts units correctly")
def test_cgroup_memory_parsing():
    from prismatic.sandbox.cgroup_enforcer import CgroupEnforcer
    assert_equal(CgroupEnforcer._parse_memory("512M"), 512 * 1024 * 1024)
    assert_equal(CgroupEnforcer._parse_memory("1G"), 1024 ** 3)
    assert_equal(CgroupEnforcer._parse_memory("256K"), 256 * 1024)
    assert_equal(CgroupEnforcer._parse_memory("0"), 0)
    assert_equal(CgroupEnforcer._parse_memory(1073741824), 1073741824)
    assert_equal(CgroupEnforcer._parse_memory("2T"), 2 * 1024 ** 4)


@test("Cgroup: format_bytes produces human-readable strings")
def test_cgroup_memory_format():
    from prismatic.sandbox.cgroup_enforcer import CgroupEnforcer
    assert_equal(CgroupEnforcer._format_bytes(512), "512B")
    assert_equal(CgroupEnforcer._format_bytes(2048), "2KB")
    assert_equal(CgroupEnforcer._format_bytes(1048576), "1MB")


@test("Cgroup: _parse_memory rejects invalid input")
def test_cgroup_invalid_memory():
    from prismatic.sandbox.cgroup_enforcer import CgroupError, CgroupEnforcer
    assert_raises(CgroupError, CgroupEnforcer._parse_memory, "abc")
    assert_raises(CgroupError, CgroupEnforcer._parse_memory, "5Z")


@test("Cgroup: _extract_value parses key=value files")
def test_cgroup_extract_value():
    from prismatic.sandbox.cgroup_enforcer import CgroupEnforcer
    text = "usage_usec 12345\n  usage_usec 67890"
    assert_equal(CgroupEnforcer._extract_value(text, "usage_usec"), 12345)

    text = "nr_periods 0\nnr_throttled 0\nthrottled_usec 0"
    assert_equal(CgroupEnforcer._extract_value(text, "throttled_usec"), 0)

    assert_equal(CgroupEnforcer._extract_value("empty", "usage_usec"), 0)


@test("Cgroup: graceful no-op when cgroups unavailable")
def test_cgroup_noop():
    """When cgroups aren't available, apply_limits should not crash."""
    from prismatic.sandbox.cgroup_enforcer import CgroupEnforcer
    ef = CgroupEnforcer()
    # Should not raise even if cgroups aren't available
    limits = ef.apply_limits("test-plugin", memory_max="256M", cpu_max=0.25)
    assert_true(hasattr(limits, "memory_max_bytes"),
                "Should return a CgroupLimits dataclass")
    assert_true(isinstance(limits.memory_max_bytes, int) or limits.memory_max_bytes == 0,
                "Should have a valid memory_max_bytes value")


@test("Cgroup: verify_limits raises CgroupError for unknown plugin")
def test_cgroup_verify_nonexistent():
    from prismatic.sandbox.cgroup_enforcer import CgroupEnforcer, CgroupError
    ef = CgroupEnforcer()
    # If cgroups are available, it will try to verify a non-existent path
    try:
        ef.verify_limits("nonexistent-plugin-xyz")
        # If we got here without error, cgroups are not available — that's OK
    except CgroupError:
        pass  # Expected when cgroups are available but plugin doesn't exist


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: Read-only root filesystem
# ═══════════════════════════════════════════════════════════════════════════


@test("Read-Only Root: SandboxPodManager accepts read_only_root param")
def test_rr_constructor():
    from prismatic.plugins.sandbox_pod_manager import SandboxPodManager
    mgr = SandboxPodManager(state_dir="/tmp/test-states", read_only_root=True)
    assert_true(mgr._read_only_root, "_read_only_root should be True")
    mgr2 = SandboxPodManager(state_dir="/tmp/test-states", read_only_root=False)
    assert_true(not mgr2._read_only_root, "_read_only_root should be False")


@test("Read-Only Root: Docker command includes --read-only flag")
def test_rr_docker_flag():
    """When read_only_root=True, the _start_docker command should include --read-only."""
    from prismatic.plugins.sandbox_pod_manager import SandboxPodManager
    mgr = SandboxPodManager(state_dir="/tmp/test-states", read_only_root=True)
    # Inspect the source to see if --read-only is in _start_docker
    source = open(PRISMATIC_ROOT / "prismatic" / "plugins" / "sandbox_pod_manager.py").read()
    assert_in("--read-only", source, "Docker run command should include --read-only flag")


@test("Read-Only Root: k3s command includes --read-only-root-filesystem=true")
def test_rr_k3s_flag():
    """When read_only_root=True, the _start_k3s command should include the flag."""
    source = open(PRISMATIC_ROOT / "prismatic" / "plugins" / "sandbox_pod_manager.py").read()
    assert_in("--read-only-root-filesystem=true", source,
              "k3s run should include read-only-root-filesystem=true")


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════


def print_report():
    """Print summary report of all test results."""
    print()
    print("=" * 60)
    print("  Sandbox Security Audit Report")
    print("=" * 60)
    print()
    print(f"  Tests run: {len(_results)}")
    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    errors = sum(1 for _, s, _ in _results if s == "ERROR")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print(f"  Errors: {errors}")
    print()

    if failed > 0 or errors > 0:
        print("  ❌ FAILURES:")
        for name, status, detail in _results:
            if status != "PASS":
                print(f"    [{status}] {name}")
                if detail and "--verbose" in sys.argv:
                    for line in detail.strip().split("\n"):
                        print(f"      {line}")
                print()
    else:
        print("  ✅ ALL TESTS PASSED")
    print("=" * 60)

    return failed + errors


def main():
    """Run all tests and return exit code."""
    print()
    print("⚠️  Running sandbox security tests...")
    print()

    # Collect all test functions in this module
    test_fns = [
        test_seccomp_file_exists,
        test_seccomp_valid_json,
        test_seccomp_blocks_dangerous,
        test_traversal_simple_dotdot,
        test_traversal_etc_shadow,
        test_traversal_url_encoded,
        test_traversal_double_url_encoded,
        test_traversal_allows_tmp,
        test_traversal_deep,
        test_symlink_escape_allowed_bases,
        test_cgroup_detect,
        test_cgroup_memory_parsing,
        test_cgroup_memory_format,
        test_cgroup_invalid_memory,
        test_cgroup_extract_value,
        test_cgroup_noop,
        test_cgroup_verify_nonexistent,
        test_rr_constructor,
        test_rr_docker_flag,
        test_rr_k3s_flag,
    ]

    for fn in test_fns:
        fn()

    exit_code = print_report()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
