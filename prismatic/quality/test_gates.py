"""Tests for prismatic.quality (Phase 1 quality gates).

Covers:
  - VerificationVerdict and all 7 layers
  - DriftGate and its variants
  - ShapeRouter label routing decisions
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from prismatic.quality import (
    VerificationVerdict,
    LayerResult,
    run_verification,
    check_shape,
    check_workdir,
    check_files_changed,
    check_diff_meaningful,
    check_linked_pr,
    check_basic_syntax,
    check_goal_match,
    DriftReport,
    check_drift,
    RoutingDecision,
    route_nhr_task,
    TASK_SHAPE_VIOLATION,
    OUTPUT_REQUIRES_VERIFICATION,
    MAX_FILES_CHANGED,
)


# ─────────────────────────────────────────────────────────────────────
# Layer tests — shape_ok
# ─────────────────────────────────────────────────────────────────────


class TestCheckShape:
    """Layer 1: agent respected AGY-safe task shape."""

    def test_clean_agent_output_passes(self):
        result = check_shape(
            agent_output="I edited the file and added documentation.",
            task_body="Update the readme with new examples.",
        )
        assert result.passed is True
        assert result.name == "shape_ok"

    def test_pytest_violation_detected(self):
        result = check_shape(
            agent_output="I ran pytest to verify my changes.",
            task_body="Update the docs.",
        )
        assert result.passed is False
        assert "pytest" in result.reason.lower() or "pytest" in str(result.details)

    def test_docker_violation_detected(self):
        result = check_shape(
            agent_output="Started with `docker build -t myapp .`",
            task_body="Test the build.",
        )
        assert result.passed is False

    def test_npm_install_violation_detected(self):
        result = check_shape(
            agent_output="npm install was run successfully",
            task_body="Set up dependencies.",
        )
        assert result.passed is False

    def test_force_push_violation_detected(self):
        result = check_shape(
            agent_output="Pushed with git push --force to override.",
            task_body="Push the branch.",
        )
        assert result.passed is False

    def test_background_process_violation_detected(self):
        result = check_shape(
            agent_output="Started a background process with nohup",
            task_body="Run a watcher.",
        )
        assert result.passed is False


# ─────────────────────────────────────────────────────────────────────
# Layer tests — workdir_ok
# ─────────────────────────────────────────────────────────────────────


class TestCheckWorkdir:
    """Layer 2: agent only touched declared workdir."""

    def test_all_files_within_workdir(self):
        files = ["prismatic/quality/gates.py", "prismatic/quality/__init__.py"]
        result = check_workdir(files, "prismatic/quality")
        assert result.passed is True

    def test_one_file_outside_workdir(self):
        files = ["prismatic/quality/gates.py", "prismatic/dispatcher.py"]
        result = check_workdir(files, "prismatic/quality")
        assert result.passed is False
        assert "prismatic/dispatcher.py" in result.details["out_of_workdir"]

    def test_no_workdir_skips_check(self):
        result = check_workdir(["any/file.py"], "")
        assert result.passed is True
        assert "skipped" in result.reason.lower()

    def test_trailing_slash_normalized(self):
        files = ["prismatic/quality/gates.py"]
        result = check_workdir(files, "prismatic/quality/")
        assert result.passed is True


# ─────────────────────────────────────────────────────────────────────
# Layer tests — files_changed_ok
# ─────────────────────────────────────────────────────────────────────


class TestCheckFilesChanged:
    """Layer 3: agent touched reasonable file count."""

    def test_zero_files_fails(self):
        result = check_files_changed([])
        assert result.passed is False
        assert "zero" in result.reason.lower() or "nothing" in result.reason.lower()

    def test_reasonable_count_passes(self):
        files = [f"file_{i}.py" for i in range(10)]
        result = check_files_changed(files)
        assert result.passed is True
        assert result.details["count"] == 10

    def test_exactly_max_passes(self):
        files = [f"file_{i}.py" for i in range(MAX_FILES_CHANGED)]
        result = check_files_changed(files)
        assert result.passed is True

    def test_above_max_fails(self):
        files = [f"file_{i}.py" for i in range(MAX_FILES_CHANGED + 1)]
        result = check_files_changed(files)
        assert result.passed is False
        assert "too many" in result.reason.lower()


# ─────────────────────────────────────────────────────────────────────
# Layer tests — diff_meaningful
# ─────────────────────────────────────────────────────────────────────


class TestCheckDiffMeaningful:
    """Layer 4: diff has substance."""

    def test_empty_diff_fails(self):
        result = check_diff_meaningful("", [])
        assert result.passed is False

    def test_whitespace_only_diff_fails(self):
        diff = "+++ b/file.py\n--- a/file.py\n@@ -1,1 +1,1 @@\n"
        result = check_diff_meaningful(diff, ["file.py"])
        assert result.passed is False
        assert result.details["substantive_lines"] == 0

    def test_substantive_diff_passes(self):
        diff = """diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1,3 +1,8 @@
 def hello():
