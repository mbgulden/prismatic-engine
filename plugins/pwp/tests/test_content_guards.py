from __future__ import annotations

from pathlib import Path
import sys

PWP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PWP_DIR.parent))

from pwp.content_guards import (  # noqa: E402
    LockedFieldViolation,
    assert_safe_emdash_edits,
    guard_emdash_edits,
)


def test_locked_identity_and_compliance_fields_are_removed_from_patch() -> None:
    result = guard_emdash_edits(
        {
            "title": "Secure pickup for Honolulu offices",
            "legalName": "Different Legal Entity LLC",
            "schemaOrgType": "MedicalBusiness",
            "complianceClaims": ["HIPAA certified"],
        }
    )

    assert result.safe_edits == {"title": "Secure pickup for Honolulu offices"}
    assert {violation.path for violation in result.violations} == {
        "legalName",
        "schemaOrgType",
        "complianceClaims",
    }


def test_system_routing_fields_are_blocked_at_any_depth() -> None:
    result = guard_emdash_edits(
        {
            "hero": {
                "title": "Keep this editable",
                "route": "/changed-by-editor/",
                "component": "UnsafeComponent.astro",
            },
            "slug": "editor-owned-slug",
        }
    )

    assert result.safe_edits == {"hero": {"title": "Keep this editable"}}
    assert [violation.path for violation in result.violations] == [
        "hero.route",
        "hero.component",
        "slug",
    ]
    assert all("routing/system field" in violation.reason for violation in result.violations)


def test_unsupported_certification_claims_are_rejected_but_allowlisted_claims_pass() -> None:
    allowed = ["R2v3 certified"]

    result = guard_emdash_edits(
        {
            "trustPanel": {
                "approved": "Sentinel ITAD is R2v3 certified for downstream processing.",
                "unsupported": "Our pickup workflow is HIPAA certified.",
            }
        },
        allowed_compliance_claims=allowed,
    )

    assert result.safe_edits == {
        "trustPanel": {"approved": "Sentinel ITAD is R2v3 certified for downstream processing."}
    }
    assert len(result.violations) == 1
    assert result.violations[0].path == "trustPanel.unsupported"
    assert "unsupported certification/compliance claim" in result.violations[0].reason


def test_edit_map_locked_fields_and_noneditable_fields_are_honored() -> None:
    edit_map = {
        "lockedFields": ["schemaOrgType"],
        "blocks": [
            {
                "blockId": "hero",
                "fields": {
                    "title": {"type": "string", "editable": True},
                    "eyebrow": {"type": "string", "editable": False},
                },
            }
        ],
    }

    result = guard_emdash_edits(
        {"title": "Editable", "eyebrow": "Do not edit", "schemaOrgType": "Organization"},
        edit_map=edit_map,
    )

    assert result.safe_edits == {"title": "Editable"}
    assert {violation.path for violation in result.violations} == {"eyebrow", "schemaOrgType"}


def test_assert_safe_emdash_edits_raises_for_atomic_api_handlers() -> None:
    try:
        assert_safe_emdash_edits({"canonicalUrl": "https://example.com/changed"})
    except LockedFieldViolation as exc:
        assert len(exc.violations) == 1
        assert exc.violations[0].path == "canonicalUrl"
    else:  # pragma: no cover - assertion failure path
        raise AssertionError("expected LockedFieldViolation")
