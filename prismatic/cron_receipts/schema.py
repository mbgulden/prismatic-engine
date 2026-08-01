"""CronRunReceipt v1 dataclass, JSON Schema loading, and validation utilities.

Opaque execution identity:
    In v1, `execution_id` is an opaque string identifier.
    GRO-4345 owns trigger-identity and uniqueness-key semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.resources
import json
import re
from typing import Any, ClassVar, Mapping

import jsonschema

SCHEMA_VERSION: int = 1

VALID_TERMINAL_OUTCOMES: frozenset[str] = frozenset({
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
    "blocked",
    "missed_during_offline",
    "awaiting_operator_approval",
    "orphaned",
    "reconciled",
})

SHA256_REGEX: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")
RFC3339_TZ_REGEX: re.Pattern[str] = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def load_cron_receipt_schema() -> dict[str, Any]:
    """Load the packaged CronRunReceipt v1 JSON Schema via importlib.resources."""
    schema_file = importlib.resources.files("prismatic.cron_receipts").joinpath(
        "cron-run-receipt-v1.schema.json"
    )
    content = schema_file.read_text(encoding="utf-8")
    return json.loads(content)


def validate_receipt_dict(receipt_dict: Mapping[str, Any]) -> None:
    """Validate a dictionary representation of a CronRunReceipt against the JSON Schema."""
    schema = load_cron_receipt_schema()
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    validator.validate(receipt_dict)
    try:
        started, _ = _parse_and_validate_timestamp(
            receipt_dict["started_at"], "started_at"
        )
        finished, _ = _parse_and_validate_timestamp(
            receipt_dict["finished_at"], "finished_at"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise jsonschema.ValidationError(str(exc)) from exc
    if finished < started:
        raise jsonschema.ValidationError("finished_at cannot be earlier than started_at")


def _require_bounded_string(value: Any, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")


def _parse_and_validate_timestamp(
    val: datetime | str, name: str
) -> tuple[datetime, str]:
    if isinstance(val, datetime):
        if val.tzinfo is None or val.tzinfo.utcoffset(val) is None:
            raise ValueError(f"{name} must be a timezone-aware datetime")
        dt_utc = val.astimezone(timezone.utc)
        iso_str = dt_utc.isoformat()
        if iso_str.endswith("+00:00"):
            iso_str = iso_str[:-6] + "Z"
        return dt_utc, iso_str
    elif isinstance(val, str):
        if not RFC3339_TZ_REGEX.match(val):
            raise ValueError(
                f"{name} must be a timezone-aware RFC 3339 string, got: {val!r}"
            )
        try:
            clean_val = val.replace("Z", "+00:00") if val.endswith("Z") else val
            dt = datetime.fromisoformat(clean_val)
            if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                raise ValueError(f"{name} string parsed as naive datetime")
            dt_utc = dt.astimezone(timezone.utc)
            iso_str = dt_utc.isoformat()
            if iso_str.endswith("+00:00"):
                iso_str = iso_str[:-6] + "Z"
            return dt_utc, iso_str
        except Exception as exc:
            raise ValueError(
                f"Invalid timestamp format for {name}: {val!r}"
            ) from exc
    else:
        raise TypeError(f"{name} must be datetime or ISO string, got {type(val).__name__}")


@dataclass(frozen=True)
class CronRunReceipt:
    """Immutable CronRunReceipt v1 contract dataclass.

    Note: `execution_id` is opaque in v1. GRO-4345 owns trigger-identity and
    uniqueness-key semantics.
    """

    receipt_id: str
    cron_id: str
    execution_id: str
    outcome: str
    attempt: int
    runner_id: str
    runner_release_digest: str
    started_at: datetime | str
    finished_at: datetime | str
    signing_key_id: str
    signature: str
    schema_version: int = SCHEMA_VERSION
    error_classification: str | None = None
    evidence_digest: str | None = None

    _FIELD_NAMES: ClassVar[set[str]] = {
        "schema_version",
        "receipt_id",
        "cron_id",
        "execution_id",
        "outcome",
        "attempt",
        "runner_id",
        "runner_release_digest",
        "started_at",
        "finished_at",
        "error_classification",
        "evidence_digest",
        "signing_key_id",
        "signature",
    }

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Invalid schema_version: expected {SCHEMA_VERSION}, got {self.schema_version}"
            )

        _require_bounded_string(self.receipt_id, "receipt_id", 128)
        _require_bounded_string(self.cron_id, "cron_id", 128)
        _require_bounded_string(self.execution_id, "execution_id", 128)

        if not isinstance(self.outcome, str) or self.outcome not in VALID_TERMINAL_OUTCOMES:
            raise ValueError(f"Invalid outcome: {self.outcome!r}")

        if (
            not isinstance(self.attempt, int)
            or isinstance(self.attempt, bool)
            or self.attempt < 1
        ):
            raise ValueError(f"attempt must be integer >= 1, got {self.attempt!r}")

        _require_bounded_string(self.runner_id, "runner_id", 128)

        if (
            not isinstance(self.runner_release_digest, str)
            or not SHA256_REGEX.match(self.runner_release_digest)
        ):
            raise ValueError(
                f"runner_release_digest must be 64 lowercase hex SHA-256 digest, got {self.runner_release_digest!r}"
            )

        if self.evidence_digest is not None:
            if not isinstance(self.evidence_digest, str) or not SHA256_REGEX.match(
                self.evidence_digest
            ):
                raise ValueError(
                    f"evidence_digest must be 64 lowercase hex SHA-256 digest or None, got {self.evidence_digest!r}"
                )

        if self.error_classification is not None:
            if not isinstance(self.error_classification, str) or len(self.error_classification) > 128:
                raise ValueError(
                    "error_classification must be a string of at most 128 characters or None"
                )

        _require_bounded_string(self.signing_key_id, "signing_key_id", 128)
        _require_bounded_string(self.signature, "signature", 512)

        start_dt, start_str = _parse_and_validate_timestamp(self.started_at, "started_at")
        finish_dt, finish_str = _parse_and_validate_timestamp(self.finished_at, "finished_at")

        if finish_dt < start_dt:
            raise ValueError(
                f"finished_at ({finish_str}) cannot be earlier than started_at ({start_str})"
            )

        object.__setattr__(self, "started_at", start_str)
        object.__setattr__(self, "finished_at", finish_str)

    def to_dict(self) -> dict[str, Any]:
        """Serialize receipt to JSON-compatible dictionary matching the JSON Schema."""
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "cron_id": self.cron_id,
            "execution_id": self.execution_id,
            "outcome": self.outcome,
            "attempt": self.attempt,
            "runner_id": self.runner_id,
            "runner_release_digest": self.runner_release_digest,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_classification": self.error_classification,
            "evidence_digest": self.evidence_digest,
            "signing_key_id": self.signing_key_id,
            "signature": self.signature,
        }

    def to_json(self) -> str:
        """Serialize receipt to JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def validate(self) -> None:
        """Validate this receipt instance against the packaged JSON Schema."""
        validate_receipt_dict(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronRunReceipt:
        """Construct and validate a CronRunReceipt from a dictionary."""
        validate_receipt_dict(data)
        return cls(**data)
