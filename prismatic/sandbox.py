"""
Dynamic Sandbox Isolation Workspace Manager.

Provisions clean git checkouts in isolated directories for agent code
modification loops.  Every sandbox gets a dedicated directory with a
fresh git clone, an ``AgentContract`` that restricts operations to
that directory, and automatic stale-sandbox reclamation.

Usage::

    from prismatic.sandbox import SandboxManager

    mgr = SandboxManager(state_dir="/var/lib/prismatic/sandboxes")
    sandbox = mgr.provision(
        repo_url="https://github.com/org/repo.git",
        agent_id="ned",
        issue_id="GRO-1234",
    )
    # sandbox.path  → /var/lib/prismatic/sandboxes/ned-GRO-1234-abc123
    # sandbox.contract.allowed_dirs → [<sandbox.path>]

    # ... agent works inside sandbox.path ...

    mgr.cleanup(sandbox.id)   # removes clone, frees disk
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.interface.plugin import AgentContract

logger = logging.getLogger("prismatic.sandbox")

# ── Constants ──────────────────────────────────────────────────────────
DEFAULT_STALE_TIMEOUT_S = 3600  # 1 hour
DEFAULT_MAX_SANDBOXES = 20


# ── Dataclasses ────────────────────────────────────────────────────────

@dataclass
class SandboxConfig:
    """Configuration for the SandboxManager."""

    state_dir: str = "/var/lib/prismatic/sandboxes"
    """Directory where sandbox clones live."""

    stale_timeout_s: int = DEFAULT_STALE_TIMEOUT_S
    """Seconds before a sandbox is considered stale."""

    max_sandboxes: int = DEFAULT_MAX_SANDBOXES
    """Maximum concurrent sandboxes before provisioning is refused."""

    default_branch: str = "main"
    """Default branch to clone if not specified."""

    shallow_clone: bool = True
    """Use --depth 1 clones for speed."""


@dataclass
class Sandbox:
    """A provisioned sandbox workspace."""

    id: str
    """Unique sandbox identifier."""

    agent_id: str
    """Agent that owns this sandbox."""

    issue_id: str
    """Linear issue this sandbox was provisioned for."""

    repo_url: str
    """Git repository URL that was cloned."""

    branch: str
    """Branch checked out."""

    path: str
    """Absolute path to the sandbox root."""

    contract: AgentContract
    """Contract restricting file access to the sandbox directory."""

    created_at: str
    """ISO-8601 creation timestamp."""

    last_heartbeat: str
    """ISO-8601 last-activity timestamp."""

    commit_sha: str = ""
    """HEAD commit SHA at time of provisioning."""

    @property
    def age_seconds(self) -> float:
        """Seconds since creation."""
        created = datetime.fromisoformat(self.created_at)
        return (datetime.now(timezone.utc) - created).total_seconds()

    def is_stale(self, timeout_s: int = DEFAULT_STALE_TIMEOUT_S) -> bool:
        """True if the sandbox hasn't had a heartbeat within *timeout_s*."""
        last = datetime.fromisoformat(self.last_heartbeat)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed > timeout_s

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "issue_id": self.issue_id,
            "repo_url": self.repo_url,
            "branch": self.branch,
            "path": self.path,
            "created_at": self.created_at,
            "last_heartbeat": self.last_heartbeat,
            "commit_sha": self.commit_sha,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], contract: AgentContract | None = None) -> Sandbox:
        """Deserialize from a dictionary, optionally with a fresh contract."""
        if contract is None:
            contract = AgentContract(
                thread_id=data.get("issue_id", "unknown"),
                persona_id=data.get("agent_id", "unknown"),
                allowed_dirs=[data["path"]],
                max_actions=50,
                execution_env="sandbox",
            )
        return cls(
            id=data["id"],
            agent_id=data["agent_id"],
            issue_id=data["issue_id"],
            repo_url=data["repo_url"],
            branch=data.get("branch", "main"),
            path=data["path"],
            contract=contract,
            created_at=data["created_at"],
            last_heartbeat=data["last_heartbeat"],
            commit_sha=data.get("commit_sha", ""),
        )


# ── SandboxManager ─────────────────────────────────────────────────────

class SandboxProvisionError(Exception):
    """Raised when sandbox provisioning fails."""


class SandboxLimitError(Exception):
    """Raised when the maximum concurrent sandbox count is reached."""


