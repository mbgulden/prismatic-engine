"""Tests for wire schema versioning: per-type versions, response checks."""

import pytest

from prismatic.jev import DecisionError
from prismatic.jev.schema import (
    SCHEMA_VERSION,
    check_response_schema_version,
    wire_version_for,
)
from prismatic.jev.questions import Choice, Noul, Score


def test_wire_versions_are_stable():
    assert wire_version_for("noul") == "noul.v1"
    assert wire_version_for("choice") == "choice.v1"
    assert wire_version_for("score") == "score.v1"
    assert SCHEMA_VERSION == "1.0"


def test_unknown_kind_raises():
    with pytest.raises(KeyError):
        wire_version_for("essay")


def test_questions_carry_wire_version():
    assert Noul("u", "prompt").to_wire()["wire_version"] == "noul.v1"
    assert (
        Choice("v", "prompt", options=["A", "B"]).to_wire()["wire_version"]
        == "choice.v1"
    )
    assert Score("r", "prompt").to_wire()["wire_version"] == "score.v1"


def test_response_version_ok():
    check_response_schema_version({"schema_version": "1.0", "answers": {}})
    check_response_schema_version({"answers": {}})  # absent = tolerated
    check_response_schema_version({})


def test_response_unknown_version_fails_closed():
    with pytest.raises(DecisionError, match="unsupported response schema_version"):
        check_response_schema_version({"schema_version": "2.0"})
    with pytest.raises(DecisionError, match="unsupported response schema_version"):
        check_response_schema_version({"schema_version": "0.9"})


def test_response_non_dict_does_not_raise():
    # check_response_schema_version only inspects dicts; malformed JSON
    # shapes are rejected by the response parser, not here.
    check_response_schema_version([1, 2, 3])
    check_response_schema_version(None)
