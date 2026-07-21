"""Prismatic Engine Type-Specific Verifier Registry Subsystem.

Provides typed verifier plugins and evidence schemas for:
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

from prismatic.verifiers.evidence import build_verifier_result, write_durable_log
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
    enforce_strict_provenance,
)
from prismatic.verifiers.registry import DEFAULT_VERIFIER_REGISTRY, VerifierRegistry
from prismatic.verifiers.schemas import (
    CANONICAL_VERIFIER_TYPES,
    TYPE_SPECIFIC_REQUIRED_FIELDS,
    UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK,
    validate_locator_safety,
    validate_verifier_result,
)

__all__ = [
    "VerifierRegistry",
    "DEFAULT_VERIFIER_REGISTRY",
    "VerifierPlugin",
    "CodePackageVerifier",
    "WebsiteAppBrowserVerifier",
    "ImageDesignVerifier",
    "SpriteAtlasGameVerifier",
    "VideoVerifier",
    "AudioVerifier",
    "DocumentDataVerifier",
    "MixedBundleVerifier",
    "enforce_strict_provenance",
    "build_verifier_result",
    "write_durable_log",
    "validate_verifier_result",
    "validate_locator_safety",
    "UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK",
    "CANONICAL_VERIFIER_TYPES",
    "TYPE_SPECIFIC_REQUIRED_FIELDS",
]
