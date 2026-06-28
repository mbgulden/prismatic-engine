"""Plugin-internal tests for prismatic_hello_world.

5 tests covering:
  1. Manifest is valid YAML
  2. Manifest has every required field
  3. on_init wires all four pattern registrations through a duck-typed context
  4. no_hello_comments returns [] for clean diffs (length == 0)
  5. Action rule does not leak into the impact channel
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

# Make the plugin importable as `prismatic_hello_world.plugin` the way
# PluginLoader does at runtime — by inserting the parent of the package
# directory onto sys.path. In production this happens inside PluginLoader
# itself (it does ``sys.path.insert(0, plugin_root)`` where plugin_root is
# the manifest's parent directory), so we mirror that here.
_PLUGIN_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))

from prismatic_hello_world.plugin import (  # noqa: E402
    HelloWorldPlugin,
    escalate_when_hello,
    force_rework_when_hello_world,
    no_hello_comments,
)


# Path to the manifest, used by tests 1 + 2.
_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "plugin-manifest.yaml"


# ─────────────────────────────────────────────────────────────────────
# Test 1 — manifest is valid YAML
# ─────────────────────────────────────────────────────────────────────


def test_manifest_is_valid_yaml():
    """plugin-manifest.yaml must parse without error and return a mapping."""
    with open(_MANIFEST_PATH, "r") as fh:
        loaded = yaml.safe_load(fh)
    assert isinstance(loaded, dict), (
        f"Expected manifest to parse to a dict, got {type(loaded).__name__}"
    )
    # Spot-check the core identity fields so a typo doesn't sneak through.
    assert loaded["name"] == "prismatic-hello-world"
    assert loaded["entry_point"] == "prismatic_hello_world.plugin:HelloWorldPlugin"


# ─────────────────────────────────────────────────────────────────────
# Test 2 — manifest has every required field (per PluginLoader._load_plugin)
# ─────────────────────────────────────────────────────────────────────


def test_manifest_has_required_fields():
    """PluginLoader rejects manifests missing name/version/entry_point/core_constraint."""
    with open(_MANIFEST_PATH, "r") as fh:
        loaded = yaml.safe_load(fh)
    required = ("name", "version", "entry_point", "core_version_constraint")
    for field_name in required:
        assert field_name in loaded, f"Manifest missing required field {field_name!r}"
        assert loaded[field_name], f"Manifest field {field_name!r} is empty"
    # core_version_constraint must be parseable as a SpecifierSet.
    from packaging.specifiers import SpecifierSet

    SpecifierSet(loaded["core_version_constraint"])


# ─────────────────────────────────────────────────────────────────────
# Test 3 — on_init wires all four registrations through the registry
# ─────────────────────────────────────────────────────────────────────


def test_register_wires_all_four_patterns():
    """on_init must call every register_* method on the registry exactly once."""

    class SpyRegistry:
        """Duck-typed registry that records every method call."""

        def __init__(self) -> None:
            self.secret_calls: list[tuple] = []
            self.check_calls: list[tuple] = []
            self.impact_calls: list[tuple] = []
            self.action_calls: list[tuple] = []

        def register_secret_pattern(self, regex, kind, severity):
            self.secret_calls.append((regex, kind, severity))

        def register_check(self, fn, *, name=None):
            self.check_calls.append((fn, name))

        def register_impact_rule(self, fn):
            self.impact_calls.append(fn)

        def register_action_rule(self, fn):
            self.action_calls.append(fn)

    class DuckContext:
        """Bare duck-typed context carrying only review_registry (the real
        PluginContext dataclass has no review_registry attribute)."""

        def __init__(self, registry):
            self.review_registry = registry
            # Other PluginContext fields — populated with placeholders that
            # the plugin's on_init never touches, but exist so the duck
            # type matches the real dataclass shape closely enough.
            self.config: dict = {}
            self.db_connection = None
            self.state_dir = ""
            self.telemetry_client = None
            self.lock_manager = None

    registry = SpyRegistry()
    ctx = DuckContext(registry)

    # DuckContext is intentionally a structural superset for the test;
    # PluginContext doesn't declare review_registry, so we use a duck type.
    HelloWorldPlugin().on_init(ctx)  # type: ignore[arg-type]

    # 1. Secret pattern — exactly one registration, with the documented args.
    assert len(registry.secret_calls) == 1
    regex, kind, severity = registry.secret_calls[0]
    assert regex == r"hello_[a-z0-9]{16}"
    assert kind == "hello_world_token"
    assert severity == "warning"

    # 2. Quality check — exactly one registration under the stable name.
    assert len(registry.check_calls) == 1
    fn, name = registry.check_calls[0]
    assert fn is no_hello_comments
    assert name == "hello.no_comments"

    # 3. Impact rule — exactly one registration, fn must be the canonical
    #    escalation callable.
    assert len(registry.impact_calls) == 1
    assert registry.impact_calls[0] is escalate_when_hello

    # 4. Action rule — exactly one registration, fn must be the canonical
    #    action callable.
    assert len(registry.action_calls) == 1
    assert registry.action_calls[0] is force_rework_when_hello_world


# ─────────────────────────────────────────────────────────────────────
# Test 4 — no_hello_comments returns empty list for clean diff
# ─────────────────────────────────────────────────────────────────────


def test_no_hello_comments_check_returns_empty_for_clean_diff():
    """The quality check must produce zero findings when the diff is clean.

    This is the "happy path" assertion: a PR with no `# hello` comments
    should not be flagged. We also assert the return type is a list so
    downstream code that iterates the result doesn't trip on a None.
    """
    diff = (
        "diff --git a/example.py b/example.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def greet():\n"
        "+    return 'hi'\n"
        " def main():\n"
        "+    greet()\n"
    )
    result = no_hello_comments(diff)
    assert isinstance(result, list)
    assert len(result) == 0

    # And the negative case: a diff WITH a '# hello' comment produces a
    # non-empty finding list. This guards against accidental regex drift.
    flagged_diff = diff + "+# hello world — placeholder comment\n"
    flagged = no_hello_comments(flagged_diff)
    assert len(flagged) >= 1


# ─────────────────────────────────────────────────────────────────────
# Test 5 — action rule does not leak into the impact channel
# ─────────────────────────────────────────────────────────────────────


def test_action_rule_does_not_leak_into_impact_channel():
    """An action rule registered for a PR whose summary mentions hello_world
    must change ``decision.action`` but leave ``decision.impact`` as a valid
    IMPACT_LEVEL (i.e., the action string 'rework' must not leak into the
    impact slot).

    This exercises the Gap 11 channel-separation invariant: impact and
    action rules fire in separate pools, and apply_impact_rules()
    validates the returned value against the target channel's valid set.
    """
    from prismatic.review.pipeline import (
        ACTION_REWORK,
        IMPACT_LEVELS,
        PipelineOrchestrator,
    )
    from prismatic.review.pr_reviewer import APPROVE, PRReviewResult
    from prismatic.review.registry import ReviewerRegistry

    registry = ReviewerRegistry()
    # Register ONLY an action rule; no impact rule. This way any
    # non-None impact value must come from the built-in classifier,
    # and any non-None action override must come from our action rule.
    registry.register_action_rule(force_rework_when_hello_world)

    orchestrator = PipelineOrchestrator(registry=registry)

    # Build a result whose summary contains "hello_world" so the action
    # rule fires, but with verdict=APPROVE so classify_impact() returns
    # IMPACT_TRIVIAL and decide_next_action() returns ACTION_ADVANCE.
    # The action rule must override ADVANCE → REWORK; the impact must
    # stay at TRIVIAL.
    result = PRReviewResult(
        verdict=APPROVE,
        summary="Touched hello_world_token in config; nothing else.",
        inline_comments=[],
        metadata={
            "critical_count": 0,
            "high_count": 0,
            "warning_count": 0,
        },
    )
    decision = orchestrator.process(
        identifier="GRO-TEST", pr_url="https://example.test/pr/1", result=result
    )

    # Impact channel untouched — still a valid IMPACT_LEVEL (TRIVIAL here).
    assert decision.impact in IMPACT_LEVELS
    assert decision.impact == "trivial"

    # Action channel — action rule overrode ADVANCE → REWORK.
    assert decision.action == ACTION_REWORK


# Silence unused import warnings (Any is used implicitly via dict generics).
_ = Any
