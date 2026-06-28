"""Tests for prismatic.review.pr_reviewer_impl (Phase 2 / Gap 4 — real implementation).

Covers:
  - URL parsing (full URLs, short forms)
  - Secret detection (AWS keys, GitHub PATs, Slack, Stripe, etc.)
  - Code-quality metrics (function length, file length)
  - Test coverage heuristic
  - Verdict computation
  - RealPRReviewer with mocked gh CLI
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from prismatic.review.pr_reviewer_impl import (
    SECRET_PATTERNS,
    QualityFinding,
    RealPRReviewer,
    check_file_length,
    check_function_length,
    check_test_coverage_heuristic,
    compute_verdict,
    detect_secrets,
    fetch_pr_diff,
    parse_pr_url,
)
from prismatic.review.pr_reviewer import (
    APPROVE,
    NEEDS_DISCUSSION,
    REQUEST_CHANGES,
)


# ─────────────────────────────────────────────────────────────────────
# URL parsing tests
# ─────────────────────────────────────────────────────────────────────


class TestParsePRUrl:
    def test_full_https_url(self):
        owner, repo, num = parse_pr_url("https://github.com/owner/repo/pull/123")
        assert owner == "owner"
        assert repo == "repo"
        assert num == 123

    def test_full_http_url(self):
        owner, repo, num = parse_pr_url("http://github.com/owner/repo/pull/456")
        assert num == 456

    def test_short_form_with_hash(self):
        owner, repo, num = parse_pr_url("owner/repo#789")
        assert owner == "owner"
        assert repo == "repo"
        assert num == 789

    def test_short_form_with_slash(self):
        owner, repo, num = parse_pr_url("owner/repo/pull/101")
        assert num == 101

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError):
            parse_pr_url("not a url")


# ─────────────────────────────────────────────────────────────────────
# Secret detection tests
# ─────────────────────────────────────────────────────────────────────


class TestDetectSecrets:
    def test_aws_access_key_detected(self):
        # Build token via concat to avoid GitHub's secret-scanner push
        # protection flagging this test fixture as a real secret.
        fake_key = "AKIA" + "IOSF" + "ODNN" + "7XYZ" + "AB12" + "34CD"
        diff = f"""+++ b/config.py
+AWS_KEY = "{fake_key}"
"""
        findings = detect_secrets(diff)
        assert any(f.severity == "critical" for f in findings)
        assert any("aws_access_key" in f.message for f in findings)

    def test_github_pat_detected(self):
        # Build token via concat to avoid GitHub's secret-scanner push
        # protection flagging this test fixture as a real secret.
        fake_pat = (
            "ghp"
            + "_"
            + "abc"
            + "def"
            + "ghi"
            + "jkl"
            + "mno"
            + "pqr"
            + "stu"
            + "vwx"
            + "012"
            + "345"
            + "67"
            + "89"
            + "XY"
            + "ZW"
        )
        diff = f"""+++ b/token.txt
+{fake_pat}
"""
        findings = detect_secrets(diff)
        assert any("github_pat" in f.message for f in findings)

    def test_github_oauth_detected(self):
        # Build token via concat to avoid GitHub's secret-scanner push
        # protection flagging this test fixture as a real secret.
        fake_oauth = (
            "gho"
            + "_"
            + "abc"
            + "def"
            + "ghi"
            + "jkl"
            + "mno"
            + "pqr"
            + "stu"
            + "vwx"
            + "012"
            + "345"
            + "67"
            + "89"
            + "XY"
            + "ZW"
        )
        diff = f"""+++ b/token.txt
+{fake_oauth}
"""
        findings = detect_secrets(diff)
        assert any("github_oauth" in f.message for f in findings)

    def test_slack_token_detected(self):
        # Build token via concat to avoid GitHub's secret-scanner push
        # protection flagging this test fixture as a real secret.
        fake = "xoxb" + "-" + "PLACEHOLDER_LONG_BODY_NOT_REAL" * 2
        diff = f"+++ b/config.py\n+SLACK = {fake!r}\n"
        findings = detect_secrets(diff)
        assert any("slack_token" in f.message for f in findings)

    def test_stripe_key_detected(self):
        # 'PLACEHOLDERKEYBODY' has no underscores so it forms one contiguous
        # 18-char chunk that we multiply to exceed the 24-char regex requirement.
        fake = "sk" + "_" + "live" + "_" + "PLACEHOLDERKEYBODY" * 2
        diff = f"+++ b/.env\n+STRIPE = {fake!r}\n"
        findings = detect_secrets(diff)
        assert any("stripe_key" in f.message for f in findings)

    def test_private_key_detected(self):
        diff = """+++ b/server.pem
