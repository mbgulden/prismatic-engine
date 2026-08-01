"""Risk-tier policy engine for the Review/Merge Factory V1.

Loads a versioned policy YAML file and classifies changed paths into
risk tiers (0–3).  Implements the fail-closed escalation rule from §4
of the OKF: if no rule matches AND the verifier detects a high-risk
pattern, it auto-escalates to Tier 2.

Usage
-----
    from prismatic.review_factory.policy import PolicyEngine

    engine = PolicyEngine.from_yaml("prismatic/review_policy/v1.yaml")
    tier = engine.classify(["prismatic/auth/oauth.py", "docs/readme.md"])
    # tier == RiskTier.SENSITIVE (2) — auth/** matched

    # Or with fail-closed escalation:
    tier, escalation = engine.classify_with_escalation(
        ["prismatic/new_module/something.py"]
    )
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismatic.review_factory.models import RiskTier

# Try to import PyYAML; fall back to a basic parser if unavailable
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ─────────────────────────────────────────────────────────────────────
# Policy rule model
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyRule:
    """A single classification rule from the policy file."""

    rule_id: str
    match_paths: tuple[str, ...]  # glob patterns
    risk_tier: int
    required_witnesses: int = 0
    witness_required: bool = False  # for Tier 0 explicit opt-out
    deterministic_only: bool = False
    notes: str = ""

    def matches(self, path: str) -> bool:
        """Return True if any glob pattern matches the given path."""
        for pattern in self.match_paths:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False


@dataclass(frozen=True)
class PolicyDefault:
    """Default classification when no rule matches."""

    risk_tier: int = RiskTier.STANDARD
    witness_required: bool = False


# ─────────────────────────────────────────────────────────────────────
# High-risk pattern detector (for fail-closed escalation)
# ─────────────────────────────────────────────────────────────────────

# Patterns that should trigger auto-escalation if the policy doesn't
# explicitly classify them.
_HIGH_RISK_PATTERNS = [
    re.compile(r"(auth|oauth|login|session|token)", re.IGNORECASE),
    re.compile(r"(sqlite|\.db$|migration|schema)", re.IGNORECASE),
    re.compile(r"(credential|secret|password|key\.pem)", re.IGNORECASE),
    re.compile(r"(deploy|systemd|\.service|prod)", re.IGNORECASE),
    re.compile(r"(merge_executor|review_factory|policy)", re.IGNORECASE),
]


def _has_high_risk_signal(path: str) -> bool:
    """Check if a path name contains high-risk indicators."""
    return any(p.search(path) for p in _HIGH_RISK_PATTERNS)


# ─────────────────────────────────────────────────────────────────────
# Classification result
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ClassificationResult:
    """Result of classifying a set of changed paths."""

    risk_tier: int
    required_witnesses: int
    matched_rules: list[str]  # rule IDs that matched
    policy_version: str
    escalated: bool = False  # True if fail-closed escalation fired
    escalation_reason: str = ""

    @property
    def deterministic_only(self) -> bool:
        return self.risk_tier == RiskTier.DETERMINISTIC_ONLY

    @property
    def needs_human(self) -> bool:
        return self.risk_tier >= RiskTier.PRODUCTION


# ─────────────────────────────────────────────────────────────────────
# Policy engine
# ─────────────────────────────────────────────────────────────────────


class PolicyEngine:
    """Risk-tier classifier driven by a versioned policy file.

    The engine evaluates all changed paths against all rules and
    returns the HIGHEST risk tier found.  Fail-closed: if no rule
    matches but high-risk patterns are detected, escalates to Tier 2.
    """

    def __init__(
        self,
        rules: list[PolicyRule],
        default: PolicyDefault,
        version: str = "v1",
    ):
        self.rules = rules
        self.default = default
        self.version = version

    @classmethod
    def from_yaml(cls, path: str | Path) -> PolicyEngine:
        """Load policy from a YAML file.

        Falls back to built-in default policy if the file doesn't
        exist or PyYAML is not installed.
        """
        path = Path(path)
        if not path.exists() or not _HAS_YAML:
            return cls.builtin_default()

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return cls._from_dict(data)

    @classmethod
    def builtin_default(cls) -> PolicyEngine:
        """Return the built-in default policy (matches OKF §4)."""
        rules = [
            PolicyRule(
                rule_id="tier-3-prod",
                match_paths=(
                    "deploy/**",
                    "systemd/**",
                    "**/credentials*",
                    "**/secrets/**",
                ),
                risk_tier=3,
                required_witnesses=2,
                notes="Production authority changes",
            ),
            PolicyRule(
                rule_id="tier-2-auth",
                match_paths=(
                    "prismatic/auth/**",
                    "prismatic/migrations/**",
                    "prismatic/**/sqlite*",
                    "prismatic/gateway/auth*",
                ),
                risk_tier=2,
                required_witnesses=2,
                notes="Sensitive surfaces",
            ),
            PolicyRule(
                rule_id="tier-2-git-mutation",
                match_paths=(
                    "prismatic/review/**",
                    "prismatic/merge_executor*",
                    "prismatic/policy/**",
                    "prismatic/review_factory/**",
                ),
                risk_tier=2,
                required_witnesses=2,
                notes="Git mutation + factory-self",
            ),
            PolicyRule(
                rule_id="tier-0-docs",
                match_paths=(
                    "docs/**",
                    "*.md",
                    "fixtures/**",
                    "**/test_data/**",
                ),
                risk_tier=0,
                witness_required=False,
                deterministic_only=True,
                notes="Documentation and test data",
            ),
            PolicyRule(
                rule_id="tier-0-generated",
                match_paths=(
                    "**/generated/**",
                    "**/*.pb.go",
                    "**/schema_generated*",
                ),
                risk_tier=0,
                witness_required=False,
                deterministic_only=True,
                notes="Generated code",
            ),
        ]
        return cls(
            rules=rules,
            default=PolicyDefault(
                risk_tier=RiskTier.STANDARD,
                witness_required=False,
            ),
            version="v1-builtin",
        )

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> PolicyEngine:
        """Parse a policy dict (from YAML)."""
        version = str(data.get("version", "v1"))
        rules = []
        for rule_data in data.get("rules", []):
            rules.append(
                PolicyRule(
                    rule_id=rule_data["id"],
                    match_paths=tuple(rule_data.get("match_paths", [])),
                    risk_tier=int(rule_data.get("risk_tier", 1)),
                    required_witnesses=int(rule_data.get("required_witnesses", 0)),
                    witness_required=bool(rule_data.get("witness_required", False)),
                    deterministic_only=bool(rule_data.get("deterministic_only", False)),
                    notes=str(rule_data.get("notes", "")),
                )
            )

        default_data = data.get("default", {})
        default = PolicyDefault(
            risk_tier=int(default_data.get("risk_tier", 1)),
            witness_required=bool(default_data.get("witness_required", False)),
        )
        return cls(rules=rules, default=default, version=version)

    def classify(self, changed_paths: list[str]) -> ClassificationResult:
        """Classify a set of changed paths into a risk tier.

        Returns the HIGHEST tier across all paths.  If no rule matches
        any path, uses the default tier.  If the default is Tier 1 but
        a high-risk pattern is detected, escalates to Tier 2
        (fail-closed).
        """
        if not changed_paths:
            return ClassificationResult(
                risk_tier=self.default.risk_tier,
                required_witnesses=0,
                matched_rules=[],
                policy_version=self.version,
            )

        max_tier = -1
        max_witnesses = 0
        matched_rules: list[str] = []
        unmatched_paths: list[str] = []

        for path in changed_paths:
            path_matched = False
            for rule in self.rules:
                if rule.matches(path):
                    path_matched = True
                    max_tier = max(max_tier, rule.risk_tier)
                    max_witnesses = max(max_witnesses, rule.required_witnesses)
                    if rule.rule_id not in matched_rules:
                        matched_rules.append(rule.rule_id)

            if not path_matched:
                unmatched_paths.append(path)

        # Apply default for unmatched paths
        if max_tier < 0:
            max_tier = self.default.risk_tier

        # Fail-closed escalation: if we fell through to default (Tier 1)
        # but high-risk patterns are present, escalate to Tier 2
        escalated = False
        escalation_reason = ""
        if not matched_rules or max_tier <= RiskTier.STANDARD:
            high_risk_unmatched = [
                p for p in unmatched_paths if _has_high_risk_signal(p)
            ]
            if high_risk_unmatched:
                escalated = True
                max_tier = max(max_tier, RiskTier.SENSITIVE)
                max_witnesses = max(max_witnesses, 2)
                escalation_reason = (
                    f"policy_miss_escalation: {len(high_risk_unmatched)} unmatched "
                    f"high-risk paths: {high_risk_unmatched[:5]}"
                )

        return ClassificationResult(
            risk_tier=max_tier,
            required_witnesses=max_witnesses,
            matched_rules=matched_rules,
            policy_version=self.version,
            escalated=escalated,
            escalation_reason=escalation_reason,
        )

    def classify_with_witnesses(self, changed_paths: list[str]) -> tuple[int, int]:
        """Convenience: return (risk_tier, required_witnesses)."""
        result = self.classify(changed_paths)
        return result.risk_tier, result.required_witnesses
