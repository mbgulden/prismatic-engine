"""Tailnet node executor for the deploy plane (WS7).

Runs a repo's full deploy cycle -- build on-node from the node's own
mirror (reusing WS1/WS5 logic), restart via the WS2 process-manager
abstraction, 90s health poll over the tailnet, rollback on failure --
against a registered tailnet node, over SSH.

Design decisions (all deliberate):

* **Transport is genuinely new**: no paramiko/asyncssh exists anywhere in
  the repo, so this module wraps the system ``ssh`` binary via
  ``subprocess`` -- **zero new dependencies**. ``SubprocessSSHTransport.run``
  has the same ``(argv, **kwargs)`` shape as the callables
  ``GatewayRedeployer`` and ``SystemdProcessManager`` already accept, so the
  deploy steps are REUSED, not reimplemented: the same systemctl argv the
  WS2 manager constructs locally is executed over the ssh transport, and
  the same ``GatewayRedeployer`` state machine (verify SHA -> worktree ->
  wheel -> venv -> stage -> flip -> restart -> health -> rollback) runs
  against the node. Nothing in this module duplicates a deploy-step argv
  string or a health-poll budget.
* **Fail-closed SSH**: ``BatchMode=yes`` always (never prompts -- a prompt
  would hang the deploy), ``StrictHostKeyChecking=yes`` by default so an
  unknown host key refuses the connection. Pin node host keys in
  ``$PRISMATIC_SSH_KNOWN_HOSTS`` (default ``~/.ssh/known_hosts``); only an
  operator-edited known-hosts file can add a key. Every remote call carries
  a timeout: ``ConnectTimeout`` for the handshake plus an overall
  per-call cap.
* **Key scope** (documented, not enforced in code): the SSH identity the
  control plane uses should be a dedicated deploy key, ideally restricted
  on the node (``command=``/``from=`` in ``authorized_keys`` or Tailscale
  SSH with tailnet ACLs). ``ssh_method: tailscale-ssh`` avoids long-lived
  key material entirely -- auth rides the tailnet identity -- and is the
  default for new enrollments.
* **Health truth**: the node's own HTTP health endpoint, polled over the
  tailnet at ``http://<address>:<port>/health`` (per repo health
  endpoints), is the source of truth. The per-endpoint 90s poll budget is
  unchanged from the local path.
* **Locks**: the deploy lock is taken on the control plane per
  ``(node, release_prefix)`` -- the remote work executes from here, so a
  local lock serializes deploys to the same node correctly.

Terminology: ``node_executor`` -- never "hypervisor"; ``prismatic/hypervisor/``
is the unrelated guest-process containment work.

Stdlib only (plus ``pe.deploy.*``, which are stdlib-only) -- no
``prismatic`` imports at module scope (the mesh client is imported lazily
inside the reachability probe).
"""

from __future__ import annotations

import fcntl
import logging
import os
import shlex
import subprocess
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pe.deploy import config as deploy_config
from pe.deploy.deploy_alerts import emit_deploy_alert
from pe.deploy.gateway_redeploy import GatewayRedeployer, GatewayDeployResult
from pe.deploy.nodes import (
    DeployNode,
    UnresolvableNodeError,
    ping_node,
    resolve_address,
)
from pe.deploy.process_manager_systemd import SystemdProcessManager

logger = logging.getLogger(__name__)

#: Env var overriding the SSH known-hosts file used for host-key pinning.
SSH_KNOWN_HOSTS_ENV_VAR = "PRISMATIC_SSH_KNOWN_HOSTS"

#: Default overall cap (seconds) for one remote ssh call when the caller
#: passes no timeout. Every remote call is bounded -- see module docstring.
DEFAULT_SSH_CALL_TIMEOUT_S = 900

#: Handshake budget (seconds) for the ssh connection phase.
SSH_CONNECT_TIMEOUT_S = 10

#: Lock file pattern on the control plane for per-node deploy serialization.
NODE_LOCK_NAME = "node-deploy-{node}-{prefix}.lock"


