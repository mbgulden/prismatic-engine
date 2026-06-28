"""Tests for prismatic.review.registry (Gap 9 / Part B)."""

from __future__ import annotations

import pytest

from prismatic.review.registry import (
    ReviewerRegistry,
)


# Composition semantics


class TestReviewerRegistryBasics:
    def test_empty_registry_produces_empty_spec(self):
        r = ReviewerRegistry()
        spec = r.compose()
        assert spec.secret_patterns == ()
        assert spec.checks == ()
        assert spec.impact_rules == ()

    def test_secret_pattern_count_starts_at_zero(self):
        r = ReviewerRegistry()
        assert r.secret_pattern_count == 0
        assert r.check_count == 0
        assert r.impact_rule_count == 0

    def test_register_secret_pattern_increments_count(self):
        r = ReviewerRegistry()
        r.register_secret_pattern(r"foo[0-9]{8}", "company_token", "high")
        assert r.secret_pattern_count == 1

    def test_register_check_increments_count(self):
        r = ReviewerRegistry()

        def check_one(diff):
            return []

        r.register_check(check_one)
        assert r.check_count == 1

    def test_register_impact_rule_increments_count(self):
        r = ReviewerRegistry()
        r.register_impact_rule(lambda result, current: None)
        assert r.impact_rule_count == 1


# Secret-pattern registration


class TestSecretPatternRegistration:
    def test_valid_severity_accepted(self):
        r = ReviewerRegistry()
        for sev in ("critical", "high", "medium", "warning"):
            r.register_secret_pattern(r"x[0-9]", f"kind_{sev}", sev)
        assert r.secret_pattern_count == 4

    def test_invalid_severity_rejected(self):
        r = ReviewerRegistry()
        with pytest.raises(ValueError, match="Invalid severity"):
            r.register_secret_pattern(r"x[0-9]", "bad_kind", "extreme")

    def test_duplicate_pattern_idempotent(self):
        r = ReviewerRegistry()
        r.register_secret_pattern(r"foo[0-9]{8}", "company_token", "high")
        r.register_secret_pattern(r"foo[0-9]{8}", "company_token", "high")
        r.register_secret_pattern(r"foo[0-9]{8}", "company_token", "critical")
        assert r.secret_pattern_count == 1

    def test_different_kind_not_deduped(self):
        r = ReviewerRegistry()
        r.register_secret_pattern(r"foo[0-9]{8}", "kind_a", "high")
        r.register_secret_pattern(r"foo[0-9]{8}", "kind_b", "high")
        assert r.secret_pattern_count == 2

    def test_patterns_preserve_registration_order(self):
        r = ReviewerRegistry()
        r.register_secret_pattern(r"a[0-9]", "kind_a", "high")
        r.register_secret_pattern(r"b[0-9]", "kind_b", "high")
        r.register_secret_pattern(r"c[0-9]", "kind_c", "high")
        spec = r.compose()
        kinds = [p[1] for p in spec.secret_patterns]
        assert kinds == ["kind_a", "kind_b", "kind_c"]


# Check registration


class TestCheckRegistration:
    def test_unnamed_checks_appended(self):
        r = ReviewerRegistry()

        def check_one(diff):
            return []

        def check_two(diff):
            return []

        r.register_check(check_one)
        r.register_check(check_two)
        assert r.check_count == 2

    def test_named_check_replaces_existing(self):
        r = ReviewerRegistry()

        def check_v1(diff):
            return []

        def check_v2(diff):
            return []

        r.register_check(check_v1, name="mycheck")
        r.register_check(check_v2, name="mycheck")
        assert r.check_count == 1
        spec = r.compose()
        assert spec.checks[0] is check_v2

    def test_named_check_does_not_replace_unnamed(self):
        r = ReviewerRegistry()

        def named(diff):
            return []

        def unnamed(diff):
            return []

        r.register_check(named, name="x")
        r.register_check(unnamed)
        assert r.check_count == 2


# Impact-rule registration


