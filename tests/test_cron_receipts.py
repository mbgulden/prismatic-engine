"""Focused tests for CronRunReceipt v1 base schema contract.

Proves:
1. Valid dataclass serializes and validates against Draft 2020-12 schema.
2. Every terminal outcome is accepted.
3. Invalid schema version, unknown outcome, bad digest, attempt < 1, naive timestamp,
   reverse timestamp interval, missing required property, and unknown property are rejected.
4. Dataclass serialization and schema required/property sets match without silent drift.
5. Schema resource is present when loaded via importlib.resources.
6. Execution is pure and deterministic with no I/O, signing, persistence, HTTP, journal, or Linear side effects.
"""

from __future__ import annotations

import builtins
from dataclasses import fields
from datetime import datetime, timedelta, timezone
import importlib.resources
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import pytest
import jsonschema

from prismatic.cron_receipts import (
    SCHEMA_VERSION,
    VALID_TERMINAL_OUTCOMES,
    CronRunReceipt,
    load_cron_receipt_schema,
    validate_receipt_dict,
)


@pytest.fixture
def valid_receipt_kwargs() -> dict:
    """Return a dictionary of valid keyword arguments for constructing a CronRunReceipt."""
    return {
        "schema_version": 1,
        "receipt_id": "rcpt-20260727-001",
        "cron_id": "cron-daily-cleanup",
        "execution_id": "exec-abc123xyz",
        "outcome": "succeeded",
        "attempt": 1,
        "runner_id": "runner-node-01",
        "runner_release_digest": "a" * 64,
        "started_at": "2026-07-27T10:00:00Z",
        "finished_at": "2026-07-27T10:05:00Z",
        "error_classification": None,
        "evidence_digest": "b" * 64,
        "signing_key_id": "key-2026-01",
        "signature": "sig-test-signature-value-123",
    }


def test_valid_dataclass_serializes_and_validates_schema(valid_receipt_kwargs):
    """Test 1: A valid dataclass serializes and validates against Draft 2020-12 schema."""
    receipt = CronRunReceipt(**valid_receipt_kwargs)

    # Method validations
    receipt.validate()
    d = receipt.to_dict()
    assert d["schema_version"] == 1
    assert d["receipt_id"] == "rcpt-20260727-001"
    assert d["outcome"] == "succeeded"
    assert d["started_at"] == "2026-07-27T10:00:00Z"
    assert d["finished_at"] == "2026-07-27T10:05:00Z"

    # Verify JSON string serialization
    json_str = receipt.to_json()
    parsed = json.loads(json_str)
    validate_receipt_dict(parsed)

    # Verify round-trip via from_dict
    reconstructed = CronRunReceipt.from_dict(d)
    assert reconstructed == receipt


def test_datetime_object_support(valid_receipt_kwargs):
    """Verify timezone-aware datetime objects are accepted and formatted as UTC RFC 3339."""
    dt_start = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)
    dt_finish = datetime(2026, 7, 27, 10, 5, 0, tzinfo=timezone.utc)

    kwargs = dict(valid_receipt_kwargs)
    kwargs["started_at"] = dt_start
    kwargs["finished_at"] = dt_finish

    receipt = CronRunReceipt(**kwargs)
    assert receipt.started_at == "2026-07-27T10:00:00Z"
    assert receipt.finished_at == "2026-07-27T10:05:00Z"
    receipt.validate()

    offset_kwargs = dict(valid_receipt_kwargs)
    offset_kwargs["started_at"] = datetime(
        2026, 7, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=2))
    )
    offset_kwargs["finished_at"] = "2026-07-27T12:05:00+02:00"
    offset_receipt = CronRunReceipt(**offset_kwargs)
    assert offset_receipt.started_at == "2026-07-27T10:00:00Z"
    assert offset_receipt.finished_at == "2026-07-27T10:05:00Z"
    offset_receipt.validate()


def test_every_terminal_outcome_accepted(valid_receipt_kwargs):
    """Test 2: Every required terminal outcome is accepted."""
    expected_outcomes = {
        "succeeded",
        "failed",
        "timed_out",
        "cancelled",
        "blocked",
        "missed_during_offline",
        "awaiting_operator_approval",
        "orphaned",
        "reconciled",
    }
    assert VALID_TERMINAL_OUTCOMES == expected_outcomes
    assert isinstance(VALID_TERMINAL_OUTCOMES, frozenset)
    with pytest.raises(AttributeError):
        VALID_TERMINAL_OUTCOMES.add("injected_not_in_schema")  # type: ignore[attr-defined]

    for outcome in VALID_TERMINAL_OUTCOMES:
        kwargs = dict(valid_receipt_kwargs)
        kwargs["outcome"] = outcome
        receipt = CronRunReceipt(**kwargs)
        receipt.validate()
        assert receipt.to_dict()["outcome"] == outcome


