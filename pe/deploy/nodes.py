"""Tailnet node registry for the deploy plane (WS7).

Explicit, user-managed registry of the Tailscale nodes the control plane
may deploy to. Read from ``$PRISMATIC_NODES_FILE``, defaulting to
``~/.prismatic/nodes.yaml``.

Discovery/reachability REUSE ``prismatic.mesh.tailscale.TailscaleMeshClient``
(lazy import, inside the resolve helpers) -- this module builds no parallel
discovery mechanism. SSH is the transport (see ``pe.deploy.node_executor``).

Field alignment with ``prismatic/distributed_watchdog.py``
----------------------------------------------------------
The watchdog keeps an ephemeral roster at ``/tmp/prismatic/swarm_nodes.json``
(``{"nodes": [{"node_id": ..., "hostname": ..., ...}]}``) that it *writes
itself* every cycle for agent failover. This registry is deliberately NOT
that roster:

* ``name`` ~ ``node_id`` -- but human-chosen and stable, not runtime-generated.
* ``address`` replaces ``hostname`` -- an explicit tailnet address (Tailscale
  IP or MagicDNS name) chosen by the operator, not an ephemeral discovered
  hostname.
* This registry is **explicit config**: only the operator (or the WS3
  installer's enroll-node flow) writes it. The watchdog roster is ephemeral
  /tmp state. There is no auto-sync or migration between them; the deploy
  plane's source of truth is this file.

The special name ``"local"`` always resolves to the control plane itself
(the ``target_node="local"`` default every repo config carries) and never
needs registry, mesh, or SSH.

File format: YAML (``nodes:`` list) when PyYAML is importable, otherwise
JSON. JSON is valid YAML, so a JSON file always parses -- the two formats
interoperate.

Stdlib only (the PyYAML/tailscale imports are lazy) so this module stays
importable in the same minimal environments as ``pe.deploy.config``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

#: Env var overriding the node registry path.
NODES_FILE_ENV_VAR = "PRISMATIC_NODES_FILE"

#: Default registry path (operator-managed, never rewritten by deploys).
DEFAULT_NODES_FILE_NAME = "nodes.yaml"

#: Reserved name for the control plane itself (never in the registry file).
LOCAL_NODE_NAME = "local"

#: Supported SSH methods for a node.
SSH_METHOD_TAILSCALE_SSH = "tailscale-ssh"
SSH_METHOD_KEY = "key"
SSH_METHODS = (SSH_METHOD_TAILSCALE_SSH, SSH_METHOD_KEY)

#: Supported node platforms (macOS/Windows deferred per the plan).
PLATFORM_LINUX_SYSTEMD = "linux-systemd"
PLATFORMS = (PLATFORM_LINUX_SYSTEMD,)

#: Registry names: dns-safe slugs, also safe as SSH/log identifiers.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class NodeRegistryError(ValueError):
    """The node registry file is unreadable or invalid."""


class UnknownNodeError(KeyError):
    """A deploy named a target node that is not registered."""


class UnresolvableNodeError(NodeRegistryError):
    """The node's tailnet address cannot be resolved via the mesh client."""