-    pass
+    print("hello world")
+    return 42
+
+def goodbye():
+    return None
"""
        result = check_diff_meaningful(diff, ["file.py"])
        assert result.passed is True
        assert result.details["substantive_lines"] >= 5


# ─────────────────────────────────────────────────────────────────────
# Layer tests — linked_pr_ok
# ─────────────────────────────────────────────────────────────────────


class TestCheckLinkedPr:
    """Layer 5: PR was opened for commit."""

    def test_no_commit_skips(self):
        result = check_linked_pr("GRO-1", commit_sha="", branch_name="")
        assert result.passed is True
        assert "skipped" in result.reason.lower()

    def test_no_check_fn_skips(self):
        result = check_linked_pr("GRO-1", commit_sha="abc123", branch_name="feat/x")
        assert result.passed is True

    def test_pr_found(self):
        def fake_check(lookup):
            return {"number": 42, "url": "https://github.com/x/y/pull/42"}
        result = check_linked_pr(
            "GRO-1",
            commit_sha="abc123",
            branch_name="feat/x",
            pr_check_fn=fake_check,
        )
        assert result.passed is True
        assert result.details["pr_number"] == 42

    def test_pr_not_found(self):
        def fake_check(lookup):
            return None
        result = check_linked_pr(
            "GRO-1",
            commit_sha="abc123",
            branch_name="feat/x",
            pr_check_fn=fake_check,
        )
        assert result.passed is False


# ─────────────────────────────────────────────────────────────────────
# Layer tests — basic_syntax_ok
# ─────────────────────────────────────────────────────────────────────


class TestCheckBasicSyntax:
    """Layer 6: .py/.json/.yaml files parse cleanly."""

    def test_valid_python(self, tmp_path):
        good = tmp_path / "good.py"
        good.write_text("def hello():\n    return 42\n")
        result = check_basic_syntax([str(good)], workdir=".")
        assert result.passed is True

    def test_invalid_python(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def hello(:\n    return 42\n")  # Syntax error
        result = check_basic_syntax([str(bad)], workdir=".")
        assert result.passed is False
        assert "syntax" in result.reason.lower() or "error" in result.reason.lower()

    def test_valid_json(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_text('{"key": "value", "n": 42}\n')
        result = check_basic_syntax([str(good)], workdir=".")
        assert result.passed is True

    def test_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"key": "value"')  # Missing closing brace
        result = check_basic_syntax([str(bad)], workdir=".")
        assert result.passed is False

    def test_non_checked_extension_skipped(self, tmp_path):
        other = tmp_path / "file.txt"
        other.write_text("garbage content {{{")
        result = check_basic_syntax([str(other)], workdir=".")
        assert result.passed is True

    def test_nonexistent_file_skipped(self, tmp_path):
        result = check_basic_syntax([str(tmp_path / "nonexistent.py")], workdir=".")
        assert result.passed is True


# ─────────────────────────────────────────────────────────────────────
# Layer tests — goal_match
# ─────────────────────────────────────────────────────────────────────


class TestCheckGoalMatch:
    """Layer 7: agent's output addresses the task's stated goal."""

    def test_keyword_overlap_passes(self):
        task = "Refactor the dispatcher module to add retry logic with exponential backoff"
        output = "I refactored the dispatcher to retry with exponential backoff. Added new logic."
        result = check_goal_match(task, output)
        assert result.passed is True

    def test_no_overlap_fails(self):
        task = "Refactor the dispatcher module to add retry logic"
        output = "I wrote some poetry about flowers and the ocean."
        result = check_goal_match(task, output)
        assert result.passed is False

    def test_partial_match_30_percent_threshold(self):
        task = "Refactor dispatcher add retry logic exponential backoff"
        output = "I touched the dispatcher to add retry logic"  # 2 of 4 = 50%
        result = check_goal_match(task, output)
        assert result.passed is True

    def test_empty_task_skipped(self):
        result = check_goal_match("", "Some agent output")
        assert result.passed is True

    def test_no_keywords_skipped(self):
        task = "Do x"
        result = check_goal_match(task, "Some output")
        assert result.passed is True


# ─────────────────────────────────────────────────────────────────────
# Full verdict — integration test
# ─────────────────────────────────────────────────────────────────────


class TestRunVerification:
    """End-to-end test of the 7-layer verdict."""

    def test_clean_task_passes_all_layers(self, tmp_path):
        # Create a real file in workdir with substantive content
        workdir = tmp_path / "prismatic" / "quality"
        workdir.mkdir(parents=True)
        good_file = workdir / "good.py"
        good_file.write_text(
            "def hello():\n"
            "    print('hello world')\n"
            "    return 42\n"
            "\n"
            "def goodbye():\n"
            "    print('goodbye')\n"
            "    return None\n"
        )

        # Diff with at least 5 substantive lines
        diff = """diff --git a/prismatic/quality/good.py b/prismatic/quality/good.py