class TestImpactRuleRegistration:
    def test_rules_fire_in_registration_order(self):
        r = ReviewerRegistry()
        calls = []

        def rule_a(result, current):
            calls.append("a")
            return None

        def rule_b(result, current):
            calls.append("b")
            return "blocker"

        r.register_impact_rule(rule_a)
        r.register_impact_rule(rule_b)
        spec = r.compose()
        current = "trivial"
        for rule in spec.impact_rules:
            new = rule(None, current)
            if new is not None:
                current = new
                break
        assert calls == ["a", "b"]
        assert current == "blocker"

    def test_first_non_none_wins(self):
        r = ReviewerRegistry()

        def rule_a(result, current):
            return None

        def rule_b(result, current):
            return "major"

        def rule_c(result, current):
            return "blocker"

        r.register_impact_rule(rule_a)
        r.register_impact_rule(rule_b)
        r.register_impact_rule(rule_c)
        spec = r.compose()
        current = "trivial"
        for rule in spec.impact_rules:
            new = rule(None, current)
            if new is not None:
                current = new
                break
        assert current == "major"


# compose() snapshot safety


class TestComposeSnapshot:
    def test_compose_returns_frozen_dataclass(self):
        r = ReviewerRegistry()
        r.register_secret_pattern(r"x", "k", "high")
        spec = r.compose()
        with pytest.raises(Exception):
            spec.secret_patterns = ()

    def test_post_compose_registration_does_not_affect_snapshot(self):
        r = ReviewerRegistry()
        r.register_secret_pattern(r"x", "k", "high")
        spec1 = r.compose()
        r.register_secret_pattern(r"y", "k2", "high")
        spec2 = r.compose()
        assert len(spec1.secret_patterns) == 1
        assert len(spec2.secret_patterns) == 2

    def test_tuple_types_in_spec(self):
        r = ReviewerRegistry()
        r.register_secret_pattern(r"x", "k", "high")
        r.register_check(lambda d: [])
        r.register_impact_rule(lambda r, c: None)
        spec = r.compose()
        assert isinstance(spec.secret_patterns, tuple)
        assert isinstance(spec.checks, tuple)
        assert isinstance(spec.impact_rules, tuple)


# Integration with RealPRReviewer (mocked diff)


class TestRegistryIntegrationWithReviewer:
    def test_reviewer_without_registry_unchanged(self):
        from prismatic.review import RealPRReviewer

        reviewer = RealPRReviewer()
        assert reviewer.registry is None
        assert reviewer.timeout_seconds == 30

    def test_reviewer_with_registry_stores_it(self):
        from prismatic.review import RealPRReviewer

        reg = ReviewerRegistry()
        reviewer = RealPRReviewer(registry=reg)
        assert reviewer.registry is reg

    def test_registry_pattern_detected_in_review(self, monkeypatch):
        from prismatic.review import RealPRReviewer

        reg = ReviewerRegistry()
        reg.register_secret_pattern(
            r"COMPANY_INT_[A-Z]{8}",
            "company_internal_token",
            "critical",
        )

        test_token = "COMPANY_INT_ABCDEFGH"
        diff = "+++ b/config.py\n+API_KEY=" + test_token + "\n"

        def mock_fetch(url, timeout=30):
            return diff

        monkeypatch.setattr(
            "prismatic.review.pr_reviewer_impl.fetch_pr_diff",
            mock_fetch,
        )

        reviewer = RealPRReviewer(registry=reg)
        result = reviewer.review_pr("https://github.com/o/r/pull/1")

        plugin_findings = [
            c for c in result.inline_comments if "plugin" in c.body.lower()
        ]
        assert len(plugin_findings) >= 1
        assert any("company_internal_token" in c.body for c in plugin_findings)
        assert result.metadata["registry_pattern_count"] == 1

    def test_registry_check_runs_after_builtins(self, monkeypatch):
        from prismatic.review import RealPRReviewer
        from prismatic.review.pr_reviewer_impl import QualityFinding

        reg = ReviewerRegistry()
        captured_diffs = []

        def my_check(diff):
            captured_diffs.append(diff)
            return [
                QualityFinding(
                    path="custom.py",
                    line=99,
                    severity="warning",
                    message="plugin-detected-issue",
                )
            ]

        reg.register_check(my_check, name="custom_check")

        monkeypatch.setattr(
            "prismatic.review.pr_reviewer_impl.fetch_pr_diff",
            lambda url, timeout=30: "+++ b/main.py\n+x = 1\n",
        )

        reviewer = RealPRReviewer(registry=reg)
        result = reviewer.review_pr("https://github.com/o/r/pull/1")

        assert len(captured_diffs) == 1
        assert any("plugin-detected-issue" in c.body for c in result.inline_comments)
        assert result.metadata["registry_check_count"] == 1

    def test_plugin_exception_isolated_to_finding(self, monkeypatch):
        from prismatic.review import RealPRReviewer
        from prismatic.review.pr_reviewer_impl import QualityFinding

        reg = ReviewerRegistry()

        def broken_check(diff):
            raise RuntimeError("plugin crashed")

        def good_check(diff):
            return [
                QualityFinding(path="x.py", line=1, severity="warning", message="good")
            ]

        reg.register_check(broken_check, name="broken")
        reg.register_check(good_check, name="good")

        monkeypatch.setattr(
            "prismatic.review.pr_reviewer_impl.fetch_pr_diff",
            lambda url, timeout=30: "+++ b/main.py\n+x = 1\n",
        )

        reviewer = RealPRReviewer(registry=reg)
        result = reviewer.review_pr("https://github.com/o/r/pull/1")

        bodies = [c.body for c in result.inline_comments]
        assert any("Plugin check failed" in b for b in bodies)
        assert any("good" in b for b in bodies)


