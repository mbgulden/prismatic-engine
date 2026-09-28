from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

# Anchor for bundled read-only assets (templates, schemas). Resolves against the
# real package, so assets travel with the installed package — wheel/sdist, zip,
# or plain directory — instead of assuming a checked-out repo layout.
_PACKAGE_ROOT = files(__package__)


def bundled_resource(*parts: str):
    """Traversable for a bundled asset path (read via .open()/.read_text()/.is_file())."""
    resource = _PACKAGE_ROOT
    for part in parts:
        resource = resource.joinpath(part)
    return resource


def bundled_json(*parts: str) -> Any:
    with bundled_resource(*parts).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bundled_text(*parts: str) -> str:
    return bundled_resource(*parts).read_text(encoding="utf-8")


def bundled_relpath(*parts: str) -> str:
    """Package-relative display path for error messages (no repo paths leak)."""
    return "/".join(parts)
