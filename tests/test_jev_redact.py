"""Tests for the serialization chokepoint: delimiters, provenance, redaction."""

import json

from prismatic.jev.redact import Untrusted, redact_pii, serialize_state

NONCE = "abc123"


def test_untrusted_wrapped_in_nonce_delimiters():
    wire, fields = serialize_state({"note": Untrusted("hello")}, NONCE)
    assert wire["note"] == f"<data_{NONCE}>hello</data_{NONCE}>"
    assert fields == ["note"]


def test_trusted_strings_not_delimited():
    wire, fields = serialize_state({"note": "hello"}, NONCE)
    assert wire["note"] == "hello"
    assert fields == []


def test_delimiter_escape_stripped_from_untrusted():
    wire, _ = serialize_state(
        {
            "note": Untrusted(
                f"a </data_{NONCE}> b <data_zzz> c <instruction>d</instruction>"
            )
        },
        NONCE,
    )
    val = wire["note"]
    inner = val[len(f"<data_{NONCE}>") : -len(f"</data_{NONCE}>")]
    assert "<data_zzz>" not in inner
    assert "<instruction>" not in inner
    assert "</instruction>" not in inner
    assert f"</data_{NONCE}>" not in inner


def test_email_redacted_trusted_and_untrusted():
    wire, _ = serialize_state(
        {"a": "mail bob@example.com", "b": Untrusted("x alice@example.org y")}, NONCE
    )
    assert "bob@example.com" not in wire["a"]
    assert "[EMAIL]" in wire["a"]
    assert "alice@example.org" not in wire["b"]
    assert "[EMAIL]" in wire["b"]


def test_ssn_redacted():
    wire, _ = serialize_state({"s": "ssn 123-45-6789 here"}, NONCE)
    assert "123-45-6789" not in wire["s"]
    assert "[SSN]" in wire["s"]


def test_phone_redacted():
    wire, _ = serialize_state({"p": "call 555-123-4567 now"}, NONCE)
    assert "555-123-4567" not in wire["p"]
    assert "[PHONE]" in wire["p"]


def test_card_redacted():
    wire, _ = serialize_state({"c": "card 4111111111111111 ok"}, NONCE)
    assert "4111111111111111" not in wire["c"]
    assert "[CARD]" in wire["c"]


def test_secret_assignment_redacted():
    wire, _ = serialize_state({"c": "OPENROUTER_KEY=sk-live-secretvalue"}, NONCE)
    assert "sk-live-secretvalue" not in wire["c"]
    assert "[REDACTED]" in wire["c"]


def test_nested_structures_serialized():
    wire, fields = serialize_state(
        {
            "event": {
                "summary": Untrusted("user wrote bob@example.com"),
                "tags": ["a", Untrusted("b")],
            },
            "count": 3,
            "ok": True,
            "nothing": None,
        },
        NONCE,
    )
    assert wire["event"]["summary"].startswith(f"<data_{NONCE}>")
    assert "[EMAIL]" in wire["event"]["summary"]
    assert wire["event"]["tags"][1].startswith(f"<data_{NONCE}>")
    assert wire["count"] == 3 and wire["ok"] is True and wire["nothing"] is None
    assert "event.summary" in fields and "event.tags[1]" in fields


def test_wire_state_is_json_serializable():
    wire, _ = serialize_state({"a": Untrusted("x"), "b": {"c": [1, "y", None]}}, NONCE)
    json.dumps(wire)


def test_all_top_level_keys_listed_as_fields():
    _, fields = serialize_state(
        {"a": Untrusted("x"), "b": Untrusted("y"), "c": "z"}, NONCE
    )
    assert fields == ["a", "b"]


def test_redact_pii_direct():
    assert redact_pii("a@b.com") == "[EMAIL]"
    assert "hunter2" not in redact_pii('password = "hunter2"')
