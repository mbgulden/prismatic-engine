"""Prismatic Engine Hypervisor: guest process spawn path (Containment Phase A).

Spawns *guest* processes under explicit trust tiers, supervises them with a
fail-closed heartbeat lease, and records every lifecycle event to the
hypervisor ledger.

What each trust tier guarantees -- read this before relying on it:

- ``TrustTier.UNTRUSTED``: the guest gets the strongest backend available
  (Linux network/mount namespaces via the optional ``swarmsandbox``
  dependency when importable, otherwise the stdlib fallback), no network
  access, and a scrubbed environment. What it does NOT guarantee: the
  stdlib fallback backend cannot enforce no-network or mount/filesystem
  isolation -- it is a policy-enforced runner (timeouts, output caps, env
  scrubbing), not a security boundary. Real isolation comes only from the
  namespace/container backends, which need Linux (``unshare``) or a
  container runtime (docker/podman) respectively.
- ``TrustTier.TRUSTED``: a wrapped-but-unjailed local process. The run is
  ledger-wrapped, timeout-enforced, output-capped, and env-scrubbed, but
  there are NO isolation claims. This tier is never described as
  "sandboxed" -- not in code, logs, docs, or ledger payloads.

Heartbeat lease (fail-closed, in-process): every guest is watched by a
monitor task. The owner must call ``await handle.heartbeat()`` at least
every ``lease_ttl_seconds`` (or hold the guest via
``GuestManager.supervised()``, which heartbeats automatically). If
renewals stop for longer than the TTL, the monitor SIGKILLs the guest and
records ``guest_lease_expired``. The lease is enforced in-process: if the
supervising process itself dies, the monitor dies with it and the guest
keeps running. Cross-process lease supervision is an explicit follow-up.

Boundary (per docs/infrastructure-capabilities.md): this module moves
processes and enforces policy. It never decides what a guest does next --
no agent logic, no tool-call decisions, no conversation state.

Ledger: every guest event is recorded via
``HypervisorLedger.record_event`` with ``producer="hypervisor.guest"``
and ``task_id=<guest_id>``. Payloads carry tier, backend, isolation,
exit codes, and durations -- never environment values or secrets.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Mapping

from prismatic.hypervisor.ledger import HypervisorLedger, get_hypervisor_ledger

logger = logging.getLogger("prismatic.hypervisor.guest")

_PRODUCER = "hypervisor.guest"

# Env keys containing any of these substrings (case-insensitive) are treated
# as secrets: dropped from the guest environment, and their values are
# redacted from any captured output before it reaches logs or the ledger.
_SECRET_KEY_HINTS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD")

_TRUNCATION_MARKER = "[prismatic: guest output truncated at {cap} bytes]"

_VALID_BACKENDS = ("auto", "stdlib", "namespace", "container")

# end_reason values a GuestResult can carry.
_REASON_EXIT = "exit"
_REASON_TIMEOUT = "timeout"
_REASON_LEASE_EXPIRED = "lease_expired"
_REASON_TERMINATED = "terminated"
_REASON_ERROR = "error"


class TrustTier(str, Enum):
    """Explicit trust tier for a guest process."""

    UNTRUSTED = "untrusted"
    TRUSTED = "trusted"


class GuestSpecError(ValueError):
    """A GuestSpec is invalid or asks for a capability Phase A cannot provide."""


@dataclass
class GuestSpec:
    """What to spawn and under which policy.

    ``entrypoint`` is an argv list -- never a shell string; no shell is
    ever invoked. ``env`` is merged over the scrubbed process environment.
    ``budget_usd`` is stored on the handle/result for Phase C; Phase A
    does not enforce it. ``snapshot_from`` is rejected: guest snapshots /
    suspend-resume arrive in Phase D.
    """

    entrypoint: list[str]
    tier: TrustTier = TrustTier.UNTRUSTED
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 300.0
    max_output_bytes: int = 1_000_000
    lease_ttl_seconds: float = 300.0
    backend: str = "auto"  # auto | stdlib | namespace | container
    budget_usd: float | None = None
    snapshot_from: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entrypoint, list) or not self.entrypoint:
            raise GuestSpecError(
                "entrypoint must be a non-empty argv list (no shell is used)."
            )
        for part in self.entrypoint:
            if not isinstance(part, str) or not part:
                raise GuestSpecError(
                    f"entrypoint parts must be non-empty strings, got {part!r}."
                )
        if not isinstance(self.tier, TrustTier):
            raise GuestSpecError(f"tier must be a TrustTier, got {self.tier!r}.")
        if self.backend not in _VALID_BACKENDS:
            raise GuestSpecError(
                f"backend must be one of {_VALID_BACKENDS}, got {self.backend!r}."
            )
        for name in ("timeout_seconds", "lease_ttl_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value <= 0:
                raise GuestSpecError(f"{name} must be a positive number.")
        if not isinstance(self.max_output_bytes, int) or self.max_output_bytes <= 0:
            raise GuestSpecError("max_output_bytes must be a positive int.")
        if self.snapshot_from is not None:
            raise GuestSpecError(
                f"snapshot_from={self.snapshot_from!r} requires guest snapshots "
                "/ suspend-resume, which arrive in Phase D. "
                "Phase A rejects this spec field."
            )
        for key, value in self.env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise GuestSpecError(
                    f"env keys and values must be strings, got {key!r}: {value!r}."
                )
        if self.budget_usd is not None and (
            not isinstance(self.budget_usd, (int, float)) or self.budget_usd < 0
        ):
            raise GuestSpecError("budget_usd must be a non-negative number.")


@dataclass
class GuestResult:
    """Terminal state of a guest process."""

    guest_id: str
    tier: str
    backend: str
    isolation: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    truncated: bool
    end_reason: str  # exit | timeout | lease_expired | terminated | error
    budget_usd: float | None = None


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(hint in upper for hint in _SECRET_KEY_HINTS)


def scrub_environment(
    env: Mapping[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Drop secret-hint keys from an env mapping.

    Returns ``(scrubbed_env, dropped_values)`` -- the dropped *values* are
    returned so callers can redact them from captured output. Never log
    the values themselves.
    """
    scrubbed: dict[str, str] = {}
    dropped_values: list[str] = []
    for key, value in env.items():
        if _is_secret_key(key):
            if value:
                dropped_values.append(value)
            continue
        scrubbed[key] = value
    return scrubbed, dropped_values