def test_rejections_and_negative_cases(valid_receipt_kwargs):
    """Test 3: Invalid schema version, unknown outcome, bad digest, attempt < 1,

    naive timestamp, reverse timestamp, missing property, and unknown property are rejected.
    """
    # 3a. Invalid schema version
    kwargs = dict(valid_receipt_kwargs, schema_version=2)
    with pytest.raises(ValueError, match="schema_version"):
        CronRunReceipt(**kwargs)
    kwargs = dict(valid_receipt_kwargs)
    kwargs["schema_version"] = True
    with pytest.raises(ValueError, match="schema_version"):
        CronRunReceipt(**kwargs)

    # 3b. Unknown outcome
    kwargs = dict(valid_receipt_kwargs, outcome="unknown_state")
    with pytest.raises(ValueError, match="Invalid outcome"):
        CronRunReceipt(**kwargs)

    # 3c. Bad digest (runner_release_digest not 64 hex)
    kwargs = dict(valid_receipt_kwargs, runner_release_digest="not-a-sha256")
    with pytest.raises(ValueError, match="runner_release_digest"):
        CronRunReceipt(**kwargs)

    # 3d. Bad evidence digest
    kwargs = dict(valid_receipt_kwargs, evidence_digest="SHORT123")
    with pytest.raises(ValueError, match="evidence_digest"):
        CronRunReceipt(**kwargs)

    # 3e. Attempt below 1
    kwargs = dict(valid_receipt_kwargs, attempt=0)
    with pytest.raises(ValueError, match="attempt"):
        CronRunReceipt(**kwargs)
    kwargs = dict(valid_receipt_kwargs, attempt=-1)
    with pytest.raises(ValueError, match="attempt"):
        CronRunReceipt(**kwargs)

    # 3f. Naive timestamp (datetime object)
    kwargs = dict(valid_receipt_kwargs, started_at=datetime(2026, 7, 27, 10, 0, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        CronRunReceipt(**kwargs)

    # 3g. Naive timestamp (ISO string without TZ offset)
    kwargs = dict(valid_receipt_kwargs, started_at="2026-07-27T10:00:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        CronRunReceipt(**kwargs)

    # 3h. Reverse timestamp interval (finished_at earlier than started_at)
    kwargs = dict(
        valid_receipt_kwargs,
        started_at="2026-07-27T10:05:00Z",
        finished_at="2026-07-27T10:00:00Z",
    )
    with pytest.raises(ValueError, match="cannot be earlier than started_at"):
        CronRunReceipt(**kwargs)

    # 3i. Missing required property in JSON Schema validation
    d_missing = dict(valid_receipt_kwargs)
    del d_missing["signature"]
    with pytest.raises(jsonschema.ValidationError):
        validate_receipt_dict(d_missing)

    # 3j. Unknown / extra property rejected by JSON Schema (additionalProperties: false)
    d_extra = dict(valid_receipt_kwargs, extra_field="unauthorized_data")
    with pytest.raises(jsonschema.ValidationError):
        validate_receipt_dict(d_extra)

    # 3k. Dataclass bounds match schema bounds; raw timestamps are real dates.
    oversized = dict(valid_receipt_kwargs)
    oversized["receipt_id"] = "x" * 129
    with pytest.raises(ValueError, match="receipt_id"):
        CronRunReceipt(**oversized)
    oversized = dict(valid_receipt_kwargs)
    oversized["error_classification"] = "x" * 129
    with pytest.raises(ValueError, match="error_classification"):
        CronRunReceipt(**oversized)
    invalid_date = dict(valid_receipt_kwargs, started_at="2026-99-99T10:00:00Z")
    with pytest.raises(jsonschema.ValidationError):
        validate_receipt_dict(invalid_date)


def test_schema_and_dataclass_no_drift(valid_receipt_kwargs):
    """Test 4: Dataclass serialization keys and schema required/properties sets match exactly."""
    schema = load_cron_receipt_schema()
    schema_properties = set(schema["properties"].keys())
    schema_required = set(schema["required"])

    # Schema internal consistency
    assert schema_required == schema_properties

    # Dataclass fields match schema properties
    dataclass_fields = {f.name for f in fields(CronRunReceipt)}
    assert dataclass_fields == schema_properties

    # Serialized dict keys match schema properties
    receipt = CronRunReceipt(**valid_receipt_kwargs)
    serialized_keys = set(receipt.to_dict().keys())
    assert serialized_keys == schema_properties


def test_schema_resource_loading_via_importlib():
    """Test 5: The schema resource is present and loaded via importlib.resources."""
    schema_file = importlib.resources.files("prismatic.cron_receipts").joinpath(
        "cron-run-receipt-v1.schema.json"
    )
    assert schema_file.is_file()

    schema = load_cron_receipt_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == 1


def test_construction_and_serialization_have_no_external_side_effects(
    valid_receipt_kwargs, monkeypatch
):
    """Test 6: Construction and serialization do not perform external side effects."""
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external side effect")

    environment_before = dict(os.environ)
    cwd_before = Path.cwd()
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(time, "time", forbidden)

    receipt = CronRunReceipt(**valid_receipt_kwargs)
    serialized = receipt.to_dict()
    assert json.loads(receipt.to_json()) == serialized
    assert dict(os.environ) == environment_before
    assert Path.cwd() == cwd_before
    assert "opaque" in CronRunReceipt.__doc__.lower()
