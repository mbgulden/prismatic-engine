"""Tests for the high-impact task classification logic (Gap 8)."""

from __future__ import annotations

import pytest

from prismatic.review.impact import is_high_impact


def test_invalid_input():
    assert is_high_impact(None) is False
    assert is_high_impact([]) is False
    assert is_high_impact("not a dict") is False


def test_priority_labels():
    # Priority:high string label
    assert is_high_impact({"labels": ["priority:high"]}) is True
    # Priority:urgent string label
    assert is_high_impact({"labels": ["priority:urgent"]}) is True
    # Case insensitivity
    assert is_high_impact({"labels": ["Priority:Urgent"]}) is True
    
    # Nested label dictionaries (Linear format)
    assert is_high_impact({"labels": [{"name": "priority:high"}]}) is True
    assert is_high_impact({"labels": {"nodes": [{"name": "priority:high"}]}}) is True
    
    # Priority fields
    assert is_high_impact({"priority": "high"}) is True
    assert is_high_impact({"priority": "urgent"}) is True
    assert is_high_impact({"priority": "HIGH"}) is True
    
    # Low priority
    assert is_high_impact({"labels": ["priority:low"]}) is False
    assert is_high_impact({"priority": "low"}) is False


def test_new_feature():
    # Label
    assert is_high_impact({"labels": ["type:feature"]}) is True
    assert is_high_impact({"labels": [{"name": "type:feature"}]}) is True
    
    # Field
    assert is_high_impact({"type": "feature"}) is True
    assert is_high_impact({"type": "Feature"}) is True
    
    # Bug
    assert is_high_impact({"type": "bug"}) is False
    assert is_high_impact({"labels": ["type:bug"]}) is False


def test_infra_changes():
    # prismatic/ dir
    assert is_high_impact({"files": ["prismatic/review/impact.py"]}) is True
    assert is_high_impact({"files": ["repo_name/prismatic/review/impact.py"]}) is True
    
    # deploy/ dir
    assert is_high_impact({"files": ["deploy/vars.yaml"]}) is True
    assert is_high_impact({"changed_files": [{"path": "repo/deploy/prod.yaml"}]}) is True
    
    # infra/ dir
    assert is_high_impact({"modified_files": {"repo": ["infra/k8s/pod.yaml"]}}) is True
    
    # Non-infra directory matching
    assert is_high_impact({"files": ["src/app.py"]}) is False
    # Partial matching should not trigger (e.g. prismatic-engine-site contains "prismatic", but isn't exact path segment)
    assert is_high_impact({"files": ["prismatic-engine-site/README.md"]}) is False


def test_cross_repo_changes():
    # Explicit repos list
    assert is_high_impact({"repos": ["repo_a", "repo_b"]}) is True
    assert is_high_impact({"repositories": ["repo_1", "repo_2"]}) is True
    
    # Direct dictionary keys
    assert is_high_impact({"modified_files": {"repo_a": ["file1.py"], "repo_b": ["file2.py"]}}) is True
    
    # Multiple files starting with different repo names
    assert is_high_impact({"files": ["repo_a/file.py", "repo_b/file.py"]}) is True
    assert is_high_impact({"files": ["prismatic-engine:prismatic/review/impact.py", "prismatic-hub-ui:src/main.ts"]}) is True
    
    # Nested dictionaries in list
    assert is_high_impact({"files": [
        {"repo": "repo_a", "path": "file1.py"},
        {"repo": "repo_b", "path": "file2.py"}
    ]}) is True

    # Single repo list of files (no infra changes)
    # {"files": ["repo_a/file1.py", "repo_a/file2.py"]} has repo_a as the only repo, so len(repos) == 1.
    # Therefore, is_high_impact should be False (not feature, not high priority, not infra).
    assert is_high_impact({"files": ["repo_a/file1.py", "repo_a/file2.py"]}) is False