def nodes_file_path() -> Path:
    """Registry path: ``$PRISMATIC_NODES_FILE`` or ``~/.prismatic/nodes.yaml``."""
    raw = os.environ.get(NODES_FILE_ENV_VAR, "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".prismatic" / DEFAULT_NODES_FILE_NAME


@dataclass(frozen=True)
class DeployNode:
    """One registered tailnet deploy target.

    ``address`` is a Tailscale IP (100.64.0.0/10) or a MagicDNS name,
    resolved to a dialable address via the mesh client at deploy time
    (``resolve_address``) -- never DNS-guessed.

    ``ssh_method`` is ``"tailscale-ssh"`` (auth via tailnet identity; no key
    material on the control plane) or ``"key"`` (key-based SSH over the
    tailnet, ``ssh_key`` pointing at the private key file). ``platform`` is
    ``"linux-systemd"`` for now. ``roles`` names the services the node hosts
    (e.g. ``("gateway",)``); the receiver only routes deploys, it does not
    schedule on roles.
    """

    name: str
    address: str
    ssh_user: str
    ssh_method: str = SSH_METHOD_TAILSCALE_SSH
    platform: str = PLATFORM_LINUX_SYSTEMD
    roles: tuple[str, ...] = ("gateway",)
    ssh_key: Path | None = None
    home: Path | None = None
    gateway_port: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ValueError(
                f"invalid node name {self.name!r}: expected a dns-safe slug "
                "(lowercase letters, digits, dashes)"
            )
        if not isinstance(self.address, str) or not self.address.strip():
            raise ValueError(f"node {self.name!r}: address must be a non-empty string")
        if not isinstance(self.ssh_user, str) or not self.ssh_user.strip():
            raise ValueError(f"node {self.name!r}: ssh_user must be a non-empty string")
        if self.ssh_method not in SSH_METHODS:
            raise ValueError(
                f"node {self.name!r}: ssh_method must be one of {SSH_METHODS}, "
                f"got {self.ssh_method!r}"
            )
        if self.platform not in PLATFORMS:
            raise ValueError(
                f"node {self.name!r}: platform must be one of {PLATFORMS}, "
                f"got {self.platform!r}"
            )
        if self.ssh_method == SSH_METHOD_KEY and not self.ssh_key:
            raise ValueError(
                f"node {self.name!r}: ssh_method='key' requires ssh_key "
                "(path to the private key file)"
            )
        object.__setattr__(self, "address", self.address.strip())
        object.__setattr__(self, "ssh_user", self.ssh_user.strip())
        object.__setattr__(self, "roles", tuple(str(r) for r in (self.roles or ())))
        if self.ssh_key is not None:
            object.__setattr__(self, "ssh_key", Path(self.ssh_key).expanduser())
        if self.home is not None:
            object.__setattr__(self, "home", Path(self.home))

    @property
    def is_local(self) -> bool:
        """True for the reserved ``local`` control-plane node."""
        return self.name == LOCAL_NODE_NAME

    @property
    def remote_home(self) -> Path:
        """Home dir on the node; default ``/home/<ssh_user>``."""
        if self.home is not None:
            return self.home
        return Path("/home") / self.ssh_user

    def ssh_target(self, address: str | None = None) -> str:
        """``user@address`` for the ssh transport."""
        return f"{self.ssh_user}@{address or self.address}"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "address": self.address,
            "ssh_user": self.ssh_user,
            "ssh_method": self.ssh_method,
            "platform": self.platform,
            "roles": list(self.roles),
        }
        if self.ssh_key is not None:
            d["ssh_key"] = str(self.ssh_key)
        if self.home is not None:
            d["home"] = str(self.home)
        if self.gateway_port is not None:
            d["gateway_port"] = self.gateway_port
        return d

    @classmethod
    def local(cls) -> "DeployNode":
        """The control plane itself -- always valid, never in the file.

        Built bypassing ``__post_init__`` validation: the local node never
        dials SSH, so ``ssh_user``/``ssh_key`` are meaningless for it.
        """
        node = object.__new__(cls)
        object.__setattr__(node, "name", LOCAL_NODE_NAME)
        object.__setattr__(node, "address", "localhost")
        object.__setattr__(node, "ssh_user", "")
        object.__setattr__(node, "ssh_method", SSH_METHOD_KEY)
        object.__setattr__(node, "platform", PLATFORM_LINUX_SYSTEMD)
        object.__setattr__(node, "roles", ("gateway",))
        object.__setattr__(node, "ssh_key", None)
        object.__setattr__(node, "home", None)
        object.__setattr__(node, "gateway_port", None)
        return node


def _local_node_bypass(name: str) -> DeployNode | None:
    # "local" is constructed directly (bypasses __post_init__'s ssh_key
    # requirement via the local() factory's explicit key-method fields).
    if name == LOCAL_NODE_NAME:
        return DeployNode.local()
    return None


