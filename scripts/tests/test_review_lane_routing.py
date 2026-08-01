from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LINEAR_RELABEL = REPO_ROOT / "scripts" / "linear_relabel.py"
SUPERVISOR = REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor.py"


def _load_linear_relabel():
    spec = importlib.util.spec_from_file_location(
        "linear_relabel_for_test", LINEAR_RELABEL
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _issue(*, title: str, state: str = "Todo", labels: list[str] | None = None) -> dict:
    return {
        "identifier": "GRO-3488",
        "title": title,
        "description": "Acceptance criteria with enough detail to otherwise look launchable.",
        "priority": 2,
        "state": {"name": state},
        "labels": {"nodes": [{"name": label} for label in (labels or ["agent:ned"])]},
    }


def test_linear_relabel_keeps_peer_review_out_of_dispatch_ready():
    mod = _load_linear_relabel()

    classification = mod.classify_issue(
        _issue(title="Peer review runnable-looking work", labels=["agent:peer-review"])
    )

    assert classification.suggested_agent is None
    assert classification.suggested_engine_consumable == "false"
    assert classification.should_dispatch is False
    assert classification.reason == "review-only lane (not execution-dispatchable)"
    assert "dispatch:ready" not in classification.to_add_labels()


def test_linear_relabel_keeps_in_review_state_out_of_dispatch_ready():
    mod = _load_linear_relabel()

    classification = mod.classify_issue(
        _issue(title="Implement review feedback", state="In Review")
    )

    assert classification.suggested_engine_consumable == "false"
    assert classification.should_dispatch is False
    assert "dispatch:ready" not in classification.to_add_labels()


def test_supervisor_default_agent_labels_exclude_review_lanes():
    source = SUPERVISOR.read_text()
    default_block = source.split("AGY_LABELS_DEFAULT = [", 1)[1].split("]", 1)[0]

    for review_label in [
        "agent:peer-review",
        "agent:ned-review",
        "agent:needs-human-review",
        "agent:post-publish-review",
        "agent:post-publish-review-agy-approved",
        "agent:post-publish-review-jules-approved",
    ]:
        assert review_label not in default_block


def test_linear_relabel_still_marks_runnable_work_dispatchable():
    mod = _load_linear_relabel()

    classification = mod.classify_issue(
        _issue(title="Implement runnable dispatch rule")
    )

    assert classification.suggested_engine_consumable == "true"
    assert classification.should_dispatch is True
    assert "dispatch:ready" in classification.to_add_labels()