def build_guest_env(
    spec_env: Mapping[str, str],
) -> tuple[dict[str, str], list[str], int]:
    """Build the environment a guest process receives.

    The host environment is scrubbed of secret-hint keys, ``spec_env`` is
    merged over it, and the merged result is scrubbed again (so a spec
    cannot smuggle a blocked key in). Returns
    ``(guest_env, secret_values, scrubbed_key_count)``.
    """
    scrubbed_base, dropped = scrub_environment(os.environ)
    merged = dict(scrubbed_base)
    merged.update(spec_env)
    final_env, dropped_spec = scrub_environment(merged)
    secret_values = dropped + dropped_spec
    return final_env, secret_values, len(secret_values)


def redact_secrets(text: str, secrets: list[str]) -> str:
    """Replace occurrences of secret values in text with ``***``."""
    for secret in secrets:
        if secret and len(secret) >= 4 and secret in text:
            text = text.replace(secret, "***")
    return text


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _LocalProcess:
    """Stdlib backend: asyncio subprocess with policy enforcement.

    Enforces timeout (SIGKILL of the whole process group), output caps, env
    scrubbing, and exit-code capture. This is a *policy-enforced runner*,
    not a security boundary: it cannot enforce no-network or
    mount/filesystem isolation. ``isolation`` says so plainly.
    """

    backend_name = "stdlib"
    isolation = "policy-enforced-runner"

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        *,
        cap_bytes: int,
        secrets: list[str],
        timeout_seconds: float,
    ) -> None:
        self._proc = proc
        self._cap = cap_bytes
        self._secrets = secrets
        self._timeout = timeout_seconds
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.truncated = False

    @classmethod
    async def spawn(
        cls, spec: GuestSpec, env: dict[str, str], secrets: list[str]
    ) -> "_LocalProcess":
        """Spawn the guest; no shell is ever involved."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *spec.entrypoint,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
                # New session => the guest (and any children it spawns) can
                # be SIGKILLed as a group on timeout / lease expiry.
                start_new_session=True,
            )
        except (OSError, FileNotFoundError) as exc:
            raise GuestSpecError(
                f"cannot spawn guest entrypoint {spec.entrypoint[0]!r}: {exc}"
            ) from exc
        return cls(
            proc,
            cap_bytes=spec.max_output_bytes,
            secrets=secrets,
            timeout_seconds=spec.timeout_seconds,
        )

    async def _drain(self, stream: asyncio.StreamReader, buf: bytearray) -> None:
        """Read a pipe to EOF, keeping only the first ``cap`` bytes.

        Draining continues past the cap (discarding) so a chatty guest
        never blocks on a full pipe and never deadlocks the supervisor.
        """
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                if len(buf) < self._cap:
                    buf.extend(chunk[: self._cap - len(buf)])
                    if len(buf) >= self._cap:
                        self.truncated = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "guest stream drain failed; output may be incomplete",
                exc_info=True,
            )

    def _decode(self, buf: bytearray) -> str:
        text = bytes(buf).decode("utf-8", errors="replace")
        if self.truncated:
            text += "\n" + _TRUNCATION_MARKER.format(cap=self._cap) + "\n"
        return redact_secrets(text, self._secrets)

    def logs(self) -> tuple[str, str]:
        """(stdout, stderr) captured so far, secrets redacted."""
        return self._decode(self._stdout), self._decode(self._stderr)

    async def kill(self) -> None:
        """SIGKILL the guest's process group (fail-closed)."""
        proc = self._proc
        if proc.returncode is not None:
            return
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # Windows: no process groups; kill the direct child.
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    async def wait(self) -> tuple[int | None, str]:
        """Wait for the guest; enforce the wall-clock timeout.

        Returns ``(exit_code, reason)`` with reason ``"exit"`` or
        ``"timeout"``.
        """
        drainers = [
            asyncio.create_task(self._drain(self._proc.stdout, self._stdout)),
            asyncio.create_task(self._drain(self._proc.stderr, self._stderr)),
        ]
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=self._timeout)
            return self._proc.returncode, _REASON_EXIT
        except asyncio.TimeoutError:
            await self.kill()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("guest did not die after SIGKILL")
            return self._proc.returncode, _REASON_TIMEOUT
        finally:
            # Give the drainers a beat to consume EOF so no trailing output
            # is lost; only cancel if they genuinely hang (e.g. a pipe
            # held open by a descendant that survived the kill).
            try:
                await asyncio.wait_for(asyncio.gather(*drainers), timeout=5.0)
            except asyncio.TimeoutError:
                for task in drainers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*drainers, return_exceptions=True)