# Hooks module


class TestHooksModule:
    def test_all_hooks_are_strings(self):
        from prismatic.review.hooks import ALL_HOOKS

        for hook in ALL_HOOKS:
            assert isinstance(hook, str)
            assert hook

    def test_hook_names_are_unique(self):
        from prismatic.review.hooks import ALL_HOOKS

        assert len(ALL_HOOKS) == len(set(ALL_HOOKS))

    def test_exported_from_package(self):
        from prismatic.review import (
            HOOK_BEFORE_SECRET_SCAN,
            HOOK_BEFORE_NED_REVIEW,
            ALL_HOOKS,
        )

        assert HOOK_BEFORE_SECRET_SCAN in ALL_HOOKS
        assert HOOK_BEFORE_NED_REVIEW in ALL_HOOKS

    def test_quality_check_importable_from_package(self):
        """P0 fix from meta-review 2026-06-28: QualityCheck must be importable.

        Plugin authors following prismatic-distribution-checklist.md will
        try `from prismatic.review import QualityCheck` as their first
        line. This test guards against the missing export regressing.
        """
        from prismatic.review import QualityCheck
        from prismatic.review import __all__ as all_exports

        assert "QualityCheck" in all_exports
        # Use the alias to satisfy the linter and prove it works.
        # QualityCheck is Callable[[str], list[Any]] -- check the alias
        # origin exists (it's a TypeAlias, so __class__ check is meaningful).
        assert QualityCheck is not None

    def test_register_impact_rule_docstring_warns(self):
        """Gap 11 update: register_impact_rule docstring must describe the
        now-active (wired) channel, NOT the old inert status.

        The channel was wired in Gap 11 / PR #XX. Plugin authors should
        see that their impact rules will take effect in production reviews
        via PipelineOrchestrator.process().
        """
        from prismatic.review import ReviewerRegistry

        doc = ReviewerRegistry.register_impact_rule.__doc__ or ""
        assert "Currently inert" not in doc, (
            "register_impact_rule() docstring must NOT say 'Currently inert' "
            "after Gap 11 wires the channel."
        )
        assert "TODO Gap 9 / Part C" not in doc, (
            "register_impact_rule() docstring must NOT still reference the old TODO "
            "after Gap 11 lands."
        )
        # Positive contract: docstring references where the wiring lives.
        assert "Wired" in doc, (
            "register_impact_rule() docstring must confirm the channel is wired "
            "(mention 'Wired' and point to PipelineOrchestrator.process())."
        )
        assert "PipelineOrchestrator" in doc, (
            "register_impact_rule() docstring must reference PipelineOrchestrator "
            "so plugin authors know where their rules fire."
        )
