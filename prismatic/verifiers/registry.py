"""Typed verifier registry and manifest binding engine.

Manages registration, lookup, execution, schema validation, and Universal Result Manifest v2
binding for all 8 verifier plugin types:
- code/package
- website/app/browser
- image/design
- sprite/atlas/game import
- video
- audio
- document/data
- mixed bundles
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from prismatic.verifiers.plugins import (
    AudioVerifier,
    CodePackageVerifier,
    DocumentDataVerifier,
    ImageDesignVerifier,
    MixedBundleVerifier,
    SpriteAtlasGameVerifier,
    VerifierPlugin,
    VideoVerifier,
    WebsiteAppBrowserVerifier,
)
from prismatic.verifiers.schemas import (
    CANONICAL_VERIFIER_TYPES,
    validate_verifier_result,
)


class VerifierRegistry:
    """Typed registry for output verifiers."""

    def __init__(self, register_builtins: bool = True):
        self._verifiers: dict[str, VerifierPlugin] = {}
        self._plugin_ids: set[str] = set()

        if register_builtins:
            self._register_default_plugins()

    def _register_default_plugins(self) -> None:
        default_plugins = [
            CodePackageVerifier(),
            WebsiteAppBrowserVerifier(),
            ImageDesignVerifier(),
            SpriteAtlasGameVerifier(),
            VideoVerifier(),
            AudioVerifier(),
            DocumentDataVerifier(),
            MixedBundleVerifier(),
        ]
        for plugin in default_plugins:
            self.register(plugin)

    def register(self, plugin: VerifierPlugin) -> None:
        """Register a verifier plugin. Fails closed on invalid type or duplicate ID."""
        if not isinstance(plugin, VerifierPlugin):
            raise TypeError("Plugin must inherit from VerifierPlugin")
        if plugin.plugin_id in self._plugin_ids:
            raise ValueError(f"Duplicate plugin ID: '{plugin.plugin_id}'")
        if plugin.verifier_type not in CANONICAL_VERIFIER_TYPES.values():
            raise ValueError(
                f"Invalid canonical verifier type: '{plugin.verifier_type}'"
            )

        self._verifiers[plugin.verifier_type] = plugin
        self._plugin_ids.add(plugin.plugin_id)

    def unregister(self, plugin_id: str) -> None:
        """Unregister a plugin by plugin ID."""
        for vtype, plugin in list(self._verifiers.items()):
            if plugin.plugin_id == plugin_id:
                del self._verifiers[vtype]
                self._plugin_ids.remove(plugin_id)
                return
        raise KeyError(f"Plugin ID '{plugin_id}' not found")

    def get_verifier(self, verifier_type: str) -> VerifierPlugin:
        """Lookup verifier plugin by type or alias."""
        if verifier_type not in CANONICAL_VERIFIER_TYPES:
            raise KeyError(f"Unknown verifier type or alias: '{verifier_type}'")
        canonical_type = CANONICAL_VERIFIER_TYPES[verifier_type]
        if canonical_type not in self._verifiers:
            raise KeyError(
                f"No verifier registered for canonical type '{canonical_type}'"
            )
        return self._verifiers[canonical_type]

    def list_verifiers(self) -> list[str]:
        """List canonical names of all registered verifier types."""
        return sorted(list(self._verifiers.keys()))

    def verify(
        self,
        verifier_type: str,
        candidate: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute verification for a candidate output and return a validated result."""
        verifier = self.get_verifier(verifier_type)
        result = verifier.verify(candidate, context)

        # Enforce strict validation
        ok, errors = validate_verifier_result(result)
        if not ok:
            raise ValueError(
                f"Verifier '{verifier_type}' produced invalid result: {'; '.join(errors)}"
            )

        return result

    def bind_to_universal_manifest(
        self, verifier_result: Mapping[str, Any], manifest: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Bind a verifier result into a Universal Result Manifest v2."""
        ok, errors = validate_verifier_result(verifier_result)
        if not ok:
            raise ValueError(
                f"Cannot bind invalid verifier result: {'; '.join(errors)}"
            )

        out_manifest = copy.deepcopy(dict(manifest))
        verifier_type = verifier_result["verifier_type"]
        evidence = verifier_result["type_specific_evidence"]

        # Map canonical verifier_type to manifest proof key
        proof_key_map = {
            "code": "code",
            "web/app": "web_app",
            "image/design": "image_design",
            "sprite/game asset": "sprite_game_asset",
            "video": "video",
            "audio": "audio",
            "document/data": "document_data",
            "mixed": "mixed",
        }

        manifest_proof_allowed_keys = {
            "code": {"tests_passed", "coverage", "lint_status"},
            "web/app": {"build_status", "lighthouse", "visual_qa"},
            "image/design": {"dimensions", "color_space", "similarity_score"},
            "sprite/game asset": {"frame_count", "spritesheet", "collision_boxes"},
            "video": {"resolution", "duration_seconds", "bitrate_kbps"},
            "audio": {"channels", "sample_rate_hz", "duration_seconds"},
            "document/data": {"format", "valid", "schema_compliant"},
            "mixed": {"submanifests_validated"},
        }

        proof_key = proof_key_map[verifier_type]
        allowed_keys = manifest_proof_allowed_keys[verifier_type]

        # Extract only allowed manifest proof fields for type_specific_proofs block
        filtered_proof = {k: v for k, v in evidence.items() if k in allowed_keys}

        type_proofs = out_manifest.setdefault("type_specific_proofs", {})
        type_proofs[proof_key] = filtered_proof
        out_manifest["type_specific_proofs"] = type_proofs

        # Also store verifier result metadata in manifest
        out_manifest["verifier_result"] = dict(verifier_result)
        out_manifest["marker"] = "UNIVERSAL_RESULT_MANIFEST_V2_OK"

        return out_manifest


# Global default registry instance
DEFAULT_VERIFIER_REGISTRY = VerifierRegistry()
