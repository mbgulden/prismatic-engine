#!/usr/bin/env python3
"""PR contract lint: mechanical structure check for PR bodies.

A PR is a contract: it must carry the required sections (what changed /
how it was proved / negative paths / risk & rollback / local verification
attestation). This script checks structure only — it never judges prose
quality. Exit 0 = pass, 1 = fail. Findings print one per line.

Waiver path (audited): when the PR carries the `contract-waiver` label AND
the body has a `## Waiver reason` section, the lint passes with a "waived"
note. The label alone is not enough; the reason must be in the body.

Usage:
    pr_contract_lint.py [FILE|-] [--labels "label-a,label-b"]

Reads the PR body from FILE (or stdin with `-`) and the PR's labels from
--labels (comma-separated). Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys

REQUIRED_SECTIONS = (
    "What changed",
    "How I proved it",
    "Negative paths tested",
    "Risk & rollback",
    "Local verification attestation",
)
WAIVER_LABEL = "contract-waiver"
WAIVER_SECTION = "Waiver reason"

_HEADER_RE = re.compile(r"^##(?!#)\s*(.*?)\s*$")


def _normalize(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def parse_sections(text: str) -> dict[str, str]:
    """Map normalized section titles to their raw body text.

    A section starts at a `## Title` line and runs until the next `##`
    header or end of body. `###` sub-headers do not start sections.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    chunks: list[str] = []
    for line in text.splitlines():
        match = _HEADER_RE.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(chunks).strip()
            current = _normalize(match.group(1))
            chunks = []
        elif current is not None:
            chunks.append(line)
    if current is not None:
        sections[current] = "\n".join(chunks).strip()
    return sections


def attestation_ok(body: str) -> tuple[bool, list[str]]:
    """The attestation must claim green AND reference a receipt."""
    lowered = body.lower()
    problems: list[str] = []
    if "green" not in lowered:
        problems.append(
            "attestation must claim a green verification "
            "(no 'green' found in '## Local verification attestation')"
        )
    if "receipt" not in lowered:
        problems.append(
            "attestation must reference a receipt "
            "(no 'receipt' found in '## Local verification attestation')"
        )
    return (not problems, problems)


def lint_body(
    text: str, labels: list[str] | None = None
) -> tuple[bool, list[str], bool]:
    """Lint a PR body. Returns (ok, messages, waived).

    Never raises on odd input; an empty or unparseable body simply fails
    with every required section named.
    """
    label_set = {_normalize(label) for label in (labels or [])}
    sections = parse_sections(text or "")
    messages: list[str] = []

    if _normalize(WAIVER_LABEL) in label_set:
        if _normalize(WAIVER_SECTION) in sections:
            return (
                True,
                [
                    "contract waived: contract-waiver label + waiver reason present (audited)"
                ],
                True,
            )
        messages.append(
            "contract-waiver label is present but the body has no "
            f"'## {WAIVER_SECTION}' section — the waiver reason must be in the body"
        )
        return False, messages, False

    missing = [
        title for title in REQUIRED_SECTIONS if _normalize(title) not in sections
    ]
    for title in missing:
        messages.append(f"missing required section: '## {title}'")
    if missing:
        return False, messages, False

    attestation = sections[_normalize("Local verification attestation")]
    ok, problems = attestation_ok(attestation)
    messages.extend(problems)
    if not ok:
        return False, messages, False
    return (
        True,
        [
            f"contract OK: {len(REQUIRED_SECTIONS)}/{len(REQUIRED_SECTIONS)} sections present"
        ],
        False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint a PR body against the PR contract."
    )
    parser.add_argument("body", help="PR body file path, or - for stdin")
    parser.add_argument(
        "--labels",
        default="",
        help="comma-separated PR label names (for the contract-waiver path)",
    )
    args = parser.parse_args(argv)

    if args.body == "-":
        text = sys.stdin.read()
    else:
        with open(args.body, encoding="utf-8") as handle:
            text = handle.read()
    labels = [label for label in args.labels.split(",") if label.strip()]

    ok, messages, _waived = lint_body(text, labels)
    for message in messages:
        print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
