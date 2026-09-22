"""Serialization-boundary safety: provenance tagging, nonce delimiters, PII redaction.

This module is the SINGLE chokepoint between caller state and the vendor HTTP
request. Everything in ``state`` passes through :func:`serialize_state`
immediately before the HTTP call — there is no bypass.

Three layered defenses:

1. **Provenance tagging.** Mark untrusted strings with :class:`Untrusted`.
   Plain strings are treated as trusted (caller-owned). Untrusted spans are
   wrapped in per-call nonce delimiters and declared as data, never
   instructions.
2. **Nonce delimiters.** ``<data_{hex}>…</data_{hex}>`` with a fresh
   ``secrets`` nonce per call. Static tags can be escaped by adversarial
   input, so delimiter-looking tags are stripped from untrusted input first.
3. **PII redaction.** Best-effort pattern redaction applied to ALL strings
   (trusted and untrusted) at this chokepoint — redacting only at the outer
   API is insufficient, because this is where text actually reaches the
   third party. The pattern list is heuristic and documented below; callers
   with stricter needs pre-redact before calling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Matches our own nonce delimiters plus common static tags, so adversarial
# input cannot close or forge a delimiter span.
_DELIMITER_TAG_RE = re.compile(
    r"</?data_[0-9a-fA-F]{1,64}>"
    r"|</?(?:user_input|untrusted|trusted|data|instruction|system|prompt)[^>]*>",
    re.IGNORECASE,
)

# Best-effort PII patterns. Heuristic by design: false positives are
# possible (e.g. a 16-digit build number); the safe direction for text
# sent to a third party is to redact.
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+"), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (
        re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"),
        "[PHONE]",
    ),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
)


# Matches "KEY = value" / "key: value" assignments for common secret names,
# including vendor-prefixed ones (OPENROUTER_KEY, JEV_API_KEY). Heuristic:
# false positives (e.g. "monkey=banana" does NOT match, but "my_key=x" does)
# err toward redaction — the safe direction for third-party-bound text.
_KEY_NAME_RE = r"(?:[\w-]*?(?:api[_-]?key|secret|token|password|passwd)|[\w-]+_key)"


def _redact_secret_assignments(text: str) -> str:
    return re.sub(
        rf"(?i)({_KEY_NAME_RE})(\s*[:=]\s*)\S+",
        r"\1\2[REDACTED]",
        text,
    )


def redact_pii(text: str) -> str:
    """Apply the best-effort PII pattern list. Heuristic; documented above."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return _redact_secret_assignments(text)


@dataclass(frozen=True)
class Untrusted:
    """Marks a state string as untrusted (user input, tool output, retrieval).

    Untrusted spans are wrapped in nonce delimiters, declared as data, and
    have delimiter-like tags stripped so they cannot break out of the span.
    """

    text: str


def _strip_delimiter_tags(text: str) -> str:
    return _DELIMITER_TAG_RE.sub("", text)


def _serialize_value(
    value: Any, nonce: str, untrusted_fields: list[str], path: str
) -> Any:
    if isinstance(value, Untrusted):
        untrusted_fields.append(path)
        clean = redact_pii(_strip_delimiter_tags(value.text))
        return f"<data_{nonce}>{clean}</data_{nonce}>"
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        return {
            str(k): _serialize_value(v, nonce, untrusted_fields, f"{path}.{k}")
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _serialize_value(v, nonce, untrusted_fields, f"{path}[{i}]")
            for i, v in enumerate(value)
        ]
    return value  # numbers, bools, None cannot carry injections


def serialize_state(
    state: dict[str, Any], nonce: str
) -> tuple[dict[str, Any], list[str]]:
    """Serialize state for the wire: delimit untrusted spans, redact PII.

    Returns ``(wire_state, untrusted_field_paths)``. The wire state is
    JSON-serializable. Nonce delimiters make each untrusted span
    tamper-evident for this call only.
    """
    wire: dict[str, Any] = {}
    untrusted_fields: list[str] = []
    for key, value in state.items():
        wire[str(key)] = _serialize_value(value, nonce, untrusted_fields, str(key))
    return wire, untrusted_fields