--- a/prismatic/quality/good.py
+++ b/prismatic/quality/good.py
@@ -0,0 +1,7 @@
+def hello():
+    print('hello world')
+    return 42
+
+def goodbye():
+    print('goodbye')
+    return None
"""

        verdict = run_verification(
            issue_id="GRO-1",
            identifier="GRO-1",
            task_body="Add a hello function to the quality module",
            agent_output="I added a hello function returning 42. Quality module updated.",
            modified_files=["prismatic/quality/good.py"],
            declared_workdir="prismatic/quality",
            git_diff=diff,
            commit_sha="abc123",
            branch_name="feat/quality",
            pr_check_fn=lambda x: {"number": 1, "url": "x"},
        )

        assert verdict.passed is True, f"Verdict failed: {[l.reason for l in verdict.failed_layers]}"
        assert len(verdict.failed_layers) == 0

    def test_drift_task_fails_workdir_and_files(self):
        # 60 files modified, none in declared workdir
        files = [f"random/dir/file_{i}.py" for i in range(60)]

        verdict = run_verification(
            issue_id="GRO-2",
            identifier="GRO-2",
            task_body="Update the docs",
            agent_output="Made updates to docs",
            modified_files=files,
            declared_workdir="docs/",
        )

        assert verdict.passed is False
        failed_names = [layer.name for layer in verdict.failed_layers]
        assert "workdir_ok" in failed_names
        assert "files_changed_ok" in failed_names

    def test_zero_output_fails(self):
        verdict = run_verification(
            issue_id="GRO-3",
            identifier="GRO-3",
            task_body="Do something",
            agent_output="",
            modified_files=[],
        )
        assert verdict.passed is False
        assert "files_changed_ok" in [l.name for l in verdict.failed_layers]

    def test_verdict_to_markdown_includes_layers(self):
        verdict = VerificationVerdict(
            issue_id="GRO-4",
            identifier="GRO-4",
            layers=[
                LayerResult(name="shape_ok", passed=True, reason="clean"),
                LayerResult(name="workdir_ok", passed=False, reason="out of scope"),
            ],
        )
        md = verdict.to_markdown()
        assert "Verification Verdict" in md
        assert "shape_ok" in md
        assert "workdir_ok" in md
        assert "❌" in md or "FAIL" in md

    def test_verdict_persists_to_disk(self, tmp_path):
        verdict = VerificationVerdict(
            issue_id="GRO-5",
            identifier="GRO-5",
            layers=[LayerResult(name="shape_ok", passed=True, reason="clean")],
        )
        path = verdict.to_dict()
        assert path["passed"] is True
        assert path["identifier"] == "GRO-5"


# ─────────────────────────────────────────────────────────────────────
# DriftGate tests
# ─────────────────────────────────────────────────────────────────────


class TestCheckDrift:
    """Pre-commit drift detection."""

    def test_clean_diff_passes(self):
        files = ["prismatic/quality/gates.py", "prismatic/quality/__init__.py"]
        diff = """diff --git a/prismatic/quality/gates.py b/prismatic/quality/gates.py
