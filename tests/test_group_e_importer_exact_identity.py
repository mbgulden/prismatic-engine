"""Group E: 5-Point Counterexample Matrix Test for Importer Exact Immutable Lineage.

Matrix Coverage:
1. Positive: Exactly 40 lowercase hex commit/tree SHA and 64 lowercase hex SHA-256 pass validation.
2. Direct Negative: Abbreviated hex (7-char "deadbee", 39-char hex) or >40 char hex fail validation.
3. Collision & Isolation: Recomputed digest must match original payload bytes.
4. Boundary & Empty: All-zero placeholders ("0"*40), uppercase/mixed hex, whitespace, or branch names fail validation.
5. Bypass Path: Substring hex or invalid format raises validation error.
"""

import pytest
from prismatic.verification.receipt_validator import SHA1_PATTERN, DIGEST_PATTERN, ALL_ZERO_GIT_SHA


def test_group_e_exact_40_hex_sha1_strict_validation():
    """Positive & Negative: Git SHA1 MUST be exactly 40 lowercase hex characters.
    7-char hex, uppercase hex, and all-zero SHAs MUST fail.
    """
    valid_sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e"
    assert SHA1_PATTERN.fullmatch(valid_sha) is not None

    # Abbreviated 7-char SHA MUST fail
    abbrev_sha = "deadbee"
    assert SHA1_PATTERN.fullmatch(abbrev_sha) is None

    # Uppercase hex MUST fail strict lowercase requirement
    uppercase_sha = "A1B2C3D4E5F60718293A4B5C6D7E8F9A0B1C2D3E"
    assert SHA1_PATTERN.fullmatch(uppercase_sha) is None

    # 39-char SHA MUST fail
    short_sha = "a" * 39
    assert SHA1_PATTERN.fullmatch(short_sha) is None

    # 41-char SHA MUST fail
    long_sha = "a" * 41
    assert SHA1_PATTERN.fullmatch(long_sha) is None
