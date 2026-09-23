"""Process-manager interface for Prismatic's deploy loop.

Every OS-specific service operation the deploy pipeline needs -- restarting
the gateway, polling whether it is active, installing/enabling units -- goes
through this interface instead of shelling to ``systemctl`` directly.

Implementations (present and future):

- ``pe.deploy.process_manager_systemd.SystemdProcessManager`` -- today's
  production behavior on webtop-hermes (``sudo -n systemctl``).
- launchd (macOS) -- DEFERRED, named not built.
- Windows Service -- DEFERRED, named not built.
- plain-subprocess supervisor (for boxes without any service manager) --
  DEFERRED, named not built.

Privilege contract: a manager must NEVER hang on an interactive privilege
prompt (e.g. sudo asking for a password). When an operation needs privileges
it does not have, it raises :class:`ProcessManagerPrivilegeError` -- a typed
subclass of :class:`ProcessManagerError` -- with a message naming the missing
privilege, so callers fail loudly and fast instead of stalling.

Stdlib only -- no prismatic imports -- so this module stays importable in
minimal environments.

Future consumer: prismatic/fleet/manager.py currently shells to raw
systemctl (is-active, restart --no-block, daemon-reload) for
Hermes agent services. That module is out of scope for the deploy loop,
but the method shapes here (restart/is_active/enable/status/install_unit)
were chosen so fleet can adopt this interface later with only small
extensions (e.g. a no_block flag on restart).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProcessManagerError(Exception):
    """Base error for process-manager operations."""


class ProcessManagerPrivilegeError(ProcessManagerError):
    """The manager needs privileges it does not have.

    Raised instead of hanging on an interactive privilege prompt -- e.g. when
    passwordless sudo for the service manager is not configured. The message
    names the missing privilege so the failure is loud and actionable.
    """


class ProcessManager(ABC):
    """Interface every deploy-loop process manager must implement."""

    @abstractmethod
    def restart(self, service: str, *, timeout: int | None = None) -> None:
        """Restart ``service``. Raises ProcessManagerError on failure."""

    @abstractmethod
    def is_active(self, service: str, *, timeout_s: int = 60) -> bool:
        """Poll until ``service`` reports active; return False on timeout."""

    @abstractmethod
    def install_unit(self, name: str, content: str, *, scope: str = "system") -> str:
        """Write the service unit ``name`` with ``content`` and register it.

        Returns the installed unit path. ``scope`` is ``"system"`` or
        ``"user"`` (manager-specific meaning); unknown scopes raise
        ProcessManagerError.
        """

    @abstractmethod
    def enable(self, service: str) -> None:
        """Enable ``service`` to start on boot. Raises ProcessManagerError."""

    @abstractmethod
    def status(self, service: str) -> dict[str, Any]:
        """Return a small dict of service state (keys are manager-specific)."""
