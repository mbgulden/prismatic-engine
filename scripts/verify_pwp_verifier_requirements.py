#!/usr/bin/env python3
"""Verify PWP verifier-artifact checklist requirements are documented.

This is an ad hoc targeted verifier for GRO-3739. It checks that the PWP
Phase 9 plan and dedicated artifact-requirements doc carry the required
checklist and evidence fields before generated issues are considered
dispatch-ready.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

REQUIRED_EVIDENCE_FIELDS = [
    "verification_status",
    "verification_scope",
    "failure_category",
    "cleanup_status",
    "done_gate_result",
]

REQUIRED_ARTIFACT_TERMS = [
    "Build/test",
    "Accessibility",
    "Visual/regression",
    "Contract/schema",
    "Deployment/provenance",
]

REQUIRED_FILES = {
    "requirements_doc": "plugins/pwp/docs/pwp-verifier-artifact-requirements.md",
    "master_plan": "plugins/pwp/docs/pwp-ai-theme-system-master-plan.md",
}


def read(relative_path: str) -> str:
    return (REPO / relative_path).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify PWP verifier artifact checklist requirements"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    args = parser.parse_args()

    contents = {name: read(path) for name, path in REQUIRED_FILES.items()}
    failures: list[str] = []

    requirements_doc = contents["requirements_doc"]
    master_plan = contents["master_plan"]

    for field in REQUIRED_EVIDENCE_FIELDS:
        if field not in requirements_doc:
            failures.append(f"requirements_doc missing evidence field {field}")

    for term in REQUIRED_ARTIFACT_TERMS:
        if term not in requirements_doc:
            failures.append(f"requirements_doc missing artifact term {term}")

    if "### Verifier artifact checklist" not in requirements_doc:
        failures.append("requirements_doc missing checklist heading")
    if "pwp-verifier-artifact-requirements.md" not in master_plan:
        failures.append("master_plan missing requirements doc link")
    if "dispatch:ready" not in requirements_doc:
        failures.append("requirements_doc missing dispatch readiness guard")
    if "done_gate_result=done" not in requirements_doc:
        failures.append("done gate requirement missing")

    summary = {
        "verdict": "PASS" if not failures else "FAIL",
        "scope": "ad_hoc_targeted",
        "checked_files": REQUIRED_FILES,
        "required_evidence_fields": REQUIRED_EVIDENCE_FIELDS,
        "required_artifact_terms": REQUIRED_ARTIFACT_TERMS,
        "failures": failures,
    }

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Verdict: {summary['verdict']}")
        print("Checked files:")
        for path in REQUIRED_FILES.values():
            print(f"- {path}")
        if failures:
            print("Failures:")
            for failure in failures:
                print(f"- {failure}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