class SSHError(Exception):
    """A remote ssh call failed (transport-level)."""


class NodeUnreachableError(SSHError):
    """The node failed the reachability probe -- refused before any action."""


def probe_ssh(
    node: DeployNode,
    address: str,
    timeout_s: int = 10,
    run: Callable[..., Any] | None = None,
) -> bool:
    """Observation-only SSH reachability probe: run ``true`` on the node.

    Uses the same fail-closed flags as the transport (BatchMode, strict
    host-key checking, bounded timeout). Returns a bool -- the caller names
    the failing node. ``run`` is a test seam for the subprocess call.
    """
    transport = SubprocessSSHTransport(
        node,
        address=address,
        connect_timeout_s=min(timeout_s, SSH_CONNECT_TIMEOUT_S),
        run=run,
    )
    try:
        proc = transport.run(["true"], timeout=timeout_s)
    except SSHError:
        return False
    return getattr(proc, "returncode", 1) == 0


def run_remote_command(
    node: DeployNode,
    address: str,
    argv: list[str],
    timeout: int | None = None,
    run: Callable[..., Any] | None = None,
) -> Any:
    """Run one command on the node and return the completed-process object.

    Raises :class:`SSHError` when ssh fails to launch. Thin wrapper around
    the transport for preflight probes (``uname -s``, ``command -v``).
    """
    transport = SubprocessSSHTransport(node, address=address, run=run)
    return transport.run(argv, timeout=timeout)