@dataclass(frozen=True)
class NodeRegistry:
    """Explicit node registry. ``"local"`` always resolves; never empty-safe."""

    nodes: tuple[DeployNode, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for node in self.nodes:
            if node.name == LOCAL_NODE_NAME:
                raise ValueError(
                    f"node name {LOCAL_NODE_NAME!r} is reserved for the control "
                    "plane and must not appear in the registry file"
                )
            if node.name in seen:
                raise ValueError(f"duplicate node {node.name!r} in node registry")
            seen.add(node.name)

    def get(self, name: str) -> DeployNode:
        """Return the node ``name``; ``"local"`` and registered nodes only.

        Unknown names raise :class:`UnknownNodeError` -- the fail-closed
        signal the receiver routes on. No guessing, no fallthrough.
        """
        local = _local_node_bypass(name)
        if local is not None:
            return local
        for node in self.nodes:
            if node.name == name:
                return node
        known = ", ".join(n.name for n in self.nodes) or "(none registered)"
        raise UnknownNodeError(
            f"unknown target node {name!r}: not in the node registry "
            f"{nodes_file_path()} (registered: {known})"
        )

    def __contains__(self, name: object) -> bool:
        return name == LOCAL_NODE_NAME or any(n.name == name for n in self.nodes)

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[DeployNode]:
        return iter(self.nodes)


def _parse_registry_text(text: str, source: str) -> dict[str, Any]:
    """Parse YAML (PyYAML) or JSON. JSON is valid YAML, so JSON always works."""
    try:
        import yaml  # type: ignore[import-not-found]

        data = yaml.safe_load(text)
    except ImportError:
        data = json.loads(text)
    except Exception as exc:
        raise NodeRegistryError(
            f"node registry {source} is not valid YAML/JSON: {exc}"
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise NodeRegistryError(
            f"node registry {source} must be a mapping with a 'nodes' list"
        )
    return data


def _node_from_dict(raw: dict[str, Any]) -> DeployNode:
    if not isinstance(raw, dict):
        raise NodeRegistryError(f"node entry must be a mapping, got {raw!r}")
    try:
        return DeployNode(
            name=raw["name"],
            address=raw["address"],
            ssh_user=raw["ssh_user"],
            ssh_method=raw.get("ssh_method", SSH_METHOD_TAILSCALE_SSH),
            platform=raw.get("platform", PLATFORM_LINUX_SYSTEMD),
            roles=tuple(raw.get("roles", ["gateway"])),
            ssh_key=raw.get("ssh_key"),
            home=raw.get("home"),
            gateway_port=raw.get("gateway_port"),
        )
    except KeyError as exc:
        raise NodeRegistryError(
            f"node entry missing required field {exc}: {raw!r}"
        ) from exc
    except (ValueError, TypeError) as exc:
        raise NodeRegistryError(f"invalid node entry {raw!r}: {exc}") from exc


def load_node_registry(path: Path | str | None = None) -> NodeRegistry:
    """Load the node registry file. A missing file means "no remote nodes".

    Validation failures raise :class:`NodeRegistryError` (fail-closed at
    load, never a half-parsed registry).
    """
    resolved = Path(path).expanduser() if path else nodes_file_path()
    if not resolved.is_file():
        return NodeRegistry(())
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise NodeRegistryError(f"node registry {resolved} unreadable: {exc}") from exc
    data = _parse_registry_text(text, str(resolved))
    entries = data.get("nodes", [])
    if not isinstance(entries, list):
        raise NodeRegistryError(f"node registry {resolved}: 'nodes' must be a list")
    return NodeRegistry(tuple(_node_from_dict(e) for e in entries))


def _is_tailscale_ip_lazy(address: str) -> bool:
    """``is_tailscale_ip`` without importing prismatic at module scope."""
    from prismatic.mesh.tailscale import is_tailscale_ip

    return is_tailscale_ip(address)


def resolve_address(
    node: DeployNode,
    mesh_client: Any | None = None,
) -> str:
    """Resolve a node's tailnet address to a dialable address (fail-closed).

    * ``"local"`` -> ``"localhost"`` (no mesh needed).
    * A Tailscale IP passes through (``is_tailscale_ip``).
    * Anything else (MagicDNS name) must match a mesh node's
      ``dns_name``/``name``/``hostname``/IP via ``list_nodes_sync()`` --
      no match raises :class:`UnresolvableNodeError`.

    No DNS guessing: an address that is neither a Tailscale IP nor in the
    tailnet mesh is refused, never dialed.
    """
    if node.is_local:
        return "localhost"
    address = node.address.strip().rstrip(".")
    try:
        if _is_tailscale_ip_lazy(address):
            return address
    except Exception:  # pragma: no cover - defensive; helper is stdlib+prismatic
        pass
    client = mesh_client if mesh_client is not None else _default_mesh_client()
    try:
        mesh_nodes = client.list_nodes_sync()
    except Exception as exc:
        raise UnresolvableNodeError(
            f"node {node.name!r}: cannot list tailnet mesh to resolve "
            f"{address!r}: {exc}"
        ) from exc
    needle = address.lower()
    for peer in mesh_nodes:
        candidates = {
            (peer.dns_name or "").lower().rstrip("."),
            (peer.dns_name or "").split(".")[0].lower(),
            (peer.name or "").lower(),
            (peer.hostname or "").lower(),
        }
        ips = {str(ip) for ip in (peer.tailscale_ips or [])}
        if needle in candidates or address in ips:
            resolved = (peer.tailscale_ips or [peer.dns_name])[0]
            logger.info("node %s: resolved %r -> %s", node.name, address, resolved)
            return resolved
    raise UnresolvableNodeError(
        f"node {node.name!r}: address {node.address!r} is not a Tailscale IP "
        "and matches no node in the tailnet mesh (checked dns_name, name, "
        "hostname, IPs) -- refusing to dial it"
    )


def _default_mesh_client() -> Any:
    from prismatic.mesh.tailscale import get_tailscale_mesh_client

    return get_tailscale_mesh_client()


def ping_node(
    node: DeployNode,
    mesh_client: Any | None = None,
    count: int = 1,
) -> dict[str, Any]:
    """Mesh reachability probe for one node (``tailscale ping``).

    Returns the mesh client's ping dict; ``success=False`` means the node is
    unreachable over the tailnet. Never raises for reachability failures.
    """
    if node.is_local:
        return {"target": node.name, "success": True, "via": "local"}
    try:
        address = resolve_address(node, mesh_client)
    except UnresolvableNodeError as exc:
        return {"target": node.address, "success": False, "error": str(exc)}
    client = mesh_client if mesh_client is not None else _default_mesh_client()
    import asyncio

    try:
        result = asyncio.run(client.ping(address, count=count))
    except Exception as exc:
        return {"target": address, "success": False, "error": str(exc)}
    return result


def append_node(path: Path | str | None, node: DeployNode) -> Path:
    """Append ``node`` to the registry file (enroll-node flow only).

    Creates the file when missing. Rewrites preserving existing entries;
    refuses when a node with the same name already exists.
    """
    resolved = Path(path).expanduser() if path else nodes_file_path()
    registry = load_node_registry(resolved)
    if node.name in registry:
        raise NodeRegistryError(
            f"node {node.name!r} is already registered in {resolved}"
        )
    entries = [n.to_dict() for n in registry] + [node.to_dict()]
    data = {"nodes": entries}
    text = _dump_registry(data, resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        pass
    logger.info("registered node %s in %s", node.name, resolved)
    return resolved


def _dump_registry(data: dict[str, Any], resolved: Path) -> str:
    """Serialize the registry: YAML when PyYAML is present, else JSON.

    JSON is valid YAML, so a JSON-written ``nodes.yaml`` still parses.
    """
    try:
        import yaml  # type: ignore[import-not-found]

        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except ImportError:
        return json.dumps(data, indent=2) + "\n"
