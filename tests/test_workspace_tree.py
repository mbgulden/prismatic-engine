"""Unit and integration tests for Workstream A: Curated Workspace Plugin (WA-10).
"""


import pytest

from prismatic.workspace.acceptance import AcceptanceProtocol
from prismatic.workspace.categorize import WorkspaceCategorizer
from prismatic.workspace.static.share import WorkspaceShareManager
from prismatic.workspace.tree import WorkspaceTreeWalker


@pytest.fixture
def tmp_docs(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    # 1. Architecture doc
    arch_file = docs_dir / "architecture.md"
    arch_file.write_text(
        "---\ntitle: Core Architecture\ntype: architecture\nstatus: accepted\n---\n# Core Architecture\n\nSystem overview.",
        encoding="utf-8",
    )

    # 2. Decision doc
    dec_file = docs_dir / "okf-review-factory-v1.md"
    dec_file.write_text(
        "---\ntitle: Review Factory OKF\ntype: okf\nstatus: accepted\nlinear_issue: GRO-4188\n---\n# Review Factory OKF\n\nSpec content.",
        encoding="utf-8",
    )

    # 3. Failing doc
    fail_file = docs_dir / "bad-doc.md"
    fail_file.write_text(
        "---\ntitle: Bad Doc\nstatus: invalid_status_name\n---\n[Broken Link](nonexistent.md)",
        encoding="utf-8",
    )

    return docs_dir


class TestWorkspaceCategorizer:
    def test_categorize_by_path(self):
        assert WorkspaceCategorizer.categorize("docs/architecture/overview.md") == "architecture"
        assert WorkspaceCategorizer.categorize("docs/decisions/adr-0001.md") == "decisions"
        assert WorkspaceCategorizer.categorize("docs/okf-review-factory-v1.md") == "okfs"
        assert WorkspaceCategorizer.categorize("docs/runbooks/deploy.md") == "runbooks"
        assert WorkspaceCategorizer.categorize("docs/prompts/reviewer.md") == "prompts"
        assert WorkspaceCategorizer.categorize("docs/random.md") == "other"

    def test_categorize_by_frontmatter_override(self):
        assert WorkspaceCategorizer.categorize("docs/random.md", {"type": "architecture"}) == "architecture"


class TestAcceptanceProtocol:
    def test_valid_doc_passes(self, tmp_docs):
        acc = AcceptanceProtocol(docs_root=tmp_docs)
        res = acc.validate(tmp_docs / "architecture.md")
        assert res["passed"] is True
        assert res["status"] == "passed"
        assert len(res["failed_reasons"]) == 0

    def test_failing_doc(self, tmp_docs):
        acc = AcceptanceProtocol(docs_root=tmp_docs)
        res = acc.validate(tmp_docs / "bad-doc.md")
        assert res["passed"] is False
        assert res["status"] == "failed"
        assert any("Invalid status" in r for r in res["failed_reasons"])
        assert any("Broken relative link" in r for r in res["failed_reasons"])

    def test_skip_reasons_override(self, tmp_path):
        acc = AcceptanceProtocol(docs_root=tmp_path)
        doc = tmp_path / "skip.md"
        doc.write_text(
            "---\ntitle: Skip Test\nstatus: invalid\nacceptance:\n  skip_reasons:\n    - invalid-status\n    - skip-link-check\n---\n# Skip Test\n[Broken](no.md)",
            encoding="utf-8",
        )
        res = acc.validate(doc)
        assert res["passed"] is True


class TestWorkspaceTreeWalker:
    def test_walk_collects_manifest_entries(self, tmp_docs):
        walker = WorkspaceTreeWalker(docs_root=tmp_docs)
        entries = walker.walk()
        assert len(entries) == 3

        categories = {e.category for e in entries}
        assert "architecture" in categories
        assert "okfs" in categories

        # Linear issue captured
        okf_entry = next(e for e in entries if e.id == "okf-review-factory-v1")
        assert okf_entry.linear_issue == "GRO-4188"


class TestWorkspaceShareManager:
    def test_generate_and_validate_share_token(self):
        mgr = WorkspaceShareManager(secret="test-secret-key")
        token_data = mgr.generate_token(doc_id="architecture", ttl_seconds=3600)
        token = token_data["token"]

        valid, doc_id, err = mgr.validate_token(token)
        assert valid is True
        assert doc_id == "architecture"
        assert err is None

    def test_expired_token(self):
        mgr = WorkspaceShareManager(secret="test-secret-key")
        token_data = mgr.generate_token(doc_id="architecture", ttl_seconds=-10)
        token = token_data["token"]

        valid, doc_id, err = mgr.validate_token(token)
        assert valid is False
        assert "expired" in err.lower()

    def test_revoked_token(self):
        mgr = WorkspaceShareManager(secret="test-secret-key")
        token_data = mgr.generate_token(doc_id="architecture", ttl_seconds=3600)
        token = token_data["token"]

        mgr.revoke_token(token)
        valid, doc_id, err = mgr.validate_token(token)
        assert valid is False
        assert "revoked" in err.lower()
