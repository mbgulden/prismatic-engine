"""
HardwareProfile — typed hardware execution profile definitions.

Provides a ``HardwareProfileRegistry`` that loads profiles from the
canonical YAML file (``prismatic/config/hardware_profiles.yaml``) and
exposes lookup / validation methods used by the ``PluginLoader`` to
verify ``hardware_profile`` and ``execution_profile`` fields on plugin
manifests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger("prismatic.hardware")


# ── Exceptions ──────────────────────────────────────────────────────────


class HardwareProfileError(Exception):
    """Raised when a hardware profile is invalid, unknown, or missing."""


# ── Dataclass ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HardwareProfile:
    """A named hardware execution profile for routing agent tasks.

    Attributes
    ----------
    name:
        Unique identifier (e.g. ``"llm-inference-large"``).
    description:
        Human-readable purpose.
    vram_gb_min:
        Minimum GPU VRAM in GB.  ``0`` means CPU-only.
    cpu_cores:
        Minimum CPU core count.
    memory_gb_min:
        Minimum system RAM in GB.
    gpu_required:
        Whether a GPU is mandatory for this profile.
    gpu_type:
        Optional hint (``"nvidia"``, ``"amd"``, ``"apple-silicon"``, etc.).
    aliases:
        Alternative names that should resolve to this profile (e.g. for
        backward-compatible manifest references).
    """

    name: str
    description: str
    vram_gb_min: int = 0
    cpu_cores: int = 2
    memory_gb_min: int = 4
    gpu_required: bool = False
    gpu_type: Optional[str] = None
    aliases: List[str] = field(default_factory=list)


# ── Registry ────────────────────────────────────────────────────────────


_DEFAULT_PROFILES_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "hardware_profiles.yaml"
)


class HardwareProfileRegistry:
    """Loads, caches, and validates hardware execution profiles.

    Parameters
    ----------
    yaml_path:
        Path to the YAML profiles file.  Defaults to the canonical
        location under ``prismatic/config/hardware_profiles.yaml``.
    """

    def __init__(
        self, yaml_path: Optional[str | Path] = None
    ) -> None:
        self._yaml_path = Path(yaml_path or _DEFAULT_PROFILES_PATH)
        self._profiles: Dict[str, HardwareProfile] = {}
        self._alias_map: Dict[str, str] = {}

    # ── public API ──────────────────────────────────────────────────────

    @property
    def profile_names(self) -> List[str]:
        """Return all canonical profile names."""
        return sorted(self._profiles.keys())

    def load(self) -> None:
        """Load or reload profiles from the YAML file."""
        if not self._yaml_path.exists():
            raise HardwareProfileError(
                f"Hardware profiles file not found: {self._yaml_path}"
            )

        with open(self._yaml_path, "r") as fh:
            data = yaml.safe_load(fh)

        raw_profiles = data.get("profiles", [])
        if not raw_profiles:
            raise HardwareProfileError(
                f"No 'profiles' key found in {self._yaml_path}"
            )

        self._profiles = {}
        self._alias_map = {}
        for raw in raw_profiles:
            profile = HardwareProfile(
                name=raw["name"],
                description=raw.get("description", ""),
                vram_gb_min=raw.get("vram_gb_min", 0),
                cpu_cores=raw.get("cpu_cores", 2),
                memory_gb_min=raw.get("memory_gb_min", 4),
                gpu_required=raw.get("gpu_required", False),
                gpu_type=raw.get("gpu_type"),
                aliases=raw.get("aliases", []),
            )
            self._profiles[profile.name] = profile
            for alias in profile.aliases:
                self._alias_map[alias] = profile.name

        logger.info(
            "Loaded %d hardware profiles from %s",
            len(self._profiles),
            self._yaml_path,
        )

    def get(self, name: str) -> HardwareProfile:
        """Look up a profile by canonical name or alias.

        Raises
        ------
        HardwareProfileError
            If *name* is not found among profiles or aliases.
        """
        if name in self._profiles:
            return self._profiles[name]
        canonical = self._alias_map.get(name)
        if canonical:
            return self._profiles[canonical]
        raise HardwareProfileError(
            f"Unknown hardware profile '{name}'. "
            f"Known profiles: {', '.join(self._profiles)}"
        )

    def is_valid(self, name: str) -> bool:
        """Return ``True`` if *name* is a known profile or alias."""
        try:
            self.get(name)
            return True
        except HardwareProfileError:
            return False