class SandboxManager:
    """
    Manages the lifecycle of isolated agent sandboxes.

    Each sandbox is a clean git clone in a dedicated directory under
    *state_dir*.  The manager persists sandbox metadata to a JSON
    registry so state survives restarts.
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self._config = config or SandboxConfig()
        self._lock = threading.RLock()
        self._state_file = Path(self._config.state_dir) / "sandbox_registry.json"
        self._sandboxes: dict[str, Sandbox] = {}

        # Ensure state dir exists
        Path(self._config.state_dir).mkdir(parents=True, exist_ok=True)

        # Load existing sandboxes from disk
        self._load()

    # ── Public API ─────────────────────────────────────────────────

    def provision(
        self,
        repo_url: str,
        agent_id: str,
        issue_id: str,
        *,
        branch: str | None = None,
        shallow: bool | None = None,
    ) -> Sandbox:
        """
        Provision a new sandbox with a clean git clone of *repo_url*.

        Parameters
        ----------
        repo_url:
            Git repository URL (HTTPS or SSH).
        agent_id:
            Identifier of the agent that will use this sandbox (e.g. ``"ned"``).
        issue_id:
            Linear issue identifier for audit trail.
        branch:
            Branch to clone.  Defaults to *config.default_branch*.
        shallow:
            Whether to do a shallow clone.  Defaults to *config.shallow_clone*.

        Returns
        -------
        Sandbox
            The provisioned sandbox with path, contract, and metadata.

        Raises
        ------
        SandboxLimitError
            If the maximum concurrent sandbox count would be exceeded.
        SandboxProvisionError
            If git clone fails.
        """
        with self._lock:
            # ── Pre-flight: check capacity ─────────────────────────
            self._prune_stale_sandboxes()
            if len(self._sandboxes) >= self._config.max_sandboxes:
                raise SandboxLimitError(
                    f"Maximum sandbox count ({self._config.max_sandboxes}) reached. "
                    f"Clean up stale sandboxes or increase max_sandboxes."
                )

            # ── Generate sandbox identity ──────────────────────────
            branch_name = branch or self._config.default_branch
            do_shallow = shallow if shallow is not None else self._config.shallow_clone
            sandbox_id = self._generate_id(agent_id, issue_id)
            sandbox_dir = Path(self._config.state_dir) / sandbox_id

            now_iso = datetime.now(timezone.utc).isoformat()

            # ── Clone the repo ─────────────────────────────────────
            try:
                sandbox_dir.mkdir(parents=True, exist_ok=False)
                self._clone_repo(repo_url, branch_name, do_shallow, sandbox_dir)
                commit_sha = self._get_head_sha(sandbox_dir)
            except Exception as exc:
                # Best-effort cleanup on failure
                if sandbox_dir.exists():
                    shutil.rmtree(sandbox_dir, ignore_errors=True)
                raise SandboxProvisionError(
                    f"Failed to clone {repo_url} (branch={branch_name}): {exc}"
                ) from exc

            # ── Build the sandbox record ───────────────────────────
            contract = AgentContract(
                thread_id=issue_id,
                persona_id=agent_id,
                allowed_dirs=[str(sandbox_dir)],
                max_actions=50,
                execution_env="sandbox",
            )

            sandbox = Sandbox(
                id=sandbox_id,
                agent_id=agent_id,
                issue_id=issue_id,
                repo_url=repo_url,
                branch=branch_name,
                path=str(sandbox_dir),
                contract=contract,
                created_at=now_iso,
                last_heartbeat=now_iso,
                commit_sha=commit_sha,
            )

            self._sandboxes[sandbox_id] = sandbox
            self._persist()

            logger.info(
                "Sandbox provisioned: %s (%s) → %s [commit %s]",
                sandbox_id, repo_url, sandbox_dir, commit_sha[:8],
            )
            return sandbox

    def list_sandboxes(
        self, agent_id: str | None = None, include_stale: bool = True
    ) -> list[Sandbox]:
        """
        List all tracked sandboxes, optionally filtered by *agent_id*.

        Parameters
        ----------
        agent_id:
            If provided, only return sandboxes for this agent.
        include_stale:
            If False, exclude stale sandboxes.
        """
        with self._lock:
            sandboxes = list(self._sandboxes.values())
            if agent_id:
                sandboxes = [s for s in sandboxes if s.agent_id == agent_id]
            if not include_stale:
                timeout = self._config.stale_timeout_s
                sandboxes = [s for s in sandboxes if not s.is_stale(timeout)]
            return sandboxes

    def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get a sandbox by its unique ID."""
        with self._lock:
            return self._sandboxes.get(sandbox_id)

    def heartbeat(self, sandbox_id: str) -> bool:
        """
        Update the last-heartbeat timestamp for a sandbox.

        Returns True if the sandbox was found and updated.
        """
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is None:
                return False
            sandbox.last_heartbeat = datetime.now(timezone.utc).isoformat()
            self._persist()
            return True

    def cleanup(self, sandbox_id: str) -> bool:
        """
        Remove a sandbox: delete its directory and registry entry.

        Returns True if the sandbox was found and cleaned up.
        """
        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
            if sandbox is None:
                logger.warning("Sandbox %s not found for cleanup.", sandbox_id)
                return False

            sandbox_path = Path(sandbox.path)
            if sandbox_path.exists():
                shutil.rmtree(sandbox_path, ignore_errors=True)
                logger.info(
                    "Sandbox directory removed: %s (%s)",
                    sandbox_id, sandbox_path,
                )

            self._persist()
            logger.info("Sandbox cleaned up: %s", sandbox_id)
            return True

    def cleanup_stale(self) -> int:
        """
        Remove all stale sandboxes.

        Returns the number of sandboxes cleaned up.
        """
        with self._lock:
            timeout = self._config.stale_timeout_s
            stale_ids = [
                sid for sid, sb in self._sandboxes.items()
                if sb.is_stale(timeout)
            ]
            count = 0
            for sid in stale_ids:
                if self.cleanup(sid):
                    count += 1
            if count:
                logger.info("Stale cleanup: removed %d sandbox(es).", count)
            return count

    def cleanup_all(self) -> int:
        """Remove ALL sandboxes.  Returns count cleaned up."""
        with self._lock:
            all_ids = list(self._sandboxes.keys())
            count = 0
            for sid in all_ids:
                if self.cleanup(sid):
                    count += 1
            return count

    # ── Internal helpers ──────────────────────────────────────────

    def _generate_id(self, agent_id: str, issue_id: str) -> str:
        """Generate a unique sandbox ID."""
        suffix = secrets.token_hex(4)
        safe_agent = "".join(c for c in agent_id if c.isalnum() or c in "-_")
        safe_issue = "".join(c for c in issue_id if c.isalnum() or c in "-_")
        return f"{safe_agent}-{safe_issue}-{suffix}"

    @staticmethod
    def _clone_repo(
        repo_url: str, branch: str, shallow: bool, target_dir: Path
    ) -> None:
        """Clone *repo_url* into *target_dir*."""
        cmd = ["git", "clone", "--branch", branch]
        if shallow:
            cmd.append("--depth=1")
        cmd.extend([repo_url, str(target_dir)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout for clones
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise SandboxProvisionError(
                f"git clone failed (exit {result.returncode}): {stderr}"
            )

    @staticmethod
    def _get_head_sha(repo_dir: Path) -> str:
        """Get the HEAD commit SHA from a git repository."""
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""

    def _prune_stale_sandboxes(self) -> int:
        """Remove stale sandboxes.  Must be called under _lock."""
        timeout = self._config.stale_timeout_s
        stale_ids = [
            sid for sid, sb in self._sandboxes.items()
            if sb.is_stale(timeout)
        ]
        count = 0
        for sid in stale_ids:
            sb = self._sandboxes.pop(sid, None)
            if sb:
                sandbox_path = Path(sb.path)
                if sandbox_path.exists():
                    shutil.rmtree(sandbox_path, ignore_errors=True)
                count += 1
        if count:
            self._persist()
        return count

    def _persist(self) -> None:
        """Write the sandbox registry to disk (atomically)."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        data = {
            "sandboxes": [sb.to_dict() for sb in self._sandboxes.values()],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._state_file)

    def _load(self) -> None:
        """Load the sandbox registry from disk."""
        if not self._state_file.exists():
            logger.debug("No sandbox registry found at %s — starting fresh.", self._state_file)
            return

        try:
            with open(self._state_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load sandbox registry: %s", exc)
            return

        loaded = 0
        for entry in data.get("sandboxes", []):
            sid = entry.get("id")
            if not sid:
                continue
            # Reconstruct contract
            contract = AgentContract(
                thread_id=entry.get("issue_id", "unknown"),
                persona_id=entry.get("agent_id", "unknown"),
                allowed_dirs=[entry["path"]],
                max_actions=50,
                execution_env="sandbox",
            )
            try:
                sandbox = Sandbox.from_dict(entry, contract=contract)
                self._sandboxes[sid] = sandbox
                loaded += 1
            except Exception as exc:
                logger.warning("Skipping corrupt sandbox entry %s: %s", sid, exc)

        if loaded:
            logger.info("Loaded %d sandbox(es) from registry.", loaded)

        # Auto-prune stale on load
        pruned = self._prune_stale_sandboxes()
        if pruned:
            logger.info("Pruned %d stale sandbox(es) on load.", pruned)


# ── Singleton helper ───────────────────────────────────────────────────

_global_sandbox_manager: SandboxManager | None = None
_global_sandbox_lock = threading.Lock()


def get_sandbox_manager(config: SandboxConfig | None = None) -> SandboxManager:
    """Return a shared singleton ``SandboxManager``."""
    global _global_sandbox_manager
    if _global_sandbox_manager is None:
        with _global_sandbox_lock:
            if _global_sandbox_manager is None:
                _global_sandbox_manager = SandboxManager(config=config)
    return _global_sandbox_manager
