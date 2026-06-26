"""tests/core/test_handoff.py — Handoff Contracts specification tests.

Covers:
    * FieldSpec construction rules (required-with-default rejection, type
      enforcement, Prismatic extension types).
    * RetryPolicy math across all four backoff curves and edge cases.
    * SLASpec validation (negative values, availability out of [0, 1]).
    * CapabilityRequirement domain:verb format and semver floor.
    * HandoffContract field-uniqueness rule and serialisation round-trip.
    * validate_payload / validate_capabilities / validate_handoff happy paths
      and the canonical failure modes (missing required, wrong type,
      unexpected field, missing capability).
    * Built-in contracts (fred-to-kai, kai-to-agy) parse and round-trip.
"""

from __future__ import annotations

import unittest

from prismatic.core.handoff import (
    BUILTIN_CONTRACTS,
    BackoffStrategy,
    CapabilityRequirement,
    FieldSpec,
    FieldType,
    HandoffContract,
    RetryPolicy,
    SLASpec,
    ValidationFailure,
    validate_capabilities,
    validate_handoff,
    validate_handoff_or_raise,
    validate_payload,
)


# ═══════════════════════════════════════════════════════════════════
# FieldSpec
# ═══════════════════════════════════════════════════════════════════


class FieldSpecTests(unittest.TestCase):
    def test_required_field_must_not_have_default(self) -> None:
        with self.assertRaises(ValueError):
            FieldSpec("x", FieldType.STRING, default="hi")

    def test_optional_field_with_default_round_trips(self) -> None:
        f = FieldSpec("x", FieldType.STRING, required=False, default="hi")
        self.assertEqual(f.default, "hi")
        d = f.to_dict()
        self.assertEqual(d["default"], "hi")
        self.assertFalse(d["required"])

    def test_unknown_type_rejected(self) -> None:
        with self.assertRaises(TypeError):
            FieldSpec("x", "string")  # type: ignore[arg-type]

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FieldSpec("", FieldType.STRING)


# ═══════════════════════════════════════════════════════════════════
# RetryPolicy math
# ═══════════════════════════════════════════════════════════════════


class RetryPolicyTests(unittest.TestCase):
    def test_no_retries_zero_delay(self) -> None:
        rp = RetryPolicy(max_attempts=1, backoff=BackoffStrategy.NONE)
        for attempt in range(1, 5):
            self.assertEqual(rp.delay_ms(attempt), 0)

    def test_constant_backoff(self) -> None:
        rp = RetryPolicy(
            max_attempts=4,
            base_delay_ms=500,
            max_delay_ms=10_000,
            backoff=BackoffStrategy.CONSTANT,
        )
        for attempt in range(1, 4):
            self.assertEqual(rp.delay_ms(attempt), 500)

    def test_linear_backoff_grows_linearly(self) -> None:
        rp = RetryPolicy(
            max_attempts=5,
            base_delay_ms=100,
            max_delay_ms=10_000,
            backoff=BackoffStrategy.LINEAR,
        )
        self.assertEqual(rp.delay_ms(1), 100)
        self.assertEqual(rp.delay_ms(2), 200)
        self.assertEqual(rp.delay_ms(3), 300)

    def test_exponential_backoff_doubles(self) -> None:
        rp = RetryPolicy(
            max_attempts=5,
            base_delay_ms=100,
            max_delay_ms=10_000,
            backoff=BackoffStrategy.EXPONENTIAL,
        )
        self.assertEqual(rp.delay_ms(1), 100)
        self.assertEqual(rp.delay_ms(2), 200)
        self.assertEqual(rp.delay_ms(3), 400)
        self.assertEqual(rp.delay_ms(4), 800)

    def test_exponential_caps_at_max_delay(self) -> None:
        rp = RetryPolicy(
            max_attempts=10,
            base_delay_ms=1000,
            max_delay_ms=3500,
            backoff=BackoffStrategy.EXPONENTIAL,
        )
        # 1000, 2000, 4000→3500, 8000→3500
        self.assertEqual(rp.delay_ms(1), 1000)
        self.assertEqual(rp.delay_ms(2), 2000)
        self.assertEqual(rp.delay_ms(3), 3500)
        self.assertEqual(rp.delay_ms(9), 3500)

    def test_zero_attempts_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)

    def test_max_below_base_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=3, base_delay_ms=1000, max_delay_ms=500)


# ═══════════════════════════════════════════════════════════════════
# SLASpec
# ═══════════════════════════════════════════════════════════════════


class SLASpecTests(unittest.TestCase):
    def test_negative_latency_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SLASpec(max_latency_ms=-1)

    def test_availability_must_be_unit_interval(self) -> None:
        with self.assertRaises(ValueError):
            SLASpec(availability_target=1.5)
        with self.assertRaises(ValueError):
            SLASpec(availability_target=-0.1)
        # Boundary values are accepted.
        SLASpec(availability_target=0.0)
        SLASpec(availability_target=1.0)


# ═══════════════════════════════════════════════════════════════════
# CapabilityRequirement
# ═══════════════════════════════════════════════════════════════════


