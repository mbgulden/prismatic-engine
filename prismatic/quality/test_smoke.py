"""Tests for prismatic.quality.smoke (Phase 2 / Gap 5).

Covers:
  - Claim extraction from various agent output formats
  - Filesystem verification (exists, non-empty, has content)
  - Path traversal detection
  - Edge cases (empty output, no claims, lying agents)
  - Integration with workdir resolution
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from prismatic.quality.smoke import (
    SmokeFinding,
    SmokeTestResult,
    extract_claimed_paths,
    is_path_traversal,
    file_exists,
    file_has_substantive_content,
    smoke_test,
)


# ─────────────────────────────────────────────────────────────────────
# Claim extraction tests
# ─────────────────────────────────────────────────────────────────────


class TestExtractClaimedPaths:
    def test_creates_claim(self):
        output = "I created prismatic/quality/smoke.py with the smoke test logic."
        claimed = extract_claimed_paths(output)
        assert "prismatic/quality/smoke.py" in claimed

    def test_wrote_claim(self):
        output = "I wrote tests/test_smoke.py"
        claimed = extract_claimed_paths(output)
        assert "tests/test_smoke.py" in claimed

    def test_modified_claim(self):
        output = "Modified prismatic/quality/gates.py to add new function"
        claimed = extract_claimed_paths(output)
        assert "prismatic/quality/gates.py" in claimed

    def test_backtick_path(self):
        output = "Updated `prismatic/quality/failure.py` with new logic"
        claimed = extract_claimed_paths(output)
        assert "prismatic/quality/failure.py" in claimed

    def test_file_colon_format(self):
        output = "file: prismatic/quality/gates.py"
        claimed = extract_claimed_paths(output)
        assert "prismatic/quality/gates.py" in claimed

    def test_absolute_path(self):
        output = "I wrote /tmp/work/foo.py"
        claimed = extract_claimed_paths(output)
        assert "/tmp/work/foo.py" in claimed

    def test_no_claims_returns_empty(self):
        output = "I thought about the problem but didn't write anything."
        claimed = extract_claimed_paths(output)
        assert claimed == []

    def test_empty_output(self):
        assert extract_claimed_paths("") == []

    def test_urls_filtered_out(self):
        output = "See https://example.com/docs.html for details"
        claimed = extract_claimed_paths(output)
        assert all(not p.startswith("http") for p in claimed)

    def test_version_strings_filtered(self):
        output = "Updated to version 3.14"
        claimed = extract_claimed_paths(output)
        assert all(not p.startswith(("1.", "2.", "3.", "4.", "5.")) for p in claimed)

    def test_deduplicates(self):
        output = "I created foo.py. Then I edited foo.py. Finally, foo.py is done."
        claimed = extract_claimed_paths(output)
        # foo.py appears multiple times — should be deduplicated
        assert claimed.count("foo.py") <= 1

    def test_git_sha_filtered_out(self):
        """Per PR #36 review: 40-hex-char SHA in backticks should NOT be a path."""
        output = "Committed at `6c6ee9526abc123def456789012345678901234`"
        claimed = extract_claimed_paths(output)
        assert "6c6ee9526abc123def456789012345678901234" not in claimed

    def test_short_hex_not_filtered(self):
        """Short hex strings (not SHAs) should still be considered as paths"""
        output = "See `abc123` for details"
        claimed = extract_claimed_paths(output)
        # "abc123" is 6 chars — doesn't match SHA pattern (needs 20+), so it's still a path
        assert "abc123" in claimed  # short hex IS treated as a path


class TestBinaryDetectionBoundary:
    """Per PR #36 review: off-by-one at exactly 1% null bytes."""

    def test_exactly_1_percent_nulls_is_binary(self, tmp_path):
        # 100 bytes, 1 null = exactly 1% — should be binary
        f = tmp_path / "boundary.dat"
        f.write_bytes(b"\x00" + b"x" * 99)
        has_content, detail = file_has_substantive_content(str(f))
        assert has_content is True
        assert "binary" in detail.lower()

    def test_just_under_1_percent_is_text(self, tmp_path):
        # 200 bytes, 1 null = 0.5% — should NOT be binary
        f = tmp_path / "mostly_text.dat"
        content = b"\x00" + b"x" * 199
        f.write_bytes(content)
        has_content, detail = file_has_substantive_content(str(f))
        # Will be classified as text since null ratio < 1%
        # (Note: content is "x" repeated, so it'll be flagged as having substantive content)
        assert "binary" not in detail.lower()


# ─────────────────────────────────────────────────────────────────────
# Path traversal tests
# ─────────────────────────────────────────────────────────────────────


class TestPathTraversal:
    def test_clean_path_passes(self):
        assert is_path_traversal("prismatic/quality/gates.py") is False

    def test_traversal_with_dotdot_slash(self):
        assert is_path_traversal("prismatic/quality/../../etc/passwd") is True

    def test_traversal_with_dotdot_backslash(self):
        assert is_path_traversal("prismatic\\..\\foo") is True

    def test_relative_dotdot_passes(self):
        # 'foo..bar' is not a traversal — should pass
        assert is_path_traversal("foo..bar") is False


# ─────────────────────────────────────────────────────────────────────
# Filesystem verification tests
# ─────────────────────────────────────────────────────────────────────


