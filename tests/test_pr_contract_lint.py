"""WS3 tests: the PR contract lint (structure only, never quality)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pr_contract_lint.py"
_spec = importlib.util.spec_from_file_location("pr_contract_lint", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

lint_body = _mod.lint_body
REQUIRED_SECTIONS = _mod.REQUIRED_SECTIONS

GOOD_BODY = """\
## What changed

Added the PR contract lint and template.

## How I proved it

- pytest tests/test_pr_contract_lint.py -q: 14 passed
- ruff check + ruff format --check clean (0.15.20)
- receipt: lint-run-2026-09-25-001

## Negative paths tested

- Body with a section removed fails naming the section.
- Empty body fails with all sections named.
- Waiver label without a waiver-reason section fails.

## Risk & rollback

Risk: lint blocks a legitimate PR. Mitigation: structure-only checks plus the
contract-waiver escape hatch. Rollback: revert the merge; the lint only runs
on new PR activity.

## Local verification attestation

Local verification: green, receipt lint-run-2026-09-25-001
"""


def _without(body: str, section: str) -> str:
    """Remove one ## section (header + body) from a contract body."""
    lines = body.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            skipping = line.strip() == f"## {section}"
            if not skipping:
                out.append(line)
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)


def test_complete_contract_passes():
    ok, messages, waived = lint_body(GOOD_BODY, [])
    assert ok and not waived
    assert any("contract OK" in message for message in messages)


def test_each_missing_section_fails():
    for section in REQUIRED_SECTIONS:
        body = _without(GOOD_BODY, section)
        ok, messages, _waived = lint_body(body, [])
        assert not ok, f"expected failure without {section!r}"
        assert any(section in message for message in messages), (
            f"failure must name the missing section {section!r}: {messages}"
        )


def test_empty_body_fails_naming_every_section():
    ok, messages, _waived = lint_body("", [])
    assert not ok
    for section in REQUIRED_SECTIONS:
        assert any(section in message for message in messages)


def test_attestation_without_green_fails():
    body = GOOD_BODY.replace("green, receipt", "receipt")
    ok, messages, _waived = lint_body(body, [])
    assert not ok
    assert any("green" in message for message in messages)


def test_attestation_without_receipt_fails():
    body = GOOD_BODY.replace("receipt lint-run-2026-09-25-001", "")
    ok, messages, _waived = lint_body(body, [])
    assert not ok
    assert any("receipt" in message for message in messages)


def test_section_titles_are_case_insensitive():
    body = GOOD_BODY.replace("## What changed", "## WHAT CHANGED")
    ok, _messages, _waived = lint_body(body, [])
    assert ok


def test_waiver_label_plus_reason_passes_as_waived():
    body = "## Waiver reason\n\nDocs-only typo fix; no behavior change.\n"
    ok, messages, waived = lint_body(body, ["contract-waiver"])
    assert ok and waived
    assert any("waived" in message for message in messages)


def test_waiver_label_without_reason_fails():
    ok, messages, waived = lint_body(GOOD_BODY, ["contract-waiver"])
    assert not ok and not waived
    assert any("Waiver reason" in message for message in messages)


def test_waiver_reason_without_label_does_not_waive():
    body = _without(GOOD_BODY, "What changed") + "\n## Waiver reason\n\nNope.\n"
    ok, _messages, waived = lint_body(body, [])
    assert not ok and not waived


def test_cli_stdin_good_body_exits_zero():
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "-"],
        input=GOOD_BODY,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_cli_stdin_bad_body_exits_one_names_section():
    bad = _without(GOOD_BODY, "Risk & rollback")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "-"],
        input=bad,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert "Risk & rollback" in proc.stdout