def default_known_hosts() -> Path:
    """Host-key pinning source: ``$PRISMATIC_SSH_KNOWN_HOSTS`` or ~/.ssh/known_hosts."""
    raw = os.environ.get(SSH_KNOWN_HOSTS_ENV_VAR, "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".ssh" / "known_hosts"


class SubprocessSSHTransport:
    """``ssh``-over-subprocess transport with fail-closed security defaults.

    ``run(argv, timeout=None, cwd=None)`` executes ``argv`` on the remote
    node and returns a ``subprocess.CompletedProcess``-shaped object. The
    signature matches the ``run`` callables ``GatewayRedeployer`` and
    ``SystemdProcessManager`` accept, so both run unchanged over this
    transport.

    Security posture (all fail-closed):

    * ``-o BatchMode=yes``: never prompts for a password/passphrase -- a
      missing credential fails fast instead of hanging the deploy.
    * ``-o StrictHostKeyChecking=yes``: an unknown host key REFUSES the
      connection. Pin keys in ``$PRISMATIC_SSH_KNOWN_HOSTS`` (or
      ``~/.ssh/known_hosts``). There is deliberately no "accept-new" mode.
    * ``-o ConnectTimeout=<n>`` on every call, plus an overall per-call
      timeout (``DEFAULT_SSH_CALL_TIMEOUT_S`` when the caller passes none).
    * Key method: ``-i <ssh_key>`` + ``-o IdentitiesOnly=yes`` so only the
      node's dedicated key is offered -- no agent key spray.
    * Tailscale-ssh method: no ``-i`` at all; auth rides the tailnet
      identity and the node's tailnet SSH ACLs.
    """

    def __init__(
        self,
        node: DeployNode,
        address: str | None = None,
        known_hosts: Path | str | None = None,
        connect_timeout_s: int = SSH_CONNECT_TIMEOUT_S,
        default_timeout_s: int = DEFAULT_SSH_CALL_TIMEOUT_S,
        run: Callable[..., Any] | None = None,
    ) -> None:
        if node.is_local:
            raise SSHError("refusing to build an ssh transport for the local node")
        self.node = node
        self.address = address or node.address
        self.known_hosts = (
            Path(known_hosts).expanduser()
            if known_hosts is not None
            else default_known_hosts()
        )
        self.connect_timeout_s = connect_timeout_s
        self.default_timeout_s = default_timeout_s
        self._run = run or self._subprocess_run
        if node.ssh_method == "key" and node.ssh_key is None:
            raise SSHError(
                f"node {node.name!r}: ssh_method='key' requires ssh_key -- "
                "refusing to build a transport without key material"
            )

    @staticmethod
    def _subprocess_run(argv: list[str], **kwargs: Any) -> Any:
        return subprocess.run(
            argv,
            capture_output=kwargs.get("capture_output", True),
            text=True,
            timeout=kwargs.get("timeout"),
        )

    def ssh_argv(self, remote_argv: list[str], cwd: str | None = None) -> list[str]:
        """Build the full ``ssh`` argv for one remote command (pure).

        Kept pure (no subprocess) so tests assert the exact security flags.
        """
        argv: list[str] = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            f"ConnectTimeout={self.connect_timeout_s}",
        ]
        if self.node.ssh_method == "key":
            argv += [
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(self.node.ssh_key),
            ]
        # (tailscale-ssh: no -i; auth rides the tailnet identity.)
        argv.append(self.node.ssh_target(self.address))
        command = " ".join(shlex.quote(a) for a in remote_argv)
        if cwd:
            command = f"cd {shlex.quote(cwd)} && exec {command}"
        argv += ["--", command]
        return argv

    def run(
        self,
        argv: list[str],
        timeout: int | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Execute ``argv`` on the node. Never prompts; always bounded."""
        full_argv = self.ssh_argv(list(argv), cwd=cwd)
        bound = timeout if timeout is not None else self.default_timeout_s
        try:
            return self._run(full_argv, timeout=bound, capture_output=True)
        except Exception as exc:
            raise SSHError(
                f"ssh to {self.node.ssh_target(self.address)} failed to launch: {exc}"
            ) from exc


@dataclass
class NodeDeployResult:
    """Outcome of one remote deploy cycle."""

    node: str
    success: bool
    pr_sha: str = ""
    gateway: GatewayDeployResult | None = None
    reason: str = ""
    rolled_back: bool = False
    duration_ms: int = 0
    dry_run: bool = False


class NodeDeployer:
    """Runs a repo's deploy cycle against one tailnet node.

    Reuses ``GatewayRedeployer`` end to end -- the same verify/build/flip/
    restart/health/rollback state machine -- with an ssh-backed ``run``
    callable and a tailnet ``http_get``. The WS2 ``SystemdProcessManager``
    constructs the restart argv (the ``sudo -n systemctl`` strings match the
    node's NOPASSWD sudoers entry); the transport only executes them.
    """

    def __init__(
        self,
        node: DeployNode,
        repo_config: deploy_config.DeployRepoConfig,
        transport: SubprocessSSHTransport | None = None,
        mesh_client: Any | None = None,
        alert_log: Path | str | None = None,
        redeployer_factory: Callable[..., GatewayRedeployer] | None = None,
    ) -> None:
        if node.is_local:
            raise SSHError("NodeDeployer is for remote nodes; 'local' is invalid here")
        self.node = node
        self.repo = repo_config
        self.mesh_client = mesh_client
        self._alert_log = Path(alert_log) if alert_log is not None else None
        self._transport = transport
        self._redeployer_factory = redeployer_factory
        self._resolved_address: str | None = None

    # ------------------------------------------------------------------
    # reachability (fail-closed, before any remote action)
    # ------------------------------------------------------------------

    def resolved_address(self) -> str:
        """Dialable tailnet address for the node (mesh-resolved, cached)."""
        if self._resolved_address is None:
            self._resolved_address = resolve_address(self.node, self.mesh_client)
        return self._resolved_address

    def check_reachable(self) -> dict[str, Any]:
        """Probe mesh reachability + ssh reachability. Fail-closed.

        Returns ``{"ok": True, ...}`` on success; raises
        :class:`NodeUnreachableError` naming the exact failure -- the caller
        must refuse the deploy BEFORE any remote action on this signal.
        """
        # 1. Address must resolve via the mesh client (never DNS-guessed).
        try:
            address = self.resolved_address()
        except UnresolvableNodeError as exc:
            raise NodeUnreachableError(str(exc)) from exc
        # 2. Mesh ping: the node must be up on the tailnet.
        ping = ping_node(self.node, self.mesh_client)
        if not ping.get("success"):
            raise NodeUnreachableError(
                f"node {self.node.name!r} unreachable over tailnet "
                f"(mesh ping to {address} failed: {ping.get('error', 'no pong')})"
            )
        # 3. SSH must answer (BatchMode: no prompts, short timeout).
        transport = self._transport_for(address)
        probe = transport.run(["true"], timeout=30)
        if getattr(probe, "returncode", 1) != 0:
            err = (getattr(probe, "stderr", "") or "").strip()[:300]
            raise NodeUnreachableError(
                f"node {self.node.name!r} ssh probe failed "
                f"({self.node.ssh_target(address)}): rc={probe.returncode} {err}"
            )
        return {"ok": True, "address": address, "ping": ping}

    # ------------------------------------------------------------------
    # deploy cycle
    # ------------------------------------------------------------------

    def _transport_for(self, address: str) -> SubprocessSSHTransport:
        if self._transport is not None:
            return self._transport
        return SubprocessSSHTransport(self.node, address=address)

    def _tailnet_http_get(self, url: str, timeout: int = 5) -> tuple[bool, str]:
        """HTTP GET against the node's tailnet address (never localhost).

        ``GatewayRedeployer._poll_http`` builds ``http://localhost:<port><path>``;
        the netloc is rewritten to the node's address here, keeping the
        90s per-endpoint poll budget byte-identical to the local path.
        """
        address = self.resolved_address()
        port = self.node.gateway_port or deploy_config.gateway_port()
        parts = urllib.parse.urlsplit(url)
        rewritten = urllib.parse.urlunsplit(
            (parts.scheme or "http", f"{address}:{port}", parts.path, parts.query, "")
        )
        try:
            req = urllib.request.Request(
                rewritten, headers={"User-Agent": "Prismatic-NodeDeploy-Health/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.getcode()
                if 200 <= code < 400:
                    return True, f"HTTP {code}"
                return False, f"HTTP status {code}"
        except Exception as exc:
            return False, f"Connection error: {exc}"

    def _remote_mirror_dir(self) -> Path:
        """The repo's mirror ON the node (WS1/WS5 logic runs there).

        Mirrors the control-plane convention: the node's own
        ``~/.prismatic/repos/<owner>/<repo>`` under the node's home. The
        node keeps its own mirror fresh (its own fetch timer); the remote
        ``_verify_sha`` does a pre-deploy fetch, so the merge->deploy race
        fix applies on the node exactly as locally.
        """
        owner, repo = self.repo.full_name.split("/")
        return self.node.remote_home / ".prismatic" / "repos" / owner / repo

    def _alert(self, name: str, severity: str, summary: str, details: str) -> None:
        tagged = f"node={self.node.name} {details}".strip()
        try:
            emit_deploy_alert(name, severity, summary, tagged, log_path=self._alert_log)
        except Exception as exc:  # pragma: no cover - emit never raises
            logger.warning("node-executor: alert emit failed: %s", exc)

    @contextmanager
    def _node_locked(self):  # type: ignore[no-untyped-def]
        """Per-(node, release_prefix) deploy lock, taken on the control plane."""
        run_dir = deploy_config.state_dir() / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = run_dir / NODE_LOCK_NAME.format(
            node=self.node.name, prefix=self.repo.release_prefix
        )
        fh = open(lock_path, "a+")
        try:
            deadline = time.monotonic() + 1800
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise SSHError(
                            f"timed out waiting for node-deploy lock {lock_path}"
                        )
                    time.sleep(2)
            fh.write(f"{os.getpid()} {time.time()}\n")
            fh.flush()
            yield
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()

    def deploy(self, pr_sha: str, dry_run: bool = False) -> NodeDeployResult:
        """Run the remote deploy cycle. Never raises; result carries all.

        Reachability is verified FIRST (fail-closed); no remote action
        happens for an unknown/unresolvable/unreachable node -- the
        invariant holds for direct callers too, not just the receiver.
        ``dry_run=True`` short-circuits before the reachability probe:
        zero remote actions of any kind.
        """
        started = time.time()
        result = NodeDeployResult(
            node=self.node.name, success=False, pr_sha=str(pr_sha or "")
        )
        if dry_run:
            # Zero remote actions: no reachability probe, no ssh, no deploy.
            # The receiver resolves the node from the registry before it
            # ever calls this; direct callers get the same guarantee.
            result.success = True
            result.dry_run = True
            result.reason = "dry-run: no remote actions taken"
            result.duration_ms = int((time.time() - started) * 1000)
            self._alert(
                "NodeDeployDryRun",
                "info",
                f"node deploy dry-run on {self.node.name}: "
                f"{self.repo.full_name}@{str(pr_sha)[:12]}",
                f"repository={self.repo.full_name} pr_sha={pr_sha}",
            )
            return result
        self._alert(
            "NodeDeployStarted",
            "info",
            f"node deploy started on {self.node.name}: {self.repo.full_name}@{str(pr_sha)[:12]}",
            f"repository={self.repo.full_name} pr_sha={pr_sha}",
        )
        try:
            reachability = self.check_reachable()
        except NodeUnreachableError as exc:
            result.reason = (
                f"refused: target node {self.node.name!r} unreachable: {exc}"
            )
            result.duration_ms = int((time.time() - started) * 1000)
            self._alert(
                "NodeDeployFailed",
                "critical",
                f"node deploy refused: {self.node.name} unreachable",
                f"repository={self.repo.full_name} pr_sha={pr_sha} "
                f"reason={result.reason[:300]}",
            )
            return result
        address = reachability["address"]

        transport = self._transport_for(address)
        manager = SystemdProcessManager(run=transport.run)
        factory = self._redeployer_factory or GatewayRedeployer
        redeployer = factory(
            home=str(self.node.remote_home),
            run=transport.run,
            http_get=self._tailnet_http_get,
            service=self.repo.target_service,
            port=self.node.gateway_port or deploy_config.gateway_port(),
            health_endpoints=self.repo.health_endpoints,
            manager=manager,
        )
        try:
            with self._node_locked():
                gw = redeployer.redeploy(
                    pr_sha, repo=self._remote_mirror_dir(), repo_config=self.repo
                )
        except Exception as exc:
            result.reason = f"node deploy errored: {exc}"
            result.duration_ms = int((time.time() - started) * 1000)
            self._alert(
                "NodeDeployFailed",
                "critical",
                f"node deploy errored on {self.node.name}: {str(exc)[:120]}",
                f"repository={self.repo.full_name} pr_sha={pr_sha} reason={result.reason[:300]}",
            )
            return result

        result.gateway = gw
        result.rolled_back = gw.rolled_back
        result.duration_ms = int((time.time() - started) * 1000)
        if gw.success:
            result.success = True
            result.reason = f"node {self.node.name}: {gw.reason}"
            self._alert(
                "NodeDeploySucceeded",
                "info",
                f"node deploy succeeded on {self.node.name}: {self.repo.full_name}@{gw.pr_sha[:12]}",
                f"repository={self.repo.full_name} pr_sha={pr_sha} "
                f"version_dir={Path(gw.version_dir).name if gw.version_dir else ''} "
                f"duration_ms={result.duration_ms}",
            )
        else:
            result.reason = f"node {self.node.name}: {gw.reason}" + (
                " [rolled back to previous release]"
                if gw.rolled_back
                else " [ROLLBACK FAILED -- manual recovery required]"
            )
            self._alert(
                "NodeDeployFailed",
                "critical",
                f"node deploy failed on {self.node.name}: {str(gw.reason)[:120]}",
                f"repository={self.repo.full_name} pr_sha={pr_sha} "
                f"reason={str(gw.reason)[:300]} rolled_back={gw.rolled_back}",
            )
        return result
