"""
pwp — Prismatic Web Plugin entry point.

This package is loaded by ``prismatic.core.registry.PluginLoader`` via the
``plugin-manifest.yaml`` at ``plugins/pwp/plugin-manifest.yaml``.  The
public surface is intentionally small:

* ``pwp.plugin.PwpPlugin`` — the engine-facing plugin class
* ``pwp.health.check`` — capability health check (used by install + on_init)
* ``pwp.health.summarize`` — aggregate row counts

The implementation of the actual ingest/synthesize/distill/scaffold pipeline
lives in the ``prismatic-web-plugin`` repository (linked from
``okf/projects/prismatic-web-plugin.md``); this package is the integration
shim that plugs the pipeline into the engine.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]