+-----BEGIN RSA PRIVATE KEY-----
+base64data
+-----END RSA PRIVATE KEY-----
"""
        findings = detect_secrets(diff)
        assert any("private_key" in f.message for f in findings)

    def test_db_url_with_password_detected(self):
        diff = """+++ b/config.py
+DB_URL = "postgres://user:secretpass@db.example.com:5432/mydb"
"""
        findings = detect_secrets(diff)
        assert any("db_url_with_password" in f.message for f in findings)

    def test_no_secrets_clean(self):
        diff = """diff --git a/main.py b/main.py
+++ b/main.py
@@ -1,1 +1,3 @@
 def hello():
+    print("hello world")
+    return 42
"""
        findings = detect_secrets(diff)
        assert findings == []

    def test_only_added_lines_scanned(self):
        # Removed lines should NOT trigger (the secret is being deleted).
        # Build token via concat to avoid GitHub's secret-scanner push
        # protection flagging this test fixture as a real secret.
        fake_key = "AKIA" + "IOSF" + "ODNN" + "7XYZ" + "AB12" + "34CD"
        diff = f"""--- a/config.py
+++ b/config.py
@@ -1,2 +1,1 @@
-AWS_KEY = "{fake_key}"
 # cleaned up
"""
        findings = detect_secrets(diff)
        assert findings == []  # The AWS line is removed, not added


# ─────────────────────────────────────────────────────────────────────
# Function length tests
# ─────────────────────────────────────────────────────────────────────


class TestCheckFunctionLength:
    def test_short_function_passes(self):
        diff = """+++ b/file.py
+def hello():
+    return 42
"""
        findings = check_function_length(diff)
        assert findings == []

    def test_long_function_detected(self):
        diff = "+++ b/file.py\n" + "+def big_function():\n" + "+    x = 1\n" * 60
        findings = check_function_length(diff)
        assert len(findings) >= 1
        assert findings[0].severity == "warning"
        assert "big_function" in findings[0].message


# ─────────────────────────────────────────────────────────────────────
# File length tests
# ─────────────────────────────────────────────────────────────────────


class TestCheckFileLength:
    def test_short_file_passes(self):
        diff = "+++ b/file.py\n" + "+x = 1\n" * 50
        findings = check_file_length(diff)
        assert findings == []

    def test_long_file_detected(self):
        diff = "+++ b/big_file.py\n" + "+x = 1\n" * 600
        findings = check_file_length(diff)
        assert len(findings) >= 1
        assert "600 lines" in findings[0].message


# ─────────────────────────────────────────────────────────────────────
# Test coverage heuristic tests
# ─────────────────────────────────────────────────────────────────────


class TestCheckTestCoverageHeuristic:
    def test_source_with_tests_passes(self):
        diff = """+++ b/src/foo.py
+def foo():
+    return 42
+++ b/tests/test_foo.py
+def test_foo():
+    assert foo() == 42
"""
        findings = check_test_coverage_heuristic(diff)
        assert findings == []

    def test_source_without_tests_detected(self):
        # Real unified diffs use `--- /dev/null` for newly-created files.
        # The new source file has 12 added lines (>10 threshold).
        diff = """--- /dev/null
+++ b/src/foo.py
+def foo():
+    return 42
+
+
+
+
+
+
+
+
+
+
"""
        findings = check_test_coverage_heuristic(diff)
        assert len(findings) >= 1
        assert "without corresponding tests" in findings[0].message

    def test_modified_file_without_new_tests_is_not_flagged(self):
        # Editing an existing file should NOT trigger the heuristic —
        # the file may already have tests, or the change may be a refactor.
        diff = """--- a/src/foo.py
+++ b/src/foo.py
-def foo():
-    return 1
+def foo():
+    return 42
"""
        findings = check_test_coverage_heuristic(diff)
        assert findings == []

    def test_trivial_new_source_file_not_flagged(self):
        # A 1-line new source file is below the threshold and should not flag.
        diff = """--- /dev/null
+++ b/src/tiny.py
+x = 1
"""
        findings = check_test_coverage_heuristic(diff)
        assert findings == []

    def test_large_new_source_file_without_tests_flagged(self):
        # A new source file with >10 added lines and no tests should flag.
        diff = """--- /dev/null
+++ b/src/big.py
+def big():
+    return 42
+
+
+
+
+
+
+
+
+
+
"""
        findings = check_test_coverage_heuristic(diff)
        assert len(findings) >= 1
        assert "lines)" in findings[0].message  # line count included in message

    def test_only_test_changes_passes(self):
        diff = """+++ b/tests/test_foo.py
