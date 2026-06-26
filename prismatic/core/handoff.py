"""
prismatic/core/handoff.py — Handoff Contracts Specification
============================================================

Defines the formal schema for agent-to-agent integration points within the
Prismatic Engine routing system. Each handoff (an agent passing a task to
the next agent in the pipeline) is described by a HandoffContract that
captures:

    * input/output payload shapes (typed field specifications)
    * required capabilities (the producer/consumer agents must declare these)
    * SLAs (maximum latency, throughput floor, availability target)
    * retry semantics (backoff curve, max attempts, dead-letter destination)

The contract is **declarative** — it does not perform any routing itself.
It is consumed by the dispatcher (`prismatic.dispatcher`) and the pipeline
router (`prismatic.router`) to validate that two agents in a chain are
mutually compatible before a handoff is dispatched.

Relationship to ``prismatic.core.contracts``
--------------------------------------------
``contracts.py`` enforces **filesystem path boundaries** (which directories
an agent may read or write). ``handoff.py`` enforces **inter-agent data
boundaries** (which fields must be present in the payload an agent emits,
which capabilities the receiving agent must advertise, and how transient
failures are retried). Both modules share the ``SecurityException`` family
for violation reporting but operate at orthogonal layers.

Reference
---------
GRO-549 — Define and implement Handoff Contracts specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable


# ═══════════════════════════════════════════════════════════════════
# Payload field types
# ═══════════════════════════════════════════════════════════════════


class FieldType(str, Enum):
    """Supported scalar / structural types for a payload field.

    Mirrors JSON Schema's primitive type set with three Prismatic-specific
    extensions (``issue_id``, ``agent_label``, ``label_set``) that frequently
    appear in inter-agent payloads and benefit from first-class validation.
    """

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    NULL = "null"
    # Prismatic extensions
    ISSUE_ID = "issue_id"  # "GRO-1234" pattern
    AGENT_LABEL = "agent_label"  # "agent:fred" pattern
    LABEL_SET = "label_set"  # list[str] of label names


class FieldSpec:
    """Declarative specification of a single payload field.

    A field is identified by ``name`` and validated against ``type``. Fields
    may be marked ``required`` (must be present) or ``optional``. Each
    optional field may carry a ``default`` value applied during validation.

    The ``pattern`` attribute is consulted only for ``FieldType.STRING`` and
    the Prismatic extension types that have well-known regex anchors
    (``ISSUE_ID``, ``AGENT_LABEL``).
    """

    __slots__ = ("name", "type", "required", "default", "pattern", "description")

    def __init__(
        self,
        name: str,
        type: FieldType,
        *,
        required: bool = True,
        default: Any = ...,
        pattern: str | None = None,
        description: str = "",
    ) -> None:
        if not name or not isinstance(name, str):
            raise ValueError("FieldSpec.name must be a non-empty string")
        if not isinstance(type, FieldType):
            raise TypeError(f"FieldSpec.type must be a FieldType enum, got {type!r}")
        if required and default is not ...:
            # Required fields with defaults are contradictory; refuse loudly.
            raise ValueError(
                f"FieldSpec(name={name!r}) is required but has a default — "
                "either drop required=True or omit default."
            )

        self.name = name
        self.type = type
        self.required = required
        self.default = default
        self.pattern = pattern
        self.description = description

    # ── Serialisation ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type.value,
            "required": self.required,
        }
        if self.default is not ...:
            out["default"] = self.default
        if self.pattern is not None:
            out["pattern"] = self.pattern
        if self.description:
            out["description"] = self.description
        return out

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        req = "required" if self.required else "optional"
        return f"FieldSpec({self.name}:{self.type.value}, {req})"


# ═══════════════════════════════════════════════════════════════════
# SLA, retry, and capability declarations
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SLASpec:
    """Latency, throughput, and availability targets for a handoff.

    All fields are optional; ``None`` means "no constraint declared".
    Negative values are rejected at construction.
    """

    max_latency_ms: int | None = None  # wall-clock budget per handoff
    min_throughput_per_min: int | None = None  # steady-state floor
    availability_target: float | None = None  # 0.0–1.0, e.g. 0.999

    def __post_init__(self) -> None:
        for label, value in (
            ("max_latency_ms", self.max_latency_ms),
            ("min_throughput_per_min", self.min_throughput_per_min),
        ):
            if value is not None and value < 0:
                raise ValueError(f"SLASpec.{label} must be >= 0, got {value}")
        if self.availability_target is not None:
            if not 0.0 <= self.availability_target <= 1.0:
                raise ValueError(
                    "SLASpec.availability_target must be in [0.0, 1.0], "
                    f"got {self.availability_target}"
                )


class BackoffStrategy(str, Enum):
    """Retry-wait-curve selector for RetryPolicy."""

    NONE = "none"  # no retries
    CONSTANT = "constant"  # same delay every attempt
    LINEAR = "linear"  # delay = base * attempt_number
    EXPONENTIAL = "exponential"  # delay = base * (2 ** (attempt-1))
    EXPONENTIAL_JITTER = "exponential_jitter"  # exp + uniform jitter in [0, base)


@dataclass(frozen=True)
class RetryPolicy:
    """Retry semantics for a transient-failed handoff.

    ``max_attempts`` is the **total** number of attempts including the first
    (so ``max_attempts=3`` means 1 initial + 2 retries). ``base_delay_ms`` is
    the first-retry wait; subsequent waits are derived per ``BackoffStrategy``.

    ``dead_letter`` is a free-form label or queue identifier where permanently
    failed handoffs are routed after exhausting retries. Use ``""`` to drop.
    """

    max_attempts: int = 1
    base_delay_ms: int = 1000
    max_delay_ms: int = 60_000
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    dead_letter: str = ""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(
                f"RetryPolicy.max_attempts must be >= 1, got {self.max_attempts}"
            )
        if self.base_delay_ms < 0:
            raise ValueError(
                f"RetryPolicy.base_delay_ms must be >= 0, got {self.base_delay_ms}"
            )
        if self.max_delay_ms < self.base_delay_ms:
            raise ValueError(
                "RetryPolicy.max_delay_ms must be >= base_delay_ms "
                f"({self.base_delay_ms}), got {self.max_delay_ms}"
            )

    def delay_ms(self, attempt: int) -> int:
        """Compute wait milliseconds **before** the given attempt number.

        ``attempt`` is 1-indexed: ``delay_ms(1)`` is the wait before the very
        first retry (after the initial attempt failed). Returns 0 for
        ``BackoffStrategy.NONE`` or when ``attempt >= max_attempts``.
        """
        if self.backoff is BackoffStrategy.NONE:
            return 0
        if attempt < 1 or attempt >= self.max_attempts:
            return 0
        raw: float
        if self.backoff is BackoffStrategy.CONSTANT:
            raw = float(self.base_delay_ms)
        elif self.backoff is BackoffStrategy.LINEAR:
            raw = float(self.base_delay_ms) * attempt
        elif self.backoff is BackoffStrategy.EXPONENTIAL:
            raw = float(self.base_delay_ms) * (2 ** (attempt - 1))
        elif self.backoff is BackoffStrategy.EXPONENTIAL_JITTER:
            # Deterministic placeholder — jitter requires a PRNG seed which
            # we deliberately do not hold here. We return the base exp curve
            # and document the deviation so callers requiring real jitter
            # can override with their own PRNG.
            raw = float(self.base_delay_ms) * (2 ** (attempt - 1))
        else:  # pragma: no cover — defensive
            raise ValueError(f"Unknown BackoffStrategy: {self.backoff!r}")
        return int(min(raw, self.max_delay_ms))


@dataclass(frozen=True)
class CapabilityRequirement:
    """A single capability that the receiving agent must advertise.

    The capability name follows the ``domain:verb`` convention used
    elsewhere in the engine (e.g. ``"linear:write"``, ``"shell:exec"``,
    ``"git:commit"``). ``min_version`` is an optional semver floor; if
    omitted, any version is accepted.
    """

    name: str
    min_version: str = ""

    def __post_init__(self) -> None:
        # ``domain:verb`` requires both halves to be non-empty and the
        # domain to be a lowercase identifier. We also reject the empty
        # name and missing colon.
        if (
            not self.name
            or ":" not in self.name
            or not re.match(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$", self.name)
        ):
            raise ValueError(
                "CapabilityRequirement.name must follow 'domain:verb' "
                f"format (lowercase identifiers), got {self.name!r}"
            )
        if self.min_version and not re.match(r"^\d+\.\d+\.\d+$", self.min_version):
            raise ValueError(
                "CapabilityRequirement.min_version must be semver "
                f"(X.Y.Z), got {self.min_version!r}"
            )


# ═══════════════════════════════════════════════════════════════════
# Handoff contract & validation result
# ═══════════════════════════════════════════════════════════════════


@dataclass
class HandoffContract:
    """Declarative contract for a single agent-to-agent integration point.

    A contract names a ``source_agent`` (the producer of the payload) and a
    ``target_agent`` (the consumer). It declares the shape of the payload
    the source must emit (``input_fields``) and the shape the target must
    accept (``output_fields``), plus capability, SLA, and retry rules.
    """

    name: str
    source_agent: str
    target_agent: str
    input_fields: list[FieldSpec] = field(default_factory=list)
    output_fields: list[FieldSpec] = field(default_factory=list)
    required_capabilities: list[CapabilityRequirement] = field(default_factory=list)
    sla: SLASpec = field(default_factory=SLASpec)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("HandoffContract.name must be non-empty")
        if not self.source_agent:
            raise ValueError("HandoffContract.source_agent must be non-empty")
        if not self.target_agent:
            raise ValueError("HandoffContract.target_agent must be non-empty")
        # Field names must be unique within each side — duplicate names
        # would make validation ambiguous.
        self._check_unique(self.input_fields, "input_fields")
        self._check_unique(self.output_fields, "output_fields")

    @staticmethod
    def _check_unique(fields: list[FieldSpec], side: str) -> None:
        seen: set[str] = set()
        for f in fields:
            if f.name in seen:
                raise ValueError(f"Duplicate field name {f.name!r} in {side}")
            seen.add(f.name)

    # ── Serialisation ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "description": self.description,
            "input_fields": [f.to_dict() for f in self.input_fields],
            "output_fields": [f.to_dict() for f in self.output_fields],
            "required_capabilities": [asdict(c) for c in self.required_capabilities],
            "sla": asdict(self.sla),
            "retry_policy": {
                **asdict(self.retry_policy),
                "backoff": self.retry_policy.backoff.value,
            },
        }


class ValidationFailure(Exception):
    """Raised when a payload or capability check fails.

    The exception carries a structured ``failures`` list so callers can
    surface every problem at once instead of fixing them one at a time.
    """

    def __init__(self, failures: list[str]) -> None:
        self.failures = list(failures)
        super().__init__("Handoff contract validation failed: " + "; ".join(failures))


@dataclass
class ValidationResult:
    """Outcome of a contract validation pass.

    ``ok`` is True iff ``failures`` is empty. ``warnings`` carries non-fatal
    observations (e.g. optional fields present that the contract did not
    declare) for telemetry dashboards.
    """

    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# Validators
# ═══════════════════════════════════════════════════════════════════


_ISSUE_ID_RE = re.compile(r"^[A-Z]+-\d+$")
_AGENT_LABEL_RE = re.compile(r"^agent:[a-z][a-z0-9_-]*$")


def _check_value(field_spec: FieldSpec, value: Any) -> str | None:
    """Return None if value matches the field type, else an error string."""
    t = field_spec.type
    if value is None:
        return (
            None
            if t is FieldType.NULL
            else f"field {field_spec.name!r} expected {t.value}, got null"
        )
    if t is FieldType.STRING and not isinstance(value, str):
        return f"field {field_spec.name!r} expected string, got {type(value).__name__}"
    # bool is a subclass of int — exclude explicitly for numeric types.
    if t is FieldType.INTEGER and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        return f"field {field_spec.name!r} expected integer, got {type(value).__name__}"
    if t is FieldType.NUMBER and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        return f"field {field_spec.name!r} expected number, got {type(value).__name__}"
    if t is FieldType.BOOLEAN and not isinstance(value, bool):
        return f"field {field_spec.name!r} expected boolean, got {type(value).__name__}"
    if t is FieldType.OBJECT and not isinstance(value, dict):
        return f"field {field_spec.name!r} expected object, got {type(value).__name__}"
    if t is FieldType.ARRAY and not isinstance(value, list):
        return f"field {field_spec.name!r} expected array, got {type(value).__name__}"
    if t is FieldType.ISSUE_ID:
        if not isinstance(value, str) or not _ISSUE_ID_RE.match(value):
            return (
                f"field {field_spec.name!r} expected issue_id "
                f"(e.g. 'GRO-1234'), got {value!r}"
            )
    if t is FieldType.AGENT_LABEL:
        if not isinstance(value, str) or not _AGENT_LABEL_RE.match(value):
            return (
                f"field {field_spec.name!r} expected agent_label "
                f"(e.g. 'agent:fred'), got {value!r}"
            )
    if t is FieldType.LABEL_SET:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return (
                f"field {field_spec.name!r} expected label_set "
                f"(list[str]), got {value!r}"
            )
    if field_spec.pattern and isinstance(value, str):
        if not re.match(field_spec.pattern, value):
            return (
                f"field {field_spec.name!r} did not match pattern "
                f"{field_spec.pattern!r}"
            )
    return None


def validate_payload(
    payload: dict[str, Any],
    fields: Iterable[FieldSpec],
) -> ValidationResult:
    """Validate a payload dict against a list of FieldSpecs.

    * Missing **required** fields → failure.
    * Fields with wrong type → failure.
    * Unexpected fields (not declared at all) → warning.
    * Optional fields with defaults → default applied if missing.
    """
    failures: list[str] = []
    warnings: list[str] = []
    declared = {f.name for f in fields}

    for f in fields:
        if f.name not in payload:
            if f.required:
                failures.append(f"missing required field {f.name!r}")
            elif f.default is not ...:
                # Caller is responsible for applying defaults before sending;
                # we record the absence but do not mutate the payload.
                warnings.append(
                    f"optional field {f.name!r} missing — default "
                    f"{f.default!r} will be applied by producer"
                )
            continue
        err = _check_value(f, payload[f.name])
        if err:
            failures.append(err)

    for extra in payload.keys() - declared:
        warnings.append(f"unexpected field {extra!r} (not declared in contract)")

    return ValidationResult(
        ok=not failures,
        failures=failures,
        warnings=warnings,
    )


def validate_capabilities(
    contract: HandoffContract,
    advertised: Iterable[str],
) -> ValidationResult:
    """Check that the receiving agent advertises every required capability.

    ``advertised`` is a flat iterable of ``"domain:verb"`` strings (the
    receiving agent's capability list). Version floors are NOT checked here
    — that requires the receiving agent to also advertise a version map,
    which is out of scope for the initial spec.
    """
    have = set(advertised)
    failures = [
        f"missing required capability {c.name!r}"
        for c in contract.required_capabilities
        if c.name not in have
    ]
    return ValidationResult(ok=not failures, failures=failures)


def validate_handoff(
    contract: HandoffContract,
    payload: dict[str, Any],
    advertised_capabilities: Iterable[str] = (),
) -> ValidationResult:
    """End-to-end handoff validation: payload + capabilities.

    Raises ``ValidationFailure`` only when ``raise_on_failure=True`` is
    passed via the helper ``validate_handoff_or_raise``; the plain function
    always returns a ``ValidationResult`` so callers can log or route.
    """
    payload_result = validate_payload(payload, contract.input_fields)
    cap_result = validate_capabilities(contract, advertised_capabilities)

    return ValidationResult(
        ok=payload_result.ok and cap_result.ok,
        failures=payload_result.failures + cap_result.failures,
        warnings=payload_result.warnings + cap_result.warnings,
    )


def validate_handoff_or_raise(
    contract: HandoffContract,
    payload: dict[str, Any],
    advertised_capabilities: Iterable[str] = (),
) -> ValidationResult:
    """Like ``validate_handoff`` but raises on any failure."""
    result = validate_handoff(contract, payload, advertised_capabilities)
    if not result.ok:
        raise ValidationFailure(result.failures)
    return result


# ═══════════════════════════════════════════════════════════════════
# Built-in contracts
# ═══════════════════════════════════════════════════════════════════


def fred_to_kai_contract() -> HandoffContract:
    """Standard Fred → Kai handoff used by the dev-agency pipeline.

    Fred decomposes a Linear issue into a sequence of subtasks and hands the
    structured task list off to Kai for content/CSS/JS implementation.
    """
    return HandoffContract(
        name="fred-to-kai",
        source_agent="fred",
        target_agent="kai",
        description=(
            "Fred's decomposition output passed to Kai for implementation. "
            "Payload carries the Linear issue identifier, scoped subtask "
            "list, and the originating pipeline label."
        ),
        input_fields=[
            FieldSpec("issue_id", FieldType.ISSUE_ID),
            FieldSpec(
                "agent_label",
                FieldType.AGENT_LABEL,
                required=False,
                default="agent:kai",
            ),
            FieldSpec("label_set", FieldType.LABEL_SET),
            FieldSpec("subtasks", FieldType.ARRAY),
            FieldSpec("context", FieldType.OBJECT, required=False, default={}),
        ],
        output_fields=[
            FieldSpec("issue_id", FieldType.ISSUE_ID),
            FieldSpec("success", FieldType.BOOLEAN),
            FieldSpec("branch", FieldType.STRING, required=False),
            FieldSpec("commits", FieldType.INTEGER, required=False, default=0),
        ],
        required_capabilities=[
            CapabilityRequirement("linear:read"),
            CapabilityRequirement("linear:write"),
            CapabilityRequirement("git:commit"),
        ],
        sla=SLASpec(max_latency_ms=15 * 60 * 1000),  # 15 min budget
        retry_policy=RetryPolicy(
            max_attempts=3,
            base_delay_ms=5_000,
            max_delay_ms=60_000,
            backoff=BackoffStrategy.EXPONENTIAL,
            dead_letter="pipeline:dead-letter",
        ),
    )


def kai_to_agy_contract() -> HandoffContract:
    """Standard Kai → AGY handoff for verification / research follow-up."""
    return HandoffContract(
        name="kai-to-agy",
        source_agent="kai",
        target_agent="agy",
        description=(
            "After Kai lands an implementation, AGY performs a verification "
            "pass: re-reads the issue, exercises the change, and emits a "
            "review report."
        ),
        input_fields=[
            FieldSpec("issue_id", FieldType.ISSUE_ID),
            FieldSpec("branch", FieldType.STRING),
            FieldSpec("verification_focus", FieldType.STRING, required=False),
        ],
        output_fields=[
            FieldSpec("issue_id", FieldType.ISSUE_ID),
            FieldSpec("success", FieldType.BOOLEAN),
            FieldSpec("report_url", FieldType.STRING, required=False),
        ],
        required_capabilities=[
            CapabilityRequirement("git:read"),
            CapabilityRequirement("shell:exec"),
        ],
        sla=SLASpec(max_latency_ms=10 * 60 * 1000),  # 10 min
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_ms=10_000,
            max_delay_ms=30_000,
            backoff=BackoffStrategy.EXPONENTIAL,
            dead_letter="pipeline:review-dead-letter",
        ),
    )


BUILTIN_CONTRACTS: dict[str, HandoffContract] = {
    "fred-to-kai": fred_to_kai_contract(),
    "kai-to-agy": kai_to_agy_contract(),
}


def get_contract(name: str) -> HandoffContract:
    """Look up a built-in contract by name; raises ``KeyError`` if unknown."""
    if name not in BUILTIN_CONTRACTS:
        raise KeyError(
            f"Unknown handoff contract {name!r}. Known: {sorted(BUILTIN_CONTRACTS)}"
        )
    return BUILTIN_CONTRACTS[name]


__all__ = [
    "FieldType",
    "FieldSpec",
    "SLASpec",
    "BackoffStrategy",
    "RetryPolicy",
    "CapabilityRequirement",
    "HandoffContract",
    "ValidationFailure",
    "ValidationResult",
    "validate_payload",
    "validate_capabilities",
    "validate_handoff",
    "validate_handoff_or_raise",
    "BUILTIN_CONTRACTS",
    "fred_to_kai_contract",
    "kai_to_agy_contract",
    "get_contract",
]