class CapabilityRequirementTests(unittest.TestCase):
    def test_requires_domain_verb_format(self) -> None:
        with self.assertRaises(ValueError):
            CapabilityRequirement("linear")
        with self.assertRaises(ValueError):
            CapabilityRequirement(":read")
        with self.assertRaises(ValueError):
            CapabilityRequirement("")

    def test_min_version_must_be_semver(self) -> None:
        with self.assertRaises(ValueError):
            CapabilityRequirement("linear:read", min_version="1.0")
        with self.assertRaises(ValueError):
            CapabilityRequirement("linear:read", min_version="latest")

    def test_valid_capability_constructs(self) -> None:
        c = CapabilityRequirement("linear:write", min_version="2.1.0")
        self.assertEqual(c.name, "linear:write")
        self.assertEqual(c.min_version, "2.1.0")


# ═══════════════════════════════════════════════════════════════════
# HandoffContract
# ═══════════════════════════════════════════════════════════════════


class HandoffContractTests(unittest.TestCase):
    def test_rejects_empty_name_or_agents(self) -> None:
        for kw in (
            {"name": "", "source_agent": "a", "target_agent": "b"},
            {"name": "x", "source_agent": "", "target_agent": "b"},
            {"name": "x", "source_agent": "a", "target_agent": ""},
        ):
            with self.assertRaises(ValueError):
                HandoffContract(**kw)  # type: ignore[arg-type]

    def test_duplicate_field_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HandoffContract(
                name="dup",
                source_agent="a",
                target_agent="b",
                input_fields=[
                    FieldSpec("x", FieldType.STRING),
                    FieldSpec("x", FieldType.INTEGER),
                ],
            )

    def test_serialisation_round_trip(self) -> None:
        c = HandoffContract(
            name="rt",
            source_agent="fred",
            target_agent="kai",
            input_fields=[FieldSpec("issue_id", FieldType.ISSUE_ID)],
            output_fields=[
                FieldSpec("ok", FieldType.BOOLEAN, required=False, default=False)
            ],
            required_capabilities=[CapabilityRequirement("linear:read")],
            sla=SLASpec(max_latency_ms=1000),
            retry_policy=RetryPolicy(max_attempts=2),
        )
        d = c.to_dict()
        self.assertEqual(d["name"], "rt")
        self.assertEqual(d["input_fields"][0]["name"], "issue_id")
        self.assertEqual(d["output_fields"][0]["default"], False)
        self.assertEqual(d["sla"]["max_latency_ms"], 1000)
        self.assertEqual(d["retry_policy"]["backoff"], "exponential")
        self.assertEqual(d["retry_policy"]["max_attempts"], 2)


# ═══════════════════════════════════════════════════════════════════
# Payload validation
# ═══════════════════════════════════════════════════════════════════


class ValidatePayloadTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        fields = [
            FieldSpec("issue_id", FieldType.ISSUE_ID),
            FieldSpec("branch", FieldType.STRING),
            FieldSpec("commits", FieldType.INTEGER),
        ]
        result = validate_payload(
            {
                "issue_id": "GRO-549",
                "branch": "ned/GRO-549",
                "commits": 3,
            },
            fields,
        )
        self.assertTrue(result.ok, msg=str(result.failures))
        self.assertEqual(result.failures, [])

    def test_missing_required_field(self) -> None:
        fields = [FieldSpec("issue_id", FieldType.ISSUE_ID)]
        result = validate_payload({"branch": "x"}, fields)
        self.assertFalse(result.ok)
        self.assertTrue(any("missing required" in f for f in result.failures))

    def test_wrong_type(self) -> None:
        fields = [FieldSpec("commits", FieldType.INTEGER)]
        result = validate_payload({"commits": "three"}, fields)
        self.assertFalse(result.ok)
        self.assertTrue(
            any("expected integer" in f for f in result.failures),
            msg=str(result.failures),
        )

    def test_bool_not_accepted_as_integer(self) -> None:
        fields = [FieldSpec("commits", FieldType.INTEGER)]
        result = validate_payload({"commits": True}, fields)
        self.assertFalse(result.ok)
        self.assertTrue(
            any("expected integer" in f for f in result.failures),
            msg="bool must not silently pass as int",
        )

    def test_unexpected_field_warns(self) -> None:
        fields = [FieldSpec("issue_id", FieldType.ISSUE_ID)]
        result = validate_payload(
            {"issue_id": "GRO-549", "sneaky": "value"}, fields
        )
        self.assertTrue(result.ok)
        self.assertTrue(any("unexpected" in w for w in result.warnings))

    def test_optional_missing_default_warns(self) -> None:
        fields = [
            FieldSpec("issue_id", FieldType.ISSUE_ID),
            FieldSpec(
                "agent_label",
                FieldType.AGENT_LABEL,
                required=False,
                default="agent:kai",
            ),
        ]
        result = validate_payload({"issue_id": "GRO-549"}, fields)
        self.assertTrue(result.ok)
        self.assertTrue(any("default" in w for w in result.warnings))

    def test_issue_id_format_enforced(self) -> None:
        fields = [FieldSpec("issue_id", FieldType.ISSUE_ID)]
        result = validate_payload({"issue_id": "gro-549"}, fields)
        self.assertFalse(result.ok)
        self.assertTrue(any("issue_id" in f for f in result.failures))

    def test_agent_label_format_enforced(self) -> None:
        fields = [FieldSpec("label", FieldType.AGENT_LABEL)]
        # Bad prefix
        self.assertFalse(
            validate_payload({"label": "AGENT:FRED"}, fields).ok
        )
        # Missing colon
        self.assertFalse(validate_payload({"label": "fred"}, fields).ok)
        # Valid
        self.assertTrue(
            validate_payload({"label": "agent:fred"}, fields).ok
        )