--- a/prismatic/quality/gates.py
+++ b/prismatic/quality/gates.py
@@ -1,1 +1,2 @@
 # header
+new line
"""
        report = check_drift(files, "prismatic/quality", git_diff=diff)
        assert report.passed is True
        assert report.total_files == 2
        assert report.out_of_workdir == []

    def test_out_of_workdir_fails(self):
        files = ["prismatic/quality/gates.py", "prismatic/dispatcher.py"]
        report = check_drift(files, "prismatic/quality")
        assert report.passed is False
        assert "prismatic/dispatcher.py" in report.out_of_workdir

    def test_too_many_files_fails(self):
        files = [f"prismatic/file_{i}.py" for i in range(60)]
        report = check_drift(files, "prismatic")
        assert report.passed is False
        assert any("too many" in r.lower() for r in report.reasons)

    def test_oversized_file_fails(self):
        files = ["prismatic/big.py"]
        # Simulate a 600-line change
        diff_lines = ["diff --git a/prismatic/big.py b/prismatic/big.py"]
        diff_lines.append("--- a/prismatic/big.py")
        diff_lines.append("+++ b/prismatic/big.py")
        diff_lines.append("@@ -1,1 +1,601 @@")
        for i in range(600):
            diff_lines.append(f"+line {i}")
        diff = "\n".join(diff_lines)

        report = check_drift(files, "prismatic", git_diff=diff, max_lines_per_file=500)
        assert report.passed is False
        assert len(report.oversized_files) == 1

    def test_no_workdir_fails(self):
        report = check_drift(["any/file.py"], "")
        assert report.passed is False

    def test_drift_report_to_markdown(self):
        report = DriftReport(
            passed=False,
            out_of_workdir=["bad/file.py"],
            total_files=2,
            reasons=["files outside workdir"],
        )
        md = report.to_markdown()
        assert "Drift Gate" in md
        assert "FAIL" in md
        assert "bad/file.py" in md


# ─────────────────────────────────────────────────────────────────────
# ShapeRouter tests
# ─────────────────────────────────────────────────────────────────────


class TestRouteNhrTask:
    """Auto-route NHR-style tasks to correct new label."""

    def test_clean_task_no_relabel(self):
        decision = route_nhr_task("Update the readme with examples.")
        assert decision.should_relabel is False

    def test_pytest_task_routes_to_shape_violation(self):
        decision = route_nhr_task("Run pytest to verify the fix.")
        assert decision.should_relabel is True
        assert decision.new_label == TASK_SHAPE_VIOLATION
        assert decision.sla_hours == 24

    def test_docker_task_routes_to_shape_violation(self):
        decision = route_nhr_task("Use docker build to compile the image.")
        assert decision.should_relabel is True
        assert decision.new_label == TASK_SHAPE_VIOLATION

    def test_unresolved_output_routes_to_verification(self):
        decision = route_nhr_task(
            "Update the docs.",
            agent_output="I tried but was unable to complete. TODO: review needed.",
        )
        assert decision.should_relabel is True
        assert decision.new_label == OUTPUT_REQUIRES_VERIFICATION
        assert decision.sla_hours == 12

    def test_clean_output_no_relabel(self):
        decision = route_nhr_task(
            "Update the docs.",
            agent_output="Successfully updated the docs with new examples.",
        )
        assert decision.should_relabel is False


# ─────────────────────────────────────────────────────────────────────
# Constants exposed correctly
# ─────────────────────────────────────────────────────────────────────


def test_label_constants():
    assert TASK_SHAPE_VIOLATION == "task:shape-violation"
    assert OUTPUT_REQUIRES_VERIFICATION == "output:requires-verification"
    assert MAX_FILES_CHANGED == 50