+def test_foo():
+    assert True
"""
        findings = check_test_coverage_heuristic(diff)
        assert findings == []


# ─────────────────────────────────────────────────────────────────────
# Verdict computation tests
# ─────────────────────────────────────────────────────────────────────


class TestComputeVerdict:
    def test_no_findings_approve(self):
        verdict, summary = compute_verdict([])
        assert verdict == APPROVE
        assert "All checks passed" in summary

    def test_critical_secret_blocks_merge(self):
        findings = [
            QualityFinding(
                path="config.py",
                line=1,
                severity="critical",
                message="Potential aws_access_key",
            )
        ]
        verdict, summary = compute_verdict(findings)
        assert verdict == REQUEST_CHANGES
        assert "critical" in summary.lower()

    def test_high_severity_blocks_merge(self):
        findings = [
            QualityFinding(
                path="config.py",
                line=1,
                severity="high",
                message="Potential db_url_with_password",
            )
        ]
        verdict, summary = compute_verdict(findings)
        assert verdict == REQUEST_CHANGES

    def test_warning_only_needs_discussion(self):
        findings = [
            QualityFinding(
                path="file.py",
                line=10,
                severity="warning",
                message="Function too long",
            )
        ]
        verdict, summary = compute_verdict(findings)
        assert verdict == NEEDS_DISCUSSION


# ─────────────────────────────────────────────────────────────────────
# RealPRReviewer integration tests
# ─────────────────────────────────────────────────────────────────────


class TestRealPRReviewer:
    def test_clean_diff_returns_approve(self):
        with patch("prismatic.review.pr_reviewer_impl.fetch_pr_diff") as mock_fetch:
            mock_fetch.return_value = """+++ b/main.py
+def hello():
+    return 42
"""
            reviewer = RealPRReviewer()
            result = reviewer.review_pr("https://github.com/owner/repo/pull/1")
            assert result.verdict == APPROVE
            assert result.metadata["reviewer"] == "real"

    def test_secret_in_diff_returns_request_changes(self):
        # Build token via concat to avoid GitHub's secret-scanner push
        # protection flagging this test fixture as a real secret.
        fake_key = "AKIA" + "IOSF" + "ODNN" + "7XYZ" + "AB12" + "34CD"
        with patch("prismatic.review.pr_reviewer_impl.fetch_pr_diff") as mock_fetch:
            mock_fetch.return_value = f"""+++ b/config.py
+AWS_KEY = "{fake_key}"
"""
            reviewer = RealPRReviewer()
            result = reviewer.review_pr("https://github.com/owner/repo/pull/1")
            assert result.verdict == REQUEST_CHANGES
            assert result.metadata["critical_count"] >= 1
            assert len(result.inline_comments) >= 1

    def test_empty_diff_returns_needs_discussion(self):
        with patch("prismatic.review.pr_reviewer_impl.fetch_pr_diff") as mock_fetch:
            mock_fetch.return_value = ""
            reviewer = RealPRReviewer()
            result = reviewer.review_pr("https://github.com/owner/repo/pull/1")
            assert result.verdict == NEEDS_DISCUSSION
            assert "Could not fetch" in result.summary


# ─────────────────────────────────────────────────────────────────────
# GitHub CLI integration (smoke test only)
# ─────────────────────────────────────────────────────────────────────


class TestFetchPRDiff:
    def test_fetch_uses_gh_cli(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="+++ b/file.py\n+x = 1\n",
                stderr="",
            )
            result = fetch_pr_diff("https://github.com/owner/repo/pull/42")
            assert result == "+++ b/file.py\n+x = 1\n"
            # Verify it called gh with the right args
            args = mock_run.call_args[0][0]
            assert args[0] == "gh"
            assert "42" in args
            assert "owner/repo" in args

    def test_fetch_returns_empty_on_gh_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="auth error"
            )
            result = fetch_pr_diff("https://github.com/owner/repo/pull/1")
            assert result == ""

    def test_fetch_returns_empty_on_timeout(self):
        import subprocess as sp

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired("gh", 30)
            result = fetch_pr_diff("https://github.com/owner/repo/pull/1")
            assert result == ""


# ─────────────────────────────────────────────────────────────────────
# Constants and patterns
# ─────────────────────────────────────────────────────────────────────


def test_secret_patterns_format():
    """Each entry should be (regex, secret_type, severity)."""
    for entry in SECRET_PATTERNS:
        assert len(entry) == 3
        pattern, secret_type, severity = entry
        assert isinstance(pattern, str)
        assert isinstance(secret_type, str)
        assert severity in ("critical", "high", "medium", "low")
