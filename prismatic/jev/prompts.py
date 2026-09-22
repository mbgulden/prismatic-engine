"""Prompt registry: prompts as versioned files + content hash.

Prompts are versioned artifacts, not inline strings. Each registered prompt
is a text file on disk; ``registry.json`` maps a stable ``prompt_id`` to its
version and file. Traces carry ``prompt_id@version#sha256`` so any decision
is reproducible and A/B comparisons are fair.

Registry file format (``prompts/registry.json``)::

    {
      "_comment": "prompt_id -> {version, file}",
      "prompts": {
        "triage_v1": {"version": "1", "file": "triage_v1.txt"}
      }
    }

Callers may also supply their own directories (``PromptRegistry(dirs=[...])``)
— the primitive ships an empty registry because it has no call sites yet.
``resolve()`` fails closed on unknown ids: a question referencing a prompt
that does not exist is a configuration bug, never silently rendered as an
empty prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from .errors import DecisionError


@dataclass(frozen=True)
class PromptRef:
    """A resolved prompt: id, version, content hash, and text."""

    id: str
    version: str
    sha256: str
    text: str

    def label(self) -> str:
        """Trace label: ``prompt_id@version#hash``."""
        return f"{self.id}@{self.version}#{self.sha256}"


def _package_prompts_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "prompts")


class PromptRegistry:
    """Resolves prompt ids to versioned, content-hashed prompt text."""

    def __init__(self, dirs: list[str] | None = None):
        self._dirs = list(dirs) if dirs else [_package_prompts_dir()]

    def resolve(self, prompt_id: str) -> PromptRef:
        pid = (prompt_id or "").strip()
        if not pid:
            raise DecisionError("empty prompt_id")
        for directory in self._dirs:
            ref = self._resolve_in(pid, directory)
            if ref is not None:
                return ref
        raise DecisionError(f"unknown prompt_id: {pid!r}")

    def _resolve_in(self, pid: str, directory: str) -> PromptRef | None:
        registry_path = os.path.join(directory, "registry.json")
        try:
            with open(registry_path, encoding="utf-8") as fh:
                registry = json.load(fh)
        except (OSError, ValueError):
            return None
        prompts = registry.get("prompts") if isinstance(registry, dict) else None
        if not isinstance(prompts, dict) or pid not in prompts:
            return None
        entry = prompts[pid]
        if not isinstance(entry, dict):
            return None
        version = str(entry.get("version", ""))
        filename = entry.get("file")
        if not version or not filename:
            return None
        path = os.path.join(directory, str(filename))
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        text = raw.decode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return PromptRef(id=pid, version=version, sha256=digest, text=text)
