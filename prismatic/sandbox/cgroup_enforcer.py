"""prismatic/sandbox/cgroup_enforcer.py — Cgroup resource limit enforcement.

Enforces memory, CPU, and I/O limits for plugin sandbox pods using Linux
cgroups v2. Falls back to cgroups v1 if v2 is unavailable.

Usage:
    enforcer = CgroupEnforcer()
    enforcer.apply_limits("plugin-name", memory_max="512M", cpu_max=0.5)
    enforcer.verify_limits("plugin-name")
    enforcer.remove_limits("plugin-name")

The enforcer works alongside SandboxPodManager's Docker --memory/--cpus flags.
Docker flags constrain the container; cgroup constraints provide a second layer
enforced by the sandbox runtime itself, preventing escape via resource abuse.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("prismatic.sandbox.cgroup")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CGROUP_V2_BASE = Path("/sys/fs/cgroup")
CGROUP_V1_BASE = Path("/sys/fs/cgroup")

# Default resource limits (same as SandboxPodManager defaults)
_DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MB
_DEFAULT_CPU_QUOTA_US = 50_000             # 50ms per 100ms period → 0.5 CPU
_DEFAULT_CPU_PERIOD_US = 100_000           # 100ms
_DEFAULT_IO_WEIGHT = 100                   # Default block I/O weight (1-1000)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CgroupError(Exception):
    """Raised when a cgroup operation fails."""


class CgroupNotSupportedError(CgroupError):
    """Raised when cgroups are not available on this system."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CgroupLimits:
    """Current resource limits applied to a cgroup."""

    memory_max_bytes: int = _DEFAULT_MEMORY_BYTES
    memory_swap_max_bytes: int = 0  # 0 disables swap
    cpu_quota_us: int = _DEFAULT_CPU_QUOTA_US
    cpu_period_us: int = _DEFAULT_CPU_PERIOD_US
    io_weight: int = _DEFAULT_IO_WEIGHT
    applied_at: float = 0.0


@dataclass
class CgroupStats:
    """Live resource usage for a cgroup."""

    memory_current_bytes: int = 0
    memory_peak_bytes: int = 0
    cpu_usage_us: int = 0
    pid_count: int = 0


# ---------------------------------------------------------------------------
# CgroupEnforcer
# ---------------------------------------------------------------------------


