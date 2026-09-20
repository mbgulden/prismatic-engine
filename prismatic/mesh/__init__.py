"""Prismatic Distributed Tailscale Mesh & Universal Node Registry."""

from prismatic.mesh.tailscale import (
    TailscaleMeshClient,
    TailscaleNode,
    TailscalePeerIdentity,
    TailscaleAuthMiddleware,
    get_tailscale_mesh_client,
)

__all__ = [
    "TailscaleMeshClient",
    "TailscaleNode",
    "TailscalePeerIdentity",
    "TailscaleAuthMiddleware",
    "get_tailscale_mesh_client",
]
