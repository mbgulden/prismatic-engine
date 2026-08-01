"""Unit tests for prismatic.workspace.acceptance AcceptanceProtocol & Cache TTL (WA-5 & WA-9).
"""

from prismatic.workspace.acceptance import AcceptanceProtocol
from prismatic.workspace.routes import _CACHE_TTL


def test_acceptance_protocol_validation(tmp_path):
    acc = AcceptanceProtocol(docs_root=tmp_path)

    doc = tmp_path / "valid.md"
    doc.write_text("---\ntitle: Valid Doc\nstatus: accepted\n---\n# Valid Doc\nContent", encoding="utf-8")

    res = acc.validate(doc)
    assert res["passed"] is True
    assert res["status"] == "passed"


def test_acceptance_cache_ttl_under_60s():
    assert _CACHE_TTL <= 60.0, f"Cache TTL {_CACHE_TTL} exceeds 60s limit"
