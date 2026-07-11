from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    ROOT / "plugins" / "pwp" / "linear_task_templates" / "pwp-theme-lane-templates.json"
)
DOC_PATH = ROOT / "plugins" / "pwp" / "docs" / "pwp-theme-linear-task-templates.md"

EXPECTED_LANES = {
    "content strategist",
    "design/theme",
    "Astro implementation",
    "accessibility verifier",
    "SEO/schema",
    "deploy",
    "reviewer",
}


def load_templates() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_pwp_theme_lane_templates_cover_phase_9_lanes() -> None:
    data = load_templates()
    lanes = {template["lane"] for template in data["templates"]}
    assert lanes == EXPECTED_LANES
    assert data["buildPolicy"]["initialState"] == "Todo"
    assert data["buildPolicy"]["requiresMichaelBuildStart"] is True


def test_pwp_theme_lane_templates_do_not_dispatch_prematurely() -> None:
    data = load_templates()
    assert "dispatch:ready" in data["buildPolicy"]["forbiddenLabelsBeforeHumanStart"]
    for template in data["templates"]:
        assert "dispatch:ready" not in template["defaultLabels"]
        assert "dispatch:ready" in template["forbiddenLabelsBeforeHumanStart"]
        assert any(
            "No dispatch:ready" in item for item in template["completionChecklist"]
        )


def test_pwp_theme_lane_templates_include_contracts_outputs_and_verification() -> None:
    data = load_templates()
    shared = data["sharedContracts"]
    assert shared["masterPlan"].endswith("pwp-ai-theme-system-master-plan.md")
    for key in (
        "themeManifest",
        "moduleContract",
        "tokenContract",
        "emdashMapContract",
    ):
        assert shared[key].startswith("plugins/pwp/")

    for template in data["templates"]:
        assert template["inputArtifacts"], template["lane"]
        assert template["allowedFileGlobs"], template["lane"]
        assert template["outputArtifacts"], template["lane"]
        assert template["verificationCommands"], template["lane"]
        assert "content/**" in template["blockedFileGlobs"]
        assert "assets/**" in template["blockedFileGlobs"]


def test_pwp_theme_lane_template_docs_reference_template_artifact() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "pwp-theme-lane-templates.json" in doc
    for lane in EXPECTED_LANES:
        assert lane in doc