class CgroupEnforcer:
    """Enforce resource limits via Linux cgroups.

    Auto-detects cgroups v2 vs v1. All plugin sandbox cgroups are created as
    child cgroups under the ``prismatic`` slice (e.g.,
    ``/sys/fs/cgroup/prismatic/plugin-<name>/``).

    On systems without cgroups (macOS, containers without cgroupfs), all
    operations are no-ops with a warning log.
    """

    def __init__(
        self,
        cgroup_parent: str = "prismatic",
        memory_max_bytes: int = _DEFAULT_MEMORY_BYTES,
        cpu_quota_us: int = _DEFAULT_CPU_QUOTA_US,
        cpu_period_us: int = _DEFAULT_CPU_PERIOD_US,
        io_weight: int = _DEFAULT_IO_WEIGHT,
    ) -> None:
        self._parent = cgroup_parent
        self._default_limits = CgroupLimits(
            memory_max_bytes=memory_max_bytes,
            cpu_quota_us=cpu_quota_us,
            cpu_period_us=cpu_period_us,
            io_weight=io_weight,
        )
        self._cgroup_v2 = self._detect_cgroup_version() == 2
        self._available = self._detect_available()

        # Tracked cgroups
        self._cgroups: Dict[str, CgroupLimits] = {}
        self._lock = threading.Lock()

        if self._available:
            self._ensure_parent_cgroup()

    # ── Detection ──────────────────────────────────────────────────

    @staticmethod
    def _detect_cgroup_version() -> int:
        """Detect cgroups version (1 or 2).

        Returns 2 if cgroups v2 unified hierarchy is present, 1 if only v1,
        or 0 if neither is available.
        """
        # Check for cgroups v2 unified hierarchy
        try:
            with open(CGROUP_V2_BASE / "cgroup.controllers") as f:
                controllers = f.read().strip()
            if controllers:
                return 2
        except (FileNotFoundError, PermissionError, OSError):
            pass

        # Check for cgroups v1
        try:
            if (CGROUP_V1_BASE / "memory" / "memory.limit_in_bytes").exists():
                return 1
        except (FileNotFoundError, PermissionError, OSError):
            pass

        return 0

    @staticmethod
    def _detect_available() -> bool:
        """Check whether cgroups are writable on this system."""
        version = CgroupEnforcer._detect_cgroup_version()
        if version == 0:
            logger.warning(
                "No cgroup filesystem detected — cgroup enforcement disabled. "
                "Run with --privileged or mount cgroupfs for full sandbox isolation."
            )
            return False

        # Try writing a test file
        try:
            test_dir = CGROUP_V2_BASE / ".prismatic_cgroup_test"
            test_dir.mkdir(parents=True, exist_ok=True)
            (test_dir / "cgroup.procs").write_text("")
            test_dir.rmdir()
            return True
        except (PermissionError, OSError) as exc:
            logger.warning(
                "Cgroup filesystem detected but not writable: %s — "
                "cgroup enforcement disabled.",
                exc,
            )
            return False

    def _ensure_parent_cgroup(self) -> None:
        """Create the prismatic parent cgroup if it doesn't exist."""
        if not self._available:
            return
        parent = CGROUP_V2_BASE / self._parent if self._cgroup_v2 else Path(str(CGROUP_V1_BASE / "memory") / self._parent)
        parent.mkdir(parents=True, exist_ok=True)
        logger.info("Cgroup parent '%s' ready (v2=%s)", self._parent, self._cgroup_v2)

    # ── Public API ─────────────────────────────────────────────────

    def apply_limits(
        self,
        plugin_name: str,
        memory_max: str | int | None = None,
        cpu_max: float | None = None,
        io_weight: int | None = None,
        pids_max: int | None = None,
    ) -> CgroupLimits:
        """Apply resource limits for a plugin's sandbox.

        Args:
            plugin_name: Unique plugin identifier.
            memory_max: Memory limit (e.g., ``"512M"``, ``"1G"``, or bytes int).
            cpu_max: CPU limit as fraction of a core (0.5 = half a core).
            io_weight: Block I/O weight (100-1000).
            pids_max: Maximum number of processes (None = use default).

        Returns:
            The effective ``CgroupLimits`` applied.

        Raises:
            CgroupError: If limits cannot be applied.
        """
        if not self._available:
            logger.info(
                "Cgroup not available — limits for '%s' logged but not enforced "
                "(memory=%s, cpu=%s)",
                plugin_name, memory_max or self._default_limits.memory_max_bytes,
                cpu_max or (self._default_limits.cpu_quota_us / self._default_limits.cpu_period_us),
            )
            return CgroupLimits()

        limits = self._build_limits(memory_max, cpu_max, io_weight, pids_max)
        cg_path = self._plugin_path(plugin_name)

        try:
            cg_path.mkdir(parents=True, exist_ok=True)
            self._write_limits(cg_path, limits)
            limits.applied_at = time.time()

            with self._lock:
                self._cgroups[plugin_name] = limits

            logger.info(
                "Cgroup limits applied for '%s': memory=%s cpu=%.1f io=%d pids=%s",
                plugin_name,
                self._format_bytes(limits.memory_max_bytes),
                limits.cpu_quota_us / limits.cpu_period_us if limits.cpu_period_us else 0,
                limits.io_weight,
                limits.pids_max or "unlimited",
            )
            return limits

        except PermissionError as exc:
            raise CgroupError(
                f"Permission denied creating cgroup for '{plugin_name}': {exc}. "
                "Run the sandbox runtime with elevated privileges."
            ) from exc
        except OSError as exc:
            raise CgroupError(
                f"Failed to apply cgroup limits for '{plugin_name}': {exc}"
            ) from exc

    def remove_limits(self, plugin_name: str) -> None:
        """Remove cgroup limits for a plugin (cleanup on pod purge).

        Args:
            plugin_name: Unique plugin identifier.
        """
        if not self._available:
            return

        cg_path = self._plugin_path(plugin_name)

        with self._lock:
            self._cgroups.pop(plugin_name, None)

        try:
            # Migrate any remaining processes to parent before removing
            procs_file = cg_path / ("cgroup.procs" if self._cgroup_v2 else "tasks")
            if procs_file.exists():
                procs = procs_file.read_text().strip()
                if procs:
                    parent_procs = procs_file.parent.parent / procs_file.name
                    for pid in procs.split("\n"):
                        if pid.strip():
                            try:
                                parent_procs.write_text(f"{pid.strip()}\n")
                            except (OSError, PermissionError):
                                logger.warning(
                                    "Could not migrate PID %s from cgroup '%s'",
                                    pid, plugin_name,
                                )

            cg_path.rmdir()
            logger.info("Cgroup limits removed for '%s'", plugin_name)
        except OSError as exc:
            logger.warning("Failed to remove cgroup for '%s': %s", plugin_name, exc)

    def verify_limits(self, plugin_name: str) -> CgroupStats:
        """Verify that the cgroup limits are actively enforced.

        Reads current resource usage from the cgroup's control files.

        Args:
            plugin_name: Unique plugin identifier.

        Returns:
            ``CgroupStats`` with current resource usage.

        Raises:
            CgroupError: If the cgroup does not exist.
        """
        if not self._available:
            return CgroupStats()

        cg_path = self._plugin_path(plugin_name)
        if not cg_path.exists():
            raise CgroupError(f"Cgroup for '{plugin_name}' does not exist")

        stats = CgroupStats()

        if self._cgroup_v2:
            stats.memory_current_bytes = self._read_int(
                cg_path / "memory.current", default=0
            )
            stats.memory_peak_bytes = self._read_int(
                cg_path / "memory.peak", default=0
            )
            stats.cpu_usage_us = self._read_int(
                cg_path / "cpu.stat", default=0,
                extract_func=lambda t: self._extract_value(t, "usage_usec"),
            )
            stats.pid_count = self._read_int(
                cg_path / "cgroup.procs", default=0,
                extract_func=lambda t: len(t.strip().split("\n")) if t.strip() else 0,
            )
        else:
            stats.memory_current_bytes = self._read_int(
                cg_path / "memory.usage_in_bytes", default=0
            )
            stats.memory_peak_bytes = self._read_int(
                cg_path / "memory.max_usage_in_bytes", default=0
            )
            stats.pid_count = self._read_int(
                cg_path / "tasks", default=0,
                extract_func=lambda t: len(t.strip().split("\n")) if t.strip() else 0,
            )

        return stats

    def get_current_limits(self, plugin_name: str) -> Optional[CgroupLimits]:
        """Return the currently tracked limits for a plugin.

        Returns:
            ``CgroupLimits`` if tracked, or ``None``.
        """
        with self._lock:
            return self._cgroups.get(plugin_name)

    # ── Helpers ────────────────────────────────────────────────────

    def _plugin_path(self, plugin_name: str) -> Path:
        """Return the cgroup directory for a plugin."""
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", plugin_name)
        if self._cgroup_v2:
            return CGROUP_V2_BASE / self._parent / f"plugin-{safe_name}"
        return CGROUP_V1_BASE / "memory" / self._parent / f"plugin-{safe_name}"

    def _build_limits(
        self,
        memory_max: str | int | None,
        cpu_max: float | None,
        io_weight: int | None,
        pids_max: int | None,
    ) -> CgroupLimits:
        """Build CgroupLimits from optional overrides and defaults."""
        mem_bytes = self._parse_memory(memory_max) if memory_max else self._default_limits.memory_max_bytes
        cpu_quota = int((cpu_max or self._default_limits.cpu_quota_us / self._default_limits.cpu_period_us)
                        * self._default_limits.cpu_period_us)
        return CgroupLimits(
            memory_max_bytes=mem_bytes,
            cpu_quota_us=cpu_quota,
            cpu_period_us=self._default_limits.cpu_period_us,
            io_weight=io_weight or self._default_limits.io_weight,
        )

    def _write_limits(self, cg_path: Path, limits: CgroupLimits) -> None:
        """Write resource limits to cgroup control files."""
        if self._cgroup_v2:
            # Memory
            (cg_path / "memory.max").write_text(f"{limits.memory_max_bytes}\n")
            (cg_path / "memory.swap.max").write_text(f"{limits.memory_swap_max_bytes}\n")

            # CPU
            (cg_path / "cpu.max").write_text(
                f"{limits.cpu_quota_us} {limits.cpu_period_us}\n"
            )

            # I/O
            (cg_path / "io.weight").write_text(f"{limits.io_weight}\n")

            # PIDs limit
            if (cg_path / "pids.max").exists():
                (cg_path / "pids.max").write_text(f"{limits.pids_max}\n" if limits.pids_max else "max\n")
        else:
            # cgroups v1 — memory
            (cg_path / "memory.limit_in_bytes").write_text(f"{limits.memory_max_bytes}\n")
            (cg_path / "memory.memsw.limit_in_bytes").write_text(f"{limits.memory_max_bytes}\n")

    @staticmethod
    def _parse_memory(value: str | int) -> int:
        """Parse a memory string (e.g. ``\"512M\"``, ``\"1G\"``) to bytes.

        Args:
            value: Memory value as string with suffix or raw int.

        Returns:
            Memory value in bytes.
        """
        if isinstance(value, int):
            return value

        value = value.strip().upper()
        multipliers = {
            "K": 1024,
            "M": 1024 ** 2,
            "G": 1024 ** 3,
            "T": 1024 ** 4,
        }
        for suffix, mult in multipliers.items():
            if value.endswith(suffix):
                try:
                    return int(float(value[:-1]) * mult)
                except ValueError:
                    raise CgroupError(f"Invalid memory value: '{value}'")

        try:
            return int(value)
        except ValueError:
            raise CgroupError(f"Invalid memory value: '{value}'")

    @staticmethod
    def _format_bytes(b: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b}{unit}"
            b //= 1024
        return f"{b}TB"

    @staticmethod
    def _read_int(
        path: Path, default: int = 0,
        extract_func=None,
    ) -> int:
        """Read an integer from a cgroup control file.

        Args:
            path: Control file path.
            default: Value to return if file can't be read.
            extract_func: Optional function to extract value from raw text.

        Returns:
            Integer value read from the file, or default.
        """
        try:
            text = path.read_text().strip()
            if extract_func:
                return extract_func(text)
            return int(text)
        except (FileNotFoundError, ValueError, OSError):
            return default

    @staticmethod
    def _extract_value(text: str, key: str) -> int:
        """Extract a numeric value from a key=value formatted file.

        Args:
            text: Raw file content (newline-separated key=value lines).
            key: Key to find.

        Returns:
            Integer value, or 0 if not found.
        """
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith(key + " "):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        return 0
        return 0
