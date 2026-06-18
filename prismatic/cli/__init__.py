"""
Prismatic Engine — CLI package marker
=====================================

The ``prismatic.cli`` package holds the engine's command-line interface
modules. Each subcommand is a separate module that exports a
``run(args) -> int`` function. The dispatcher (or a future top-level
CLI orchestrator) imports these and dispatches to them.

The first member is ``doctor``. As more subcommands stabilize, this
package grows. The package MUST NOT import from any agent harness
(Hermes/OpenClaw/AGY/Codex/etc.) — see prismatic-engine-operations
§15 (Engine vs Harness separation).
"""
