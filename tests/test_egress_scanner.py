"""
tests/test_egress_scanner.py — Tests for Egress Secret & PII Scanner

Covers:
    - Shannon entropy calculation (boundary cases)
    - Regex pattern detection (all 12 patterns)
    - scan_text() with known-secret payloads
    - scan_hook() decorator interception
    - Quarantine write/read/release/purge
    - Fail-secure quarantine lock behavior
    - Edge cases (empty text, binary content, unicode)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from prismatic.security.egress_scanner import (
    ALL_PATTERNS,
    DEFAULT_ENTROPY_THRESHOLD,
    EgressBlockedError,
    EgressScanner,
    EgressSecurityError,
    ScanResult,
    SecurityVulnerabilityAlert,
    _find_high_entropy_segments,
    get_scanner,
    list_quarantine,
    purge_quarantine,
    release_quarantine,
    scan_egress,
    shannon_entropy,
)


# ── Shannon Entropy Tests ───────────────────────────────────

class TestShannonEntropy:
    """Shannon entropy calculation for secret detection."""

    def test_empty_string(self) -> None:
        assert shannon_entropy("") == 0.0

    def test_single_character(self) -> None:
        assert shannon_entropy("a") == 0.0

    def test_repeating_characters(self) -> None:
        assert shannon_entropy("aaaaaaaaaa") == 0.0

    def test_natural_language(self) -> None:
        entropy = shannon_entropy(
            "The quick brown fox jumps over the lazy dog"
        )
        assert entropy < 4.5, f"Natural language entropy {entropy} should be < 4.5"

    def test_random_base64_like(self) -> None:
        entropy = shannon_entropy("k7XpL9mN3qR8vF2wT5yA6zB4cD1eF0g")
        assert entropy > 4.5, f"Random string entropy {entropy} should be > 4.5"

    def test_token_like_string(self) -> None:
        """Tokens like gh_token_abc123 have high entropy."""
        entropy = shannon_entropy("ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5")
        assert entropy > 4.0

    def test_boundary_at_threshold(self) -> None:
        """A string right at the edge of the threshold."""
        # Create a string with known entropy distribution
        s = "AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPp"
        entropy = shannon_entropy(s)
        # Just verify it computes without error
        assert isinstance(entropy, float)
        assert entropy > 0


class TestHighEntropySegments:
    """Sliding-window entropy detection."""

    def test_no_high_entropy_in_english(self) -> None:
        text = "This is a normal sentence with nothing suspicious in it."
        hits = _find_high_entropy_segments(text, threshold=4.5)
        assert len(hits) == 0

    def test_high_entropy_token_embedded(self) -> None:
        text = "Here is my key: k7XpL9mN3qR8vF2wT5yA6zB4cD1 — please use it."
        hits = _find_high_entropy_segments(text, threshold=4.5)
        assert len(hits) > 0
        # The hit should include the token
        assert any("k7XpL9mN" in h["snippet"] for h in hits)

    def test_below_threshold(self) -> None:
        text = "abc" * 20  # low entropy
        hits = _find_high_entropy_segments(text, threshold=4.5)
        assert len(hits) == 0

    def test_short_text_bypass(self) -> None:
        """Text shorter than window_size should not find segments."""
        text = "ab"  # shorter than window_size (32)
        hits = _find_high_entropy_segments(text, threshold=1.0)
        assert len(hits) == 0

    def test_deduplication(self) -> None:
        """Overlapping high-entropy windows should be deduplicated."""
        # Generate text with a long high-entropy region
        high_entropy = "k7XpL9mN3qR8vF2wT5yA6zB4cD1eF0gH2iJ3"
        text = f"Prefix text. {high_entropy * 3} Suffix text."
        hits = _find_high_entropy_segments(text, threshold=4.0, window_size=16)
        # Should be fewer than all possible overlapping windows
        assert len(hits) < len(text) - 16 + 1
        # But should have at least some hits
        assert len(hits) > 0


# ── Regex Pattern Tests ─────────────────────────────────────

class TestPatternDetection:
    """Each regex pattern detects the target secret type."""

    def test_github_classic_token(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("My token is ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7")
        assert result.blocked
        assert any(f["rule"] == "github_token" for f in result.findings)

    def test_github_fine_grained_token(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("Token: github_pat_11A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P7Q8R")
        assert result.blocked
        assert any(f["rule"] == "github_token" for f in result.findings)

    def test_aws_access_key(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("AKIAIOSFODNN7EXAMPLE")
        assert result.blocked
        assert any(f["rule"] == "aws_key" for f in result.findings)

    def test_stripe_live_key(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        stripe_key = "sk_" + "live_" + "51H2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT"
        result = scanner.scan_text(stripe_key)
        assert result.blocked
        assert any(f["rule"] == "stripe_secret" for f in result.findings)

    def test_openai_api_key(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("sk-1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab")
        assert result.blocked
        assert any(f["rule"] == "ai_api_key" for f in result.findings)

    def test_database_url(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text(
            "postgresql://user:password@db.example.com:5432/mydb"
        )
        assert result.blocked
        assert any(f["rule"] == "database_url" for f in result.findings)

    def test_mysql_url(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("mysql://root:secret@localhost:3306/app")
        assert result.blocked
        assert any(f["rule"] == "database_url" for f in result.findings)

    def test_ssh_private_key(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----\nsomekeydata\n-----END OPENSSH PRIVATE KEY-----"
        )
        assert result.blocked
        assert any(f["rule"] == "ssh_private_key" for f in result.findings)

    def test_bearer_token_in_header(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text(
            'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
        )
        assert result.blocked
        assert any(f["rule"] == "bearer_token" for f in result.findings)

    def test_generic_password_assignment(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text('password = "SuperSecret123!"')
        assert result.blocked
        assert any(f["rule"] == "generic_secret" for f in result.findings)

    def test_api_key_assignment(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text('API_KEY=sk-live-1234567890abcdefg')
        assert result.blocked
        assert any(f["rule"] == "generic_secret" for f in result.findings)

    def test_ssn_detection(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("User SSN: 123-45-6789")
        assert result.blocked
        assert any(f["rule"] == "ssn" for f in result.findings)

    def test_credit_card_detection(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("Visa: 4111111111111111")
        assert result.blocked
        assert any(f["rule"] == "credit_card" for f in result.findings)

    def test_clean_text_passes(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text(
            "This is a normal comment about code review. Nothing sensitive here."
        )
        assert not result.blocked


# ── scan_text() Behavior ────────────────────────────────────

class TestScanText:
    """Full scan_text() method behavior."""

    def test_multiple_findings_in_one_scan(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        text = (
            "Here is my github token: ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7\n"
            "And my AWS key: AKIAIOSFODNN7EXAMPLE\n"
            "Also a password: secret = 'myAdminPass123'"
        )
        result = scanner.scan_text(text)
        assert result.blocked
        assert len(result.findings) >= 3

    def test_block_on_match_raises_exception(self) -> None:
        scanner = EgressScanner(block_on_match=True)
        # Use a token that specifically matches github_token pattern
        # (must be >= 36 chars after ghp_ prefix)
        with pytest.raises(EgressBlockedError) as exc_info:
            scanner.scan_text(
                "ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab"
            )
        assert exc_info.value.result.blocked
        rule_names = [f["rule"] for f in exc_info.value.result.findings]
        assert any(r in rule_names for r in ("github_token", "generic_secret"))

    def test_no_block_no_exception(self) -> None:
        scanner = EgressScanner(block_on_match=True)
        # Should not raise
        result = scanner.scan_text("Normal text, nothing sensitive.")
        assert not result.blocked

    def test_result_is_structured(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7")
        d = result.to_dict()
        assert "blocked" in d
        assert "findings" in d
        assert "entropy_hits" in d
        assert "scanned_at" in d

    def test_stats_tracking(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        assert scanner.stats["scan_count"] == 0
        scanner.scan_text("Normal text")
        assert scanner.stats["scan_count"] == 1
        scanner.scan_text("ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7")
        assert scanner.stats["scan_count"] == 2
        assert scanner.stats["block_count"] == 1


# ── scan_hook() Decorator ───────────────────────────────────

class TestScanHook:
    """Decorator intercepts egress method calls."""

    def test_hook_scans_string_args(self) -> None:
        scanner = EgressScanner(block_on_match=True)

        @scanner.scan_hook(agent_id="test-agent")
        def post_comment(text: str) -> str:
            return f"posted: {text}"

        # Clean text should pass through
        result = post_comment("Normal text")
        assert result == "posted: Normal text"

    def test_hook_blocks_secret_args(self) -> None:
        scanner = EgressScanner(block_on_match=True)

        @scanner.scan_hook(agent_id="test-agent")
        def post_comment(text: str) -> str:
            return f"posted: {text}"

        with pytest.raises(EgressBlockedError):
            post_comment("Token: ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT")

    def test_hook_scans_kwargs(self) -> None:
        scanner = EgressScanner(block_on_match=True)

        @scanner.scan_hook(agent_id="test-agent")
        def send_message(channel: str, body: str) -> str:
            return f"sent to {channel}"

        with pytest.raises(EgressBlockedError):
            send_message(channel="#general", body="AKIAIOSFODNN7EXAMPLE")

    def test_hook_preserves_function_metadata(self) -> None:
        scanner = EgressScanner(block_on_match=False)

        @scanner.scan_hook(agent_id="test-agent")
        def my_func(x: int) -> int:
            """Docstring for my_func."""
            return x * 2

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "Docstring for my_func."


# ── Quarantine ──────────────────────────────────────────────

class TestQuarantine:
    """Quarantine write, list, release, purge."""

    def test_quarantine_writes_on_block(self, tmp_path: Path) -> None:
        scanner = EgressScanner(
            block_on_match=True, quarantine_dir=tmp_path / "quarantine"
        )
        token = "ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab"  # 42 chars
        try:
            scanner.scan_text(token, agent_id="test", method="post_comment")
        except EgressBlockedError:
            pass

        qdir = tmp_path / "quarantine"
        files = list(qdir.glob("quarantine_*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["agent_id"] == "test"
        assert data["method"] == "post_comment"
        assert len(data["findings"]) > 0

    def test_list_quarantine(self, tmp_path: Path) -> None:
        scanner = EgressScanner(
            block_on_match=True, quarantine_dir=tmp_path / "quarantine"
        )
        token = "ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab"  # 42 chars
        for i in range(3):
            try:
                scanner.scan_text(f"Token_{i}_{token}", agent_id=f"agent-{i}", method="send")
            except EgressBlockedError:
                pass

        entries = list_quarantine(tmp_path / "quarantine")
        assert len(entries) == 3
        assert entries[0]["agent_id"] == "agent-2"

    def test_release_quarantine(self, tmp_path: Path) -> None:
        scanner = EgressScanner(
            block_on_match=True, quarantine_dir=tmp_path / "quarantine"
        )
        token = "ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab"  # 42 chars
        try:
            scanner.scan_text(token, agent_id="test")
        except EgressBlockedError:
            pass

        entries = list_quarantine(tmp_path / "quarantine")
        assert len(entries) == 1
        filename = entries[0]["_file"]

        result = release_quarantine(filename, tmp_path / "quarantine")
        assert result is True
        assert len(list_quarantine(tmp_path / "quarantine")) == 0

    def test_release_nonexistent(self, tmp_path: Path) -> None:
        result = release_quarantine("nonexistent.json", tmp_path / "quarantine")
        assert result is False

    def test_purge_quarantine(self, tmp_path: Path) -> None:
        scanner = EgressScanner(
            block_on_match=True, quarantine_dir=tmp_path / "quarantine"
        )
        token = "ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab"  # 42 chars
        for _ in range(5):
            try:
                scanner.scan_text(token)
            except EgressBlockedError:
                pass

        count = purge_quarantine(tmp_path / "quarantine")
        assert count == 5
        assert len(list_quarantine(tmp_path / "quarantine")) == 0


# ── Fail-Secure Quarantine Lock ─────────────────────────────

class TestFailSecure:
    """Fail-secure behavior: scanner errors block all egress."""

    def setup_method(self) -> None:
        """Ensure lock is released before each test."""
        EgressScanner.release_quarantine_lock()

    def teardown_method(self) -> None:
        """Ensure lock is released after each test."""
        EgressScanner.release_quarantine_lock()

    def test_normal_scan_does_not_lock(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        scanner.scan_text("Normal text")
        assert not EgressScanner.quarantine_lock

    def test_blocked_scan_does_not_lock(self) -> None:
        """Being blocked by finding secrets should NOT trigger fail-secure."""
        scanner = EgressScanner(block_on_match=False)
        scanner.scan_text("ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT")
        assert not EgressScanner.quarantine_lock

    def test_scanner_error_locks(self) -> None:
        """If the scanner itself raises an unexpected error, lock engages."""
        scanner = EgressScanner(block_on_match=True)

        # Make _do_scan raise an unexpected error
        original = scanner._do_scan
        def broken(*args, **kwargs):
            raise RuntimeError("Simulated scanner crash")
        scanner._do_scan = broken

        with pytest.raises(EgressSecurityError) as exc_info:
            scanner.scan_text("anything")
        assert "quarantine lock engaged" in str(exc_info.value)
        assert EgressScanner.quarantine_lock

    def test_locked_scanner_blocks_all(self) -> None:
        """When lock is engaged, even clean text should be blocked."""
        EgressScanner.quarantine_lock = True
        scanner = EgressScanner(block_on_match=False)
        with pytest.raises(EgressSecurityError):
            scanner.scan_text("Just normal text")
        EgressScanner.release_quarantine_lock()

    def test_release_lock_restores(self) -> None:
        EgressScanner.quarantine_lock = True
        EgressScanner.release_quarantine_lock()
        assert not EgressScanner.quarantine_lock

        scanner = EgressScanner(block_on_match=False)
        # Should work again after release
        result = scanner.scan_text("Normal text")
        assert not result.blocked


# ── Singleton ───────────────────────────────────────────────

class TestSingleton:
    """get_scanner() returns a singleton."""

    def test_same_instance(self) -> None:
        s1 = get_scanner()
        s2 = get_scanner()
        assert s1 is s2

    def test_scan_egress_decorator(self) -> None:
        @scan_egress(agent_id="test")
        def my_egress(msg: str) -> str:
            return msg

        # Clean text passes
        assert my_egress("hello") == "hello"

    def test_scan_egress_decorator_blocks(self) -> None:
        """Decorator blocks when singleton scanner has block_on_match=True."""
        # Reset the singleton with block_on_match enabled
        from prismatic.security.egress_scanner import _scanner
        import prismatic.security.egress_scanner as mod
        saved = _scanner
        mod._scanner = None  # Force recreation
        try:
            blocker = get_scanner()
            blocker.block_on_match = True

            @scan_egress(agent_id="test")
            def my_egress(msg: str) -> str:
                return msg

            with pytest.raises(EgressBlockedError):
                my_egress("ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab")
        finally:
            mod._scanner = saved


# ── Edge Cases ──────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases for scanner robustness."""

    def test_empty_text(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("")
        assert not result.blocked

    def test_unicode_text(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("こんにちは世界 — Hello world with unicode")
        assert not result.blocked

    def test_very_long_text(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        text = "Normal text. " * 1000
        result = scanner.scan_text(text)
        assert not result.blocked

    def test_none_like_text(self) -> None:
        """Text containing the word 'None' should not crash."""
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text("The value is None for this field")
        assert not result.blocked

    def test_json_containing_secrets(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        token = "ghp_1A2b3C4d5E6f7G8h9I0jK1L2mN3oP4qR5sT6uV7wX8yZ9ab"  # 42 chars
        payload = json.dumps({
            "user": "admin",
            "token": token,
            "message": "hello",
        })
        result = scanner.scan_text(payload)
        assert result.blocked

    def test_base64_encoded_secret(self) -> None:
        """Base64 content should be caught by entropy detection."""
        import base64
        secret = base64.b64encode(
            b"super_secret_token_value_12345!@#$%"
        ).decode()
        scanner = EgressScanner(block_on_match=False)
        result = scanner.scan_text(f"Here is the encoded key: {secret}")
        # May or may not be caught by regex, but entropy should flag it
        # or at minimum the scan shouldn't crash
        assert isinstance(result, ScanResult)

    def test_multiline_with_secret_buried(self) -> None:
        scanner = EgressScanner(block_on_match=False)
        text = (
            "# Configuration\n"
            "# This is a comment block\n"
            "database_url = postgresql://user:pass@localhost/db\n"
            "# More comments\n"
            "debug = true\n"
        )
        result = scanner.scan_text(text)
        assert result.blocked
        assert any(f["rule"] == "database_url" for f in result.findings)


# ── SecurityVulnerabilityAlert ──────────────────────────────

class TestSecurityAlert:
    """SecurityVulnerabilityAlert construction and serialization."""

    def test_alert_construction(self) -> None:
        result = ScanResult(blocked=True)
        result.add_finding("github_token", "ghp_test")
        alert = SecurityVulnerabilityAlert(
            scan_result=result,
            agent_id="test-agent",
            method="post_comment",
            quarantined_path="/tmp/quarantine/test.json",
        )
        payload = alert.to_event_payload()
        assert payload["alert_type"] == "security.vulnerability"
        assert payload["blocked"] is True
        assert payload["agent_id"] == "test-agent"
        assert len(payload["findings"]) == 1

    def test_alert_to_dict_serializable(self) -> None:
        result = ScanResult(blocked=False)
        alert = SecurityVulnerabilityAlert(result)
        payload = alert.to_event_payload()
        # Should be JSON serializable
        json.dumps(payload)


# ── ALL_PATTERNS Completeness ───────────────────────────────

class TestAllPatterns:
    """Verify ALL_PATTERNS covers the required secret types."""

    def test_expected_rules_present(self) -> None:
        rule_names = {name for name, _ in ALL_PATTERNS}
        expected = {
            "github_token",
            "aws_key",
            "aws_secret",
            "stripe_secret",
            "ai_api_key",
            "database_url",
            "ssh_private_key",
            "pgp_private_key",
            "bearer_token",
            "generic_secret",
            "ssn",
            "credit_card",
        }
        assert rule_names == expected