# ═══════════════════════════════════════════════════════════════════
# Capability validation
# ═══════════════════════════════════════════════════════════════════


class ValidateCapabilitiesTests(unittest.TestCase):
    def test_all_required_present(self) -> None:
        contract = HandoffContract(
            name="c",
            source_agent="a",
            target_agent="b",
            required_capabilities=[
                CapabilityRequirement("linear:read"),
                CapabilityRequirement("git:commit"),
            ],
        )
        result = validate_capabilities(
            contract, ["linear:read", "git:commit", "shell:exec"]
        )
        self.assertTrue(result.ok)

    def test_missing_required_capability(self) -> None:
        contract = HandoffContract(
            name="c",
            source_agent="a",
            target_agent="b",
            required_capabilities=[
                CapabilityRequirement("linear:read"),
                CapabilityRequirement("git:commit"),
            ],
        )
        result = validate_capabilities(contract, ["linear:read"])
        self.assertFalse(result.ok)
        self.assertIn("git:commit", result.failures[0])


# ═══════════════════════════════════════════════════════════════════
# Full handoff validation
# ═══════════════════════════════════════════════════════════════════


class ValidateHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = HandoffContract(
            name="happy",
            source_agent="fred",
            target_agent="kai",
            input_fields=[
                FieldSpec("issue_id", FieldType.ISSUE_ID),
                FieldSpec("subtasks", FieldType.ARRAY),
            ],
            required_capabilities=[
                CapabilityRequirement("linear:read"),
                CapabilityRequirement("git:commit"),
            ],
        )

    def test_happy_path_returns_ok_result(self) -> None:
        result = validate_handoff(
            self.contract,
            {"issue_id": "GRO-549", "subtasks": [1, 2, 3]},
            advertised_capabilities=["linear:read", "git:commit"],
        )
        self.assertTrue(result.ok, msg=str(result.failures))

    def test_aggregates_payload_and_capability_failures(self) -> None:
        result = validate_handoff(
            self.contract,
            {"subtasks": "not-a-list"},  # missing issue_id + wrong type
            advertised_capabilities=["linear:read"],  # missing git:commit
        )
        self.assertFalse(result.ok)
        # Expect at least 2 payload failures + 1 capability failure
        self.assertGreaterEqual(len(result.failures), 3)
        self.assertTrue(
            any("issue_id" in f for f in result.failures),
            msg=str(result.failures),
        )
        self.assertTrue(
            any("git:commit" in f for f in result.failures),
            msg=str(result.failures),
        )

    def test_validate_handoff_or_raise(self) -> None:
        with self.assertRaises(ValidationFailure) as cm:
            validate_handoff_or_raise(
                self.contract,
                {"issue_id": "GRO-549"},  # missing subtasks
                advertised_capabilities=["linear:read", "git:commit"],
            )
        self.assertIn("subtasks", str(cm.exception))


# ═══════════════════════════════════════════════════════════════════
# Built-in contracts
# ═══════════════════════════════════════════════════════════════════


class BuiltinContractTests(unittest.TestCase):
    def test_both_builtins_present(self) -> None:
        self.assertIn("fred-to-kai", BUILTIN_CONTRACTS)
        self.assertIn("kai-to-agy", BUILTIN_CONTRACTS)

    def test_fred_to_kai_validates_a_realistic_payload(self) -> None:
        c = BUILTIN_CONTRACTS["fred-to-kai"]
        result = validate_handoff(
            c,
            {
                "issue_id": "GRO-549",
                "agent_label": "agent:kai",
                "label_set": ["agent:kai", "pipeline:dev-agency"],
                "subtasks": [
                    {"title": "spec", "estimate": 2},
                    {"title": "tests", "estimate": 1},
                ],
            },
            advertised_capabilities=[
                "linear:read",
                "linear:write",
                "git:commit",
            ],
        )
        self.assertTrue(result.ok, msg=str(result.failures))

    def test_kai_to_agy_rejects_missing_branch(self) -> None:
        c = BUILTIN_CONTRACTS["kai-to-agy"]
        result = validate_handoff(
            c,
            {"issue_id": "GRO-549"},  # branch required, missing
            advertised_capabilities=["git:read", "shell:exec"],
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("branch" in f for f in result.failures))

    def test_get_contract_raises_for_unknown(self) -> None:
        from prismatic.core.handoff import get_contract

        with self.assertRaises(KeyError):
            get_contract("nonexistent")


if __name__ == "__main__":
    unittest.main()
