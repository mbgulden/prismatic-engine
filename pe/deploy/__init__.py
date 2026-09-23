"""pe.deploy package exports.

PEP 562 lazy exports: importing this package -- or any of its stdlib-only
submodules (``pe.deploy.config``, ``pe.deploy.process_manager``,
``pe.deploy.install``, ...) -- must NOT pull the heavy ``prismatic.deploy``
imports. Those resolve on first attribute access, so the existing
``from pe.deploy import DeployManifestStore`` behavior is preserved while
the installer entry point (``python -m pe.deploy.install``) keeps working
on a bare system python with no Prismatic dependencies installed.

(WS3: before this change, ``import pe.deploy.config`` failed on a bare
python because the package ``__init__`` imported ``prismatic.deploy``,
which needs third-party deps -- contradicting the stdlib-only docstrings
of the WS1/WS2 modules.)
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DeployManifestStore",  # noqa: F822 -- provided by __getattr__ (PEP 562)
    "DeployReceiverPipeline",  # noqa: F822 -- provided by __getattr__ (PEP 562)
]

_LAZY_EXPORTS = {
    "DeployManifestStore": ("prismatic.deploy.manifest", "DeployManifestStore"),
    "DeployReceiverPipeline": ("prismatic.deploy.receiver", "DeployReceiverPipeline"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    return getattr(module, target[1])


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