class TestFileExists:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello")
        assert file_exists(str(f)) is True

    def test_missing_file(self, tmp_path):
        assert file_exists(str(tmp_path / "missing.py")) is False

    def test_relative_path_against_workdir(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello")
        assert file_exists("test.py", workdir=str(tmp_path)) is True

    def test_directory_not_file(self, tmp_path):
        # Directories should not count as files
        d = tmp_path / "subdir"
        d.mkdir()
        assert file_exists(str(d)) is False


class TestFileHasSubstantiveContent:
    def test_substantive_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    return 42\n")
        has_content, detail = file_has_substantive_content(str(f))
        assert has_content is True
        assert "chars" in detail

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        has_content, detail = file_has_substantive_content(str(f))
        assert has_content is False
        assert "empty" in detail.lower()

    def test_whitespace_only_file(self, tmp_path):
        f = tmp_path / "ws.py"
        f.write_text("   \n\n\t\t\n   \n")
        has_content, detail = file_has_substantive_content(str(f))
        assert has_content is False
        assert "stripping" in detail.lower() or "chars" in detail.lower()

    def test_comments_only_file(self, tmp_path):
        f = tmp_path / "comments.py"
        f.write_text("# just a comment\n# nothing else\n")
        has_content, detail = file_has_substantive_content(str(f))
        assert has_content is False

    def test_binary_file(self, tmp_path):
        f = tmp_path / "binary.dat"
        f.write_bytes(b"\x00\x01\x02\x03" * 100)
        has_content, detail = file_has_substantive_content(str(f))
        assert has_content is True
        assert "binary" in detail.lower()

    def test_missing_file(self, tmp_path):
        has_content, detail = file_has_substantive_content(str(tmp_path / "missing.py"))
        assert has_content is False
        assert "does not exist" in detail


# ─────────────────────────────────────────────────────────────────────
# End-to-end smoke_test tests
# ─────────────────────────────────────────────────────────────────────


class TestSmokeTest:
    def test_lie_detection(self, tmp_path, monkeypatch):
        """Agent claims file X exists, but X doesn't exist — must FAIL."""
        monkeypatch.chdir(tmp_path)
        output = "I created important_file.py with the fix."
        result = smoke_test(output, workdir=str(tmp_path))

        assert result.passed is False
        assert any(f.status == "missing" for f in result.findings)
        assert "important_file.py" in result.claimed_paths

    def test_empty_file_caught(self, tmp_path, monkeypatch):
        """Agent claims file but it's empty — must FAIL."""
        monkeypatch.chdir(tmp_path)
        # Create the file but make it empty
        (tmp_path / "empty_file.py").write_text("")
        output = "I wrote empty_file.py"
        result = smoke_test(output, workdir=str(tmp_path))

        assert result.passed is False
        empty_findings = [f for f in result.findings if f.status == "empty"]
        assert len(empty_findings) >= 1

    def test_legitimate_output_passes(self, tmp_path, monkeypatch):
        """Agent claims file and it has substantive content — must PASS."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "real_file.py"
        f.write_text("def hello():\n    return 42\n")
        output = "I created real_file.py with the hello function."
        result = smoke_test(output, workdir=str(tmp_path))

        assert result.passed is True
        assert any(f.status == "claimed_ok" for f in result.findings)

    def test_no_claims_is_vacuous_pass(self, tmp_path):
        """Agent that says nothing about files — vacuous PASS but flagged."""
        output = "I thought about the problem carefully."
        result = smoke_test(output, workdir=str(tmp_path))

        assert result.passed is True
        assert "vacuous" in result.reason.lower()

    def test_empty_output(self):
        result = smoke_test("", workdir=".")
        assert result.passed is True  # Vacuous pass

    def test_path_traversal_caught(self, tmp_path, monkeypatch):
        """Path traversal in claimed path — must FAIL with security finding."""
        monkeypatch.chdir(tmp_path)
        output = "I created ../../../etc/passwd"
        result = smoke_test(output, workdir=str(tmp_path))

        assert result.passed is False
        traversal = [f for f in result.findings if f.status == "traversal_attempt"]
        assert len(traversal) >= 1

    def test_multiple_claims_mixed_verdict(self, tmp_path, monkeypatch):
        """Some claims pass, some fail — overall FAIL."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "exists.py").write_text("def f():\n    return 1\n")
        output = "I created exists.py and missing.py"
        result = smoke_test(output, workdir=str(tmp_path))

        assert result.passed is False
        statuses = [f.status for f in result.findings]
        assert "claimed_ok" in statuses
        assert "missing" in statuses

    def test_suspiciously_small_file(self, tmp_path, monkeypatch):
        """File < 50 bytes is flagged as suspicious."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "tiny.py").write_text("x=1")  # 4 bytes
        output = "I wrote tiny.py"
        result = smoke_test(output, workdir=str(tmp_path))

        assert result.passed is False
        small_findings = [f for f in result.findings if "small" in f.detail.lower()]
        assert len(small_findings) >= 1


# ─────────────────────────────────────────────────────────────────────
# Output format tests
# ─────────────────────────────────────────────────────────────────────


class TestOutputFormats:
    def test_to_dict(self):
        result = SmokeTestResult(
            passed=True,
            claimed_paths=["foo.py"],
            findings=[SmokeFinding(path="foo.py", status="claimed_ok", detail="100 chars")],
            reason="All good",
        )
        d = result.to_dict()
        assert d["passed"] is True
        assert d["claimed_paths"] == ["foo.py"]
        assert len(d["findings"]) == 1

    def test_to_markdown_pass(self):
        result = SmokeTestResult(
            passed=True,
            claimed_paths=["foo.py"],
            findings=[SmokeFinding(path="foo.py", status="claimed_ok", detail="100 chars")],
            reason="All good",
        )
        md = result.to_markdown()
        assert "✅" in md
        assert "PASS" in md
        assert "foo.py" in md

    def test_to_markdown_fail(self):
        result = SmokeTestResult(
            passed=False,
            claimed_paths=["missing.py"],
            findings=[SmokeFinding(path="missing.py", status="missing", detail="not found")],
            reason="1 missing",
        )
        md = result.to_markdown()
        assert "❌" in md
        assert "FAIL" in md
        assert "missing.py" in md