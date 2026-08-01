#!/usr/bin/env python3
"""Verify GRO-3957 portable completed-work skill content."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "portable-skills" / "prismatic-completed-work-packet" / "SKILL.md"
text = SKILL.read_text(encoding="utf-8")

required_literals = [
    "name: prismatic-completed-work-packet",
    "SHARED_COMPLETED_WORK_SKILL_OK",
    "schema_version",
    "issue_identifier",
    "source_path",
    "changed_files",
    "verification",
    "proof_markers",
    "non_claims",
    "lane_scope",
    "risk_level",
    "No PR was opened.",
    "No production deployment was triggered.",
    "Blocked output is valid output",
    'classification: "merge_ready"',
    'classification: "blocked_external"',
]
missing = [item for item in required_literals if item not in text]
if missing:
    raise SystemExit(f"Missing required skill content: {missing}")

# Ensure the skill covers packet examples and required status vocabulary.
for status in ["completed", "blocked", "waiting", "partial", "failed"]:
    if not re.search(rf"\b{status}\b", text):
        raise SystemExit(f"Missing status vocabulary: {status}")

# Ensure the skill explicitly prevents fake success claims.
anti_claim_patterns = [
    r"Never claim a PR URL",
    r"No auto-merge enabled",
    r"No Linear issue marked Done",
    r"Do not invent a branch",
]
for pattern in anti_claim_patterns:
    if not re.search(pattern, text):
        raise SystemExit(f"Missing anti-claim guard: {pattern}")

print("SHARED_COMPLETED_WORK_SKILL_OK")
print(f"skill_path={SKILL.relative_to(ROOT)}")
print(f"bytes={SKILL.stat().st_size}")
print("canonical_fields=present")
print("proof_blocks=present")
print("non_claims=present")
print("packet_examples=present")
print("lane_risk_selection=present")
