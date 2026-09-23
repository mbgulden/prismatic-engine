"""systemd implementation of the ProcessManager interface.

Today's production behavior on webtop-hermes, moved here verbatim:

- ``restart``: ``sudo -n systemctl restart <service>`` (the argv must match
  the NOPASSWD sudoers entry exactly).
- ``is_active``: poll ``sudo -n systemctl is-active <service>`` until the
  output is ``active`` -- per-check timeout, a sleep between attempts, an
  overall deadline, and failures swallowed until the deadline, exactly as
  the old deploy helpers did.

The ``-n`` flag is what makes the privilege contract hold: sudo fails fast
instead of prompting, so nothing here can hang on a password prompt. A
non-zero exit whose stderr says a password is required is surfaced as
:class:`ProcessManagerPrivilegeError` rather than a generic command error,
so callers can name the missing privilege loudly.

Stdlib only -- no prismatic imports -- so this module stays importable in
minimal environments.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from pe.deploy.process_manager import (
    ProcessManager,
    ProcessManagerError,
    ProcessManagerPrivilegeError,
)

_SYSTEMD_ACTIVE_OUTPUT = "active"
_POLL_INTERVAL_S = 3
_IS_ACTIVE_CHECK_TIMEOUT_S = 15
_SUDO_PASSWORD_REQUIRED_MARKER = "a password is required"


def _default_run(argv: list[str], **kwargs: Any) -> Any:
    return subprocess.run(
        argv,
        capture_output=kwargs.get("capture_output", True),
        text=True,
        timeout=kwargs.get("timeout"),
        cwd=kwargs.get("cwd"),
    )


class SystemdProcessManager(ProcessManager):
    """ProcessManager backed by ``sudo -n systemctl``."""

    def __init__(
        self,
        systemctl_bin: str = "/usr/bin/systemctl",
        run: Any | None = None,
    ) -> None:
        self.systemctl_bin = systemctl_bin
        self._run = run or _default_run

    # ------------------------------------------------------------------
    # command plumbing (verbatim behavior of the old deploy helpers)
    # ------------------------------------------------------------------

    def _run_checked(self, argv: list[str], timeout: int | None = None) -> Any:
        try:
            cp = self._run(argv, timeout=timeout)
        except Exception as exc:
            raise ProcessManagerError(
                f"command failed to launch {' '.join(argv[:3])}: {exc}"
            )
        returncode = getattr(cp, "returncode", 1)
        if returncode != 0:
            err = (getattr(cp, "stderr", "") or "").strip()[:500]
            if _SUDO_PASSWORD_REQUIRED_MARKER in err:
                raise ProcessManagerPrivilegeError(
                    f"systemd manager needs privilege: {' '.join(argv[:4])} "
                    f"failed: {err} -- configure passwordless sudo for "
                    "systemctl (the manager never prompts)"
                )
            raise ProcessManagerError(
                f"command exited {returncode}: {' '.join(argv[:4])} :: {err}"
            )
        return cp

    def _run_out(self, argv: list[str], timeout: int | None = None) -> str:
        cp = self._run_checked(argv, timeout=timeout)
        return (getattr(cp, "stdout", "") or "").strip()

    # ------------------------------------------------------------------
    # ProcessManager interface
    # ------------------------------------------------------------------

    def restart(self, service: str, *, timeout: int | None = None) -> None:
        # argv must match the NOPASSWD sudoers entry exactly
        self._run_checked(
            ["sudo", "-n", self.systemctl_bin, "restart", service], timeout=timeout
        )

    def is_active(self, service: str, *, timeout_s: int = 60) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                out = self._run_out(
                    ["sudo", "-n", self.systemctl_bin, "is-active", service],
                    timeout=_IS_ACTIVE_CHECK_TIMEOUT_S,
                )
                if out == _SYSTEMD_ACTIVE_OUTPUT:
                    return True
            except ProcessManagerError:
                pass
            time.sleep(_POLL_INTERVAL_S)
        return False

    def enable(self, service: str) -> None:
        self._run_checked(
            ["sudo", "-n", self.systemctl_bin, "enable", service], timeout=120
        )

    def install_unit(self, name: str, content: str, *, scope: str = "system") -> str:
        if scope == "user":
            unit_dir = Path.home() / ".config" / "systemd" / "user"
            reload_argv = [self.systemctl_bin, "--user", "daemon-reload"]
        elif scope == "system":
            unit_dir = Path("/etc/systemd/system")
            reload_argv = ["sudo", "-n", self.systemctl_bin, "daemon-reload"]
        else:
            raise ProcessManagerError(f"unknown unit scope: {scope!r}")
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_path = unit_dir / name
        unit_path.write_text(content)
        self._run_checked(reload_argv, timeout=60)
        return str(unit_path)

    def status(self, service: str) -> dict[str, Any]:
        cp = self._run_checked(
            [
                "sudo",
                "-n",
                self.systemctl_bin,
                "show",
                service,
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "LoadState",
            ],
            timeout=30,
        )
        info: dict[str, Any] = {"service": service}
        for line in (getattr(cp, "stdout", "") or "").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                info[key.strip()] = value.strip()
        return info