_swarmsandbox_module: Any = None
_swarmsandbox_probed = False


def _import_swarmsandbox() -> Any | None:
    """Import swarmsandbox if installed; None otherwise (cached).

    swarmsandbox is an *optional* dependency: the stdlib backend is the
    default and this module must import cleanly without it.
    """
    global _swarmsandbox_module, _swarmsandbox_probed
    if not _swarmsandbox_probed:
        try:
            import swarmsandbox as _mod

            _swarmsandbox_module = _mod
        except ImportError:
            _swarmsandbox_module = None
        _swarmsandbox_probed = True
    return _swarmsandbox_module


class _SwarmsandboxRunner:
    """Adapter around the optional swarmsandbox dependency.

    Used only for explicitly stronger isolation (Linux namespaces via
    ``unshare``, or containers via docker/podman). ``backend="auto"``
    selects the namespace backend when available and falls back to the
    stdlib backend otherwise -- containers are never auto-selected
    (opt-in only).

    Known limitation (upstream follow-up): the swarmsandbox API owns the
    child process, so on wall-clock timeout / lease expiry Phase A cannot
    hard-kill a namespace/container guest the way the stdlib backend can.
    A timeout is recorded as ``guest_failed{reason: timeout}``; the
    library's own limits remain the backstop. This is stated here instead
    of silently pretending the kill happened.
    """

    def __init__(
        self,
        kind: str,
        isolation: str,
        sandbox: Any,
        *,
        spec: GuestSpec,
        env: dict[str, str],
        secrets: list[str],
    ) -> None:
        self.backend_name = kind  # "namespace" | "container"
        self.isolation = isolation
        self._sandbox = sandbox
        self._spec = spec
        self._env = env
        self._secrets = secrets
        self._cap = spec.max_output_bytes
        self._timeout = spec.timeout_seconds
        self._stdout = ""
        self._stderr = ""
        self.truncated = False

    # -- availability probing ------------------------------------------------
    # The namespace probe result is cached: it shells out to `unshare`,
    # and the answer does not change within a process lifetime.
    _namespace_usable: bool | None = None

    @classmethod
    def _unshare_can_map_root(cls) -> bool:
        """True when this process can create a user namespace *with* a uid
        map -- the operation swarmsandbox's namespace backend performs.
        ``unshare`` existing on PATH is not sufficient: restricted kernels
        allow ``unshare --user`` but block the ``/proc/self/uid_map`` write,
        in which case the backend fails every spawn with
        "Operation not permitted"."""
        try:
            proc = subprocess.run(
                ["unshare", "--map-root-user", "true"],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            return False
        return proc.returncode == 0

    @classmethod
    def _namespace_available(cls) -> bool:
        if cls._namespace_usable is None:
            cls._namespace_usable = (
                _import_swarmsandbox() is not None
                and sys.platform.startswith("linux")
                and shutil.which("unshare") is not None
                and cls._unshare_can_map_root()
            )
        return cls._namespace_usable

    @classmethod
    def _container_available(cls) -> bool:
        return _import_swarmsandbox() is not None and (
            shutil.which("docker") is not None or shutil.which("podman") is not None
        )

    @classmethod
    def resolve(cls, spec: GuestSpec) -> tuple[str, str] | None:
        """Resolve ``spec.backend`` to ``(kind, isolation)``.

        Returns None when the stdlib backend should be used. Raises
        ``GuestSpecError`` when an *explicitly* requested stronger backend
        is unavailable -- silently downgrading an explicit isolation
        request would be dishonest. ``"auto"`` falls back to stdlib
        gracefully; containers are never auto-selected.
        """
        if spec.tier is TrustTier.TRUSTED or spec.backend == "stdlib":
            # TRUSTED guests are wrapped-but-unjailed local processes by
            # definition; backend selection applies to UNTRUSTED guests.
            return None
        if spec.backend == "auto":
            if cls._namespace_available():
                return "namespace", "linux-namespace-jail"
            return None
        if spec.backend == "namespace":
            if not cls._namespace_available():
                raise GuestSpecError(
                    "backend='namespace' requested but unavailable: need the "
                    "swarmsandbox package, Linux, `unshare` on PATH, and a "
                    "kernel that permits unprivileged user namespaces with "
                    "a uid map (the uid_map write is blocked on some VMs)."
                )
            return "namespace", "linux-namespace-jail"
        if spec.backend == "container":
            if not cls._container_available():
                raise GuestSpecError(
                    "backend='container' requested but unavailable: need the "
                    "swarmsandbox package and docker or podman on PATH."
                )
            return "container", "container-jail"
        raise GuestSpecError(f"unknown backend {spec.backend!r}.")  # pragma: no cover

    # -- spawn / run ----------------------------------------------------------
    @classmethod
    async def spawn(
        cls,
        spec: GuestSpec,
        kind: str,
        isolation: str,
        env: dict[str, str],
        secrets: list[str],
    ) -> "_SwarmsandboxRunner":
        ss = _import_swarmsandbox()
        assert ss is not None  # resolve() already proved availability
        policy = ss.SandboxPolicy(
            resource_limits=ss.ResourceLimits(
                max_memory_mb=512,
                max_cpu_seconds=float(spec.timeout_seconds),
                max_processes=64,
                max_file_size_mb=100,
            ),
            network=ss.NetworkPolicy(allow_network=False),
            mounts=[],
            env_allowlist=[],
            env_blocklist=list(_SECRET_KEY_HINTS),
        )
        clean_env = ss.PolicyValidator.sanitize_environment(env, policy)
        backend_enum = getattr(
            ss.SandboxBackend,
            {"namespace": "NAMESPACE", "container": "CONTAINER"}[kind],
        )
        try:
            sandbox = await asyncio.to_thread(ss.Sandbox, policy, backend_enum)
        except Exception as exc:
            raise GuestSpecError(
                f"swarmsandbox backend {kind!r} failed to initialize: {exc}"
            ) from exc
        return cls(kind, isolation, sandbox, spec=spec, env=clean_env, secrets=secrets)

    def _cap_text(self, text: str) -> str:
        raw = text.encode("utf-8", errors="replace")
        if len(raw) > self._cap:
            self.truncated = True
            raw = raw[: self._cap]
        out = raw.decode("utf-8", errors="replace")
        if self.truncated:
            out += "\n" + _TRUNCATION_MARKER.format(cap=self._cap) + "\n"
        return redact_secrets(out, self._secrets)

    def logs(self) -> tuple[str, str]:
        """(stdout, stderr) captured so far, secrets redacted."""
        return self._stdout, self._stderr

    async def kill(self) -> None:
        # The library owns the child process; Phase A has no handle to
        # SIGKILL it. Best effort only -- documented in the class docstring.
        logger.warning(
            "lease/timeout kill requested for swarmsandbox backend %r: "
            "no process handle available; relying on the library's limits",
            self.backend_name,
        )

    async def wait(self) -> tuple[int | None, str]:
        """Run the guest to completion with a wall-clock timeout.

        Returns ``(exit_code, reason)`` with reason ``"exit"`` or
        ``"timeout"``.
        """
        ss = _import_swarmsandbox()
        cmd = list(self._spec.entrypoint)
        run = self._sandbox.run
        started = time.monotonic()
        try:
            if inspect.iscoroutinefunction(run):
                result = await asyncio.wait_for(
                    run(cmd, env=self._env), timeout=self._timeout
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(run, cmd, None, self._env),
                    timeout=self._timeout,
                )
        except asyncio.TimeoutError:
            return None, _REASON_TIMEOUT
        except Exception as exc:
            if ss is not None and isinstance(exc, ss.SandboxTimeoutError):
                return None, _REASON_TIMEOUT
            raise
        self._stdout = self._cap_text(result.stdout or "")
        self._stderr = self._cap_text(result.stderr or "")
        logger.debug(
            "swarmsandbox guest finished in %.2fs (lib-reported)",
            time.monotonic() - started,
        )
        return result.exit_code, _REASON_EXIT


# ---------------------------------------------------------------------------
# Guest handle (lifecycle + fail-closed heartbeat lease)
# ---------------------------------------------------------------------------


class GuestHandle:
    """Live handle to a spawned guest.

    ``wait()`` blocks until the guest finishes. ``heartbeat()`` renews the
    lease. ``terminate()`` kills the guest. ``logs()`` returns captured
    output so far. ``status()`` returns a JSON-serializable status dict.
    """

    def __init__(
        self,
        *,
        guest_id: str,
        spec: GuestSpec,
        manager: "GuestManager",
        running: Any,
        secrets: list[str],
    ) -> None:
        self.guest_id = guest_id
        self.budget_usd = spec.budget_usd  # stored; NOT enforced in Phase A
        self._spec = spec
        self._manager = manager
        self._running = running
        self._secrets = secrets
        # TRUSTED guests are wrapped-but-unjailed local processes: no
        # isolation claims, so the reported isolation is "none" regardless
        # of which backend ran them. Never "sandboxed", anywhere.
        self._isolation = (
            "none" if spec.tier is TrustTier.TRUSTED else running.isolation
        )
        self._lock = asyncio.Lock()
        self._done = asyncio.Event()
        self._result: GuestResult | None = None
        self._override_reason: str | None = None
        self._start = time.monotonic()
        self._last_heartbeat = self._start
        self._supervise_task = asyncio.create_task(self._supervise())
        self._monitor_task = asyncio.create_task(self._lease_monitor())

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def tier(self) -> str:
        return self._spec.tier.value

    @property
    def backend(self) -> str:
        return self._running.backend_name

    @property
    def isolation(self) -> str:
        return self._isolation

    # -- public API ---------------------------------------------------------
    async def wait(self) -> GuestResult:
        """Block until the guest reaches a terminal state."""
        await self._done.wait()
        assert self._result is not None
        return self._result

    async def heartbeat(self) -> None:
        """Renew the heartbeat lease. No-op once the guest is done."""
        self._last_heartbeat = time.monotonic()

    async def terminate(self) -> GuestResult:
        """Kill the guest and return its final result.

        A guest that already exited naturally keeps its natural result;
        terminate() is a no-op for it.
        """
        async with self._lock:
            if self._done.is_set():
                assert self._result is not None
                return self._result
            if self._override_reason is None:
                self._override_reason = _REASON_TERMINATED
        try:
            await self._running.kill()
        except Exception:
            logger.warning("guest %s kill failed", self.guest_id, exc_info=True)
        return await self.wait()

    def logs(self) -> tuple[str, str]:
        """(stdout, stderr) captured so far, secrets redacted."""
        return self._running.logs()

    def status(self) -> dict[str, Any]:
        """JSON-serializable status snapshot.

        For ``TrustTier.TRUSTED`` guests this payload never claims
        sandboxing: isolation is reported as ``"none"`` (wrapped local
        process, no isolation claims).
        """
        return {
            "guest_id": self.guest_id,
            "tier": self.tier,
            "backend": self.backend,
            "isolation": self.isolation,
            "state": "done" if self._done.is_set() else "running",
            "end_reason": self._result.end_reason if self._result else None,
            "exit_code": self._result.exit_code if self._result else None,
            "truncated": self._result.truncated if self._result else False,
            "budget_usd": self.budget_usd,
            "uptime_seconds": round(time.monotonic() - self._start, 3),
        }

    # -- supervision ----------------------------------------------------------
    async def _supervise(self) -> None:
        """Drive the backend to completion, then finalize exactly once."""
        try:
            exit_code, natural = await self._running.wait()
            reason = natural
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("guest %s backend failed", self.guest_id)
            exit_code, reason = None, _REASON_ERROR
        async with self._lock:
            if self._override_reason is not None:
                reason = self._override_reason
        # A lease expiry records its own dedicated `guest_lease_expired`
        # event (in _expire_lease); the generic terminal recorder must not
        # also emit `guest_failed` for it.
        await self._finalize(
            exit_code, reason, extra_action_done=(reason == _REASON_LEASE_EXPIRED)
        )

    async def _lease_monitor(self) -> None:
        """Fail-closed heartbeat lease.

        In-process only: if this supervisor process dies, the monitor dies
        with it and the guest is NOT killed. Cross-process supervision is
        an explicit follow-up.
        """
        ttl = self._spec.lease_ttl_seconds
        try:
            while not self._done.is_set():
                await asyncio.sleep(min(ttl / 3.0, 5.0))
                if self._done.is_set():
                    break
                idle = time.monotonic() - self._last_heartbeat
                if idle >= ttl:
                    await self._expire_lease(idle)
                    break
        except asyncio.CancelledError:
            pass

    async def _expire_lease(self, idle_seconds: float) -> None:
        """SIGKILL the guest: its lease ran out. Fail closed."""
        async with self._lock:
            if self._done.is_set():
                return
            if self._override_reason is None:
                self._override_reason = _REASON_LEASE_EXPIRED
        logger.warning(
            "guest %s lease expired after %.1fs without heartbeat; killing",
            self.guest_id,
            idle_seconds,
        )
        try:
            await self._running.kill()
        except Exception:
            logger.warning(
                "guest %s lease-expiry kill failed", self.guest_id, exc_info=True
            )
        await self._manager._record(
            self.guest_id,
            "guest_lease_expired",
            {
                "tier": self.tier,
                "backend": self.backend,
                "isolation": self.isolation,
                "lease_ttl_seconds": self._spec.lease_ttl_seconds,
                "idle_seconds": round(idle_seconds, 3),
            },
        )
        # The supervise task observes the kill and finalizes with the real
        # exit code (SIGKILL => -9 on POSIX). Wait for it briefly; only
        # finalize directly if the backend never reports back (adapter
        # backends, where the library owns the child).
        try:
            await asyncio.wait_for(self._done.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            # exit_code is None: the guest was killed by the lease monitor
            # and its backend never reported an exit.
            await self._finalize(None, _REASON_LEASE_EXPIRED, extra_action_done=True)

    async def _finalize(
        self, exit_code: int | None, reason: str, extra_action_done: bool = False
    ) -> GuestResult | None:
        async with self._lock:
            if self._done.is_set():
                return self._result
            duration = time.monotonic() - self._start
            stdout, stderr = self._running.logs()
            result = GuestResult(
                guest_id=self.guest_id,
                tier=self.tier,
                backend=self.backend,
                isolation=self.isolation,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=round(duration, 3),
                truncated=bool(self._running.truncated),
                end_reason=reason,
                budget_usd=self.budget_usd,
            )
            self._result = result
            # NOTE: _done is set only after the terminal ledger write below,
            # so wait()/terminate() never observe a result whose terminal
            # event is not yet in the ledger.
        current = asyncio.current_task()
        for task in (self._monitor_task, self._supervise_task):
            if task is not None and task is not current:
                task.cancel()
        await self._manager._forget(self.guest_id)
        if not extra_action_done:
            await self._record_terminal(reason, result)
        self._done.set()
        return result

    async def _record_terminal(self, reason: str, result: GuestResult) -> None:
        if reason in (_REASON_EXIT, _REASON_TERMINATED):
            action: str = "guest_terminated"
            payload: dict[str, Any] = {
                "tier": result.tier,
                "backend": result.backend,
                "isolation": result.isolation,
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "end_reason": reason,
                "truncated": result.truncated,
                "output_bytes": len(result.stdout.encode("utf-8", errors="replace")),
                "budget_usd": result.budget_usd,
            }
        else:  # timeout | error
            action = "guest_failed"
            payload = {
                "tier": result.tier,
                "backend": result.backend,
                "isolation": result.isolation,
                "reason": reason,
                "duration_seconds": result.duration_seconds,
                "truncated": result.truncated,
                "budget_usd": result.budget_usd,
            }
        await self._manager._record(self.guest_id, action, payload)


# ---------------------------------------------------------------------------
# Guest manager
# ---------------------------------------------------------------------------


class GuestManager:
    """Spawns and supervises guest processes.

    ``ledger`` defaults to the global hypervisor ledger (resolved lazily so
    merely constructing a manager never touches disk). Pass an explicit
    ``HypervisorLedger`` (e.g. on a tmp path) in tests.
    """

    def __init__(self, ledger: HypervisorLedger | None = None) -> None:
        self._ledger = ledger
        self._guests: dict[str, GuestHandle] = {}
        self._lock = asyncio.Lock()

    @property
    def ledger(self) -> HypervisorLedger:
        if self._ledger is None:
            self._ledger = get_hypervisor_ledger()
        return self._ledger

    async def _record(
        self, guest_id: str, action: str, payload: dict[str, Any]
    ) -> None:
        """Record a guest event; never let a ledger outage break supervision."""
        try:
            await asyncio.to_thread(
                self.ledger.record_event, guest_id, _PRODUCER, action, payload
            )
        except Exception:
            # The guest lifecycle (especially fail-closed kills) must not
            # depend on the ledger being writable. Log loudly instead; the
            # hash-chain verification will reveal the gap later.
            logger.error(
                "guest %s: ledger write failed for action %r",
                guest_id,
                action,
                exc_info=True,
            )

    async def _forget(self, guest_id: str) -> None:
        async with self._lock:
            self._guests.pop(guest_id, None)

    async def spawn_guest(self, spec: GuestSpec) -> GuestHandle:
        """Spawn a guest from ``spec``; setup failures terminate + release."""
        if not isinstance(spec, GuestSpec):
            raise GuestSpecError(
                f"spec must be a GuestSpec, got {type(spec).__name__}."
            )
        guest_id = f"guest-{uuid.uuid4().hex[:12]}"
        resolved = _SwarmsandboxRunner.resolve(spec)
        backend_name = resolved[0] if resolved else _LocalProcess.backend_name
        # TRUSTED guests make no isolation claims: report "none".
        if spec.tier is TrustTier.TRUSTED:
            isolation = "none"
        else:
            isolation = resolved[1] if resolved else _LocalProcess.isolation
        if spec.tier is TrustTier.TRUSTED and spec.backend != "stdlib":
            logger.debug(
                "guest %s: trusted tier always uses the stdlib backend; "
                "ignoring backend=%r",
                guest_id,
                spec.backend,
            )
        env, secret_values, scrubbed_count = build_guest_env(spec.env)
        await self._record(
            guest_id,
            "guest_spawned",
            {
                "tier": spec.tier.value,
                "backend": backend_name,
                "isolation": isolation,
                "argv0": spec.entrypoint[0].rsplit("/", 1)[-1],
                "argc": len(spec.entrypoint),
                "timeout_seconds": spec.timeout_seconds,
                "lease_ttl_seconds": spec.lease_ttl_seconds,
                "max_output_bytes": spec.max_output_bytes,
                "scrubbed_env_keys": scrubbed_count,
                "budget_usd": spec.budget_usd,
            },
        )
        try:
            if resolved is None:
                running = await _LocalProcess.spawn(spec, env, secret_values)
            else:
                kind, _iso = resolved
                running = await _SwarmsandboxRunner.spawn(
                    spec, kind, _iso, env, secret_values
                )
        except Exception as exc:
            # Setup-failure discipline: record, release, re-raise. No half
            # -spawned guest is ever left behind.
            await self._record(
                guest_id,
                "guest_failed",
                {
                    "tier": spec.tier.value,
                    "backend": backend_name,
                    "reason": "spawn_error",
                    "error": redact_secrets(
                        f"{type(exc).__name__}: {exc}", secret_values
                    )[:500],
                },
            )
            raise
        handle = GuestHandle(
            guest_id=guest_id,
            spec=spec,
            manager=self,
            running=running,
            secrets=secret_values,
        )
        async with self._lock:
            self._guests[guest_id] = handle
        return handle

    async def terminate_all(self) -> None:
        """Terminate every live guest. Best effort per guest."""
        async with self._lock:
            handles = list(self._guests.values())
        for handle in handles:
            try:
                await handle.terminate()
            except Exception:
                logger.warning(
                    "terminate_all: guest %s failed to terminate",
                    handle.guest_id,
                    exc_info=True,
                )

    async def active_guests(self) -> list[str]:
        """Guest IDs currently supervised (not yet terminal)."""
        async with self._lock:
            return [
                guest_id for guest_id, handle in self._guests.items() if not handle.done
            ]

    @asynccontextmanager
    async def supervised(self, spec: GuestSpec) -> AsyncIterator[GuestHandle]:
        """Spawn a guest with automatic heartbeats; terminate on exit.

        The lease is renewed every ``lease_ttl_seconds / 3`` while the
        context is held, so a well-behaved block never trips the
        fail-closed monitor. The guest is always terminated when the
        block exits, even on exception.
        """
        handle = await self.spawn_guest(spec)
        stop = asyncio.Event()

        async def _auto_heartbeat() -> None:
            try:
                while not stop.is_set():
                    await asyncio.sleep(max(spec.lease_ttl_seconds / 3.0, 0.1))
                    if stop.is_set() or handle.done:
                        break
                    await handle.heartbeat()
            except asyncio.CancelledError:
                pass

        heartbeat_task = asyncio.create_task(_auto_heartbeat())
        try:
            yield handle
        finally:
            stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await handle.terminate()


__all__ = [
    "TrustTier",
    "GuestSpec",
    "GuestSpecError",
    "GuestResult",
    "GuestHandle",
    "GuestManager",
    "scrub_environment",
    "build_guest_env",
    "redact_secrets",
]
