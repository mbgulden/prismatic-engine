"""
prismatic.security — Prismatic Engine security subsystem.

Provides secret/PII detection, credential rotation, cryptographic
signing, immutable audit logging, and quarantine management.

Submodules:
    - egress_scanner   — Regex/entropy scanner that blocks secrets on egress
    - cryptography     — Ed25519 key generation and payload signing
    - credential_rotator — Automated token refresh for external providers
    - audit_ledger     — Blockchain-style immutable audit trail
    - security_policy  — Centralized security policy configuration
"""

from __future__ import annotations

from prismatic.security.egress_scanner import (
    EgressScanner,
    ScanResult,
    SecurityVulnerabilityAlert,
    get_scanner,
    scan_egress,
)

__all__ = [
    "EgressScanner",
    "ScanResult",
    "SecurityVulnerabilityAlert",
    "get_scanner",
    "scan_egress",
]
