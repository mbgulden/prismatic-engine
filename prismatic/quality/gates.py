"""Prismatic Quality Gates — Phase 1.

Implements:
  - VerificationVerdict: 7-layer post-completion verdict (Gap 2)
  - DriftGate: pre-commit drift detection (Gap 3)
  - ShapeRouter: routes tasks between task:shape-violation and output:requires-verification (Gap 1)

Reference: okf/operations/prismatic-quality-gates-comprehensive-plan.md

Each layer is a separate function that returns a boolean pass/fail and
a human-readable reason string. All layers must pass for the overall
verdict to be PASS.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ─────────────────────────────────────────────────────────────────────
# Gap 2: VerificationVerdict — 7-layer post-completion check
# ─────────────────────────────────────────────────────────────────────


@dataclass
class LayerResult:
    """One verification layer's result."""

    name: str
    passed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationVerdict:
    """Verdict from running all 7 verification layers against a completed task."""

    issue_id: str
    identifier: str
    layers: list[LayerResult] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def passed(self) -> bool:
        return all(layer.passed for layer in self.layers)

    @property
    def failed_layers(self) -> list[LayerResult]:
        return [layer for layer in self.layers if not layer.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "identifier": self.identifier,
            "passed": self.passed,
            "failed_layers": [layer.name for layer in self.failed_layers],
            "layers": [asdict(layer) for layer in self.layers],
            "timestamp": self.timestamp,
        }

    def to_markdown(self) -> str:
        """Format verdict as a Linear comment."""
        icon = "✅" if self.passed else "❌"
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"## {icon} Verification Verdict: {status}",
            "",
            f"**Issue:** `{self.identifier}`  ",
            f"**Timestamp:** {self.timestamp}",
            "",
            "### Layer Results",
            "",
        ]
        for layer in self.layers:
            li = "✅" if layer.passed else "❌"
            lines.append(
                f"- {li} **{layer.name}**: {layer.reason or ('PASS' if layer.passed else 'FAIL')}"
            )
            if layer.details and not layer.passed:
                for k, v in layer.details.items():
                    lines.append(f"  - {k}: {v}")
        if not self.passed:
            lines.extend(
                [
                    "",
                    "### Failed Layers",
                    "",
                ]
            )
            for layer in self.failed_layers:
                lines.append(f"- **{layer.name}**: {layer.reason}")
            lines.extend(
                [
                    "",
                    "**Action**: Task re-routed to `output:requires-verification` for human review.",
                ]
            )
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Layer 1: shape_ok — agent respected AGY-safe task shape
# ─────────────────────────────────────────────────────────────────────

# Patterns indicating the agent ran dangerous commands
SHAPE_VIOLATION_PATTERNS = [
    (r"\bpytest\b", "Ran pytest — forbidden in autonomous mode"),
    (r"\bdocker (build|run|push)\b", "Ran docker build/run/push — forbidden"),
    (r"\bnpm (install|run|test|build)\b", "Ran npm install/run/test — forbidden"),
    (r"\bpgrep\b.*kill|kill -9", "Sent kill signal — out of scope"),
    (r"Background process started|nohup|disown", "Spawned background process"),
    (r"git push\s+--force|git push\s+-f\b", "Force-pushed (not AGY-safe)"),
    (r"curl.*--upload-file|rsync.*--delete", "Risky file transfer"),
]


def check_shape(agent_output: str, task_body: str) -> LayerResult:
    """Verify the agent did not violate AGY-safe task shape.

    Looks for forbidden command patterns in the agent's output and
    compares against the declared task body.
    """
    details: dict[str, Any] = {"violations": []}

    # Check agent output for forbidden patterns
    for pattern, label in SHAPE_VIOLATION_PATTERNS:
        if re.search(pattern, agent_output, re.IGNORECASE):
            details["violations"].append(label)

    if details["violations"]:
        return LayerResult(
            name="shape_ok",
            passed=False,
            reason=f"Shape violations: {', '.join(details['violations'][:3])}",
            details=details,
        )

    return LayerResult(
        name="shape_ok", passed=True, reason="No shape violations detected"
    )


# ─────────────────────────────────────────────────────────────────────
# Layer 2: workdir_ok — agent only touched declared workdir
# ─────────────────────────────────────────────────────────────────────


def check_workdir(modified_files: list[str], declared_workdir: str) -> LayerResult:
    """Verify all modified files are within the declared workdir.

    Uses Path.resolve() and Path.relative_to() to safely normalize paths
    and reject any path that escapes the workdir via `..` traversal or
    exploits prefix collisions (e.g. 'docs_extra' vs 'docs').
    """
    if not declared_workdir:
        return LayerResult(
            name="workdir_ok",
            passed=True,
            reason="No workdir declared — skipped",
        )

    # Normalize workdir to absolute path
    workdir_path = Path(declared_workdir).resolve()

    out_of_workdir: list[str] = []
    for f in modified_files:
        try:
            # Resolve relative paths against CWD, then check it's inside workdir
            file_path = Path(f).resolve()
            file_path.relative_to(workdir_path)  # raises ValueError if not relative
        except (ValueError, OSError):
            # Either not inside workdir, or path doesn't exist
            # We check existence separately below
            if Path(f).exists():
                out_of_workdir.append(f)
            else:
                # For non-existent files (e.g. new files), still check logically
                # by comparing normalized string paths
                try:
                    file_path = Path(os.path.normpath(f)).resolve()
                    file_path.relative_to(workdir_path)
                except (ValueError, OSError):
                    out_of_workdir.append(f)

    if out_of_workdir:
        return LayerResult(
            name="workdir_ok",
            passed=False,
            reason=f"{len(out_of_workdir)} files outside declared workdir '{declared_workdir}'",
            details={
                "out_of_workdir": out_of_workdir[:10],
                "count": len(out_of_workdir),
            },
        )

    return LayerResult(
        name="workdir_ok",
        passed=True,
        reason=f"All {len(modified_files)} files within '{declared_workdir}'",
    )


# ─────────────────────────────────────────────────────────────────────
# Layer 3: files_changed_ok — agent touched reasonable file count
# ─────────────────────────────────────────────────────────────────────

MAX_FILES_CHANGED = 50  # Tasks touching more than this are flagged as drift


def check_files_changed(modified_files: list[str]) -> LayerResult:
    """Verify the agent touched a reasonable number of files.

    Bounds enforced: 1 ≤ count ≤ MAX_FILES_CHANGED (50).
    Zero files is rejected (agent did nothing).
    More than MAX_FILES_CHANGED is rejected (likely drift).
    Note: no minimum other than 1 — single-file edits are legitimate.
    """
    count = len(modified_files)
    details = {"count": count}

    if count == 0:
        return LayerResult(
            name="files_changed_ok",
            passed=False,
            reason="Zero files modified — agent did nothing",
            details=details,
        )

    if count > MAX_FILES_CHANGED:
        return LayerResult(
            name="files_changed_ok",
            passed=False,
            reason=f"Too many files modified: {count} (max {MAX_FILES_CHANGED})",
            details=details,
        )

    return LayerResult(
        name="files_changed_ok",
        passed=True,
        reason=f"Modified {count} files (within bounds)",
        details=details,
    )


# ─────────────────────────────────────────────────────────────────────
# Layer 4: diff_meaningful — diff has substance
# ─────────────────────────────────────────────────────────────────────

MIN_DIFF_LINES = 5  # Less than this = empty/whitespace-only change


def check_diff_meaningful(git_diff: str, modified_files: list[str]) -> LayerResult:
    """Verify the diff has substance (not just whitespace or single-line tweaks)."""
    if not git_diff:
        return LayerResult(
            name="diff_meaningful",
            passed=False,
            reason="Empty git diff",
            details={"files": modified_files},
        )

    # Count substantive lines (non-whitespace, non-header).
    # Note: we intentionally skip the dead comment-filter branch — a diff line
    # like '+# comment' starts with '+' (not '#') so the old filter never fired
    # for added/removed lines, only for context lines which we don't count anyway.
    substantive_lines = 0
    for line in git_diff.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("+++", "---", "@@", "diff ", "index ")):
            continue
        substantive_lines += 1

    details = {"substantive_lines": substantive_lines, "files": len(modified_files)}

    if substantive_lines < MIN_DIFF_LINES:
        return LayerResult(
            name="diff_meaningful",
            passed=False,
            reason=f"Diff has only {substantive_lines} substantive lines (min {MIN_DIFF_LINES})",
            details=details,
        )

    return LayerResult(
        name="diff_meaningful",
        passed=True,
        reason=f"Diff has {substantive_lines} substantive lines",
        details=details,
    )


# ─────────────────────────────────────────────────────────────────────
# Layer 5: linked_pr_ok — if commit was made, PR exists or was opened
# ─────────────────────────────────────────────────────────────────────


def check_linked_pr(
    issue_id: str,
    commit_sha: str = "",
    branch_name: str = "",
    pr_check_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> LayerResult:
    """Verify a PR was opened for the commit.

    Args:
        issue_id: Linear issue ID or identifier
        commit_sha: If agent made a commit, its SHA
        branch_name: Branch name to look for a PR on
        pr_check_fn: Callable that takes a branch name and returns PR dict
                     (or None if no PR exists). Inject for testability.
    """
    if not commit_sha and not branch_name:
        return LayerResult(
            name="linked_pr_ok",
            passed=True,
            reason="No commit made — PR check skipped",
        )

    if pr_check_fn is None:
        # Default: skip if no checker injected
        return LayerResult(
            name="linked_pr_ok",
            passed=True,
            reason="PR check function not provided — skipped",
        )

    lookup = branch_name or commit_sha
    pr = pr_check_fn(lookup)

    if pr is None:
        return LayerResult(
            name="linked_pr_ok",
            passed=False,
            reason=f"No PR found for branch/commit '{lookup}'",
            details={
                "branch": branch_name,
                "sha": commit_sha[:7] if commit_sha else "",
            },
        )

    return LayerResult(
        name="linked_pr_ok",
        passed=True,
        reason=f"PR #{pr.get('number', '?')} linked",
        details={"pr_number": pr.get("number"), "pr_url": pr.get("url", "")},
    )


# ─────────────────────────────────────────────────────────────────────
# Layer 6: basic_syntax_ok — python/json/yaml files pass syntax check
# ─────────────────────────────────────────────────────────────────────

# File extensions to syntax-check
SYNTAX_CHECK_EXTS = {".py", ".json", ".yaml", ".yml"}


def check_basic_syntax(modified_files: list[str], workdir: str = ".") -> LayerResult:
    """Syntax-check any .py/.json/.yaml/.yml files in the diff."""
    errors: list[str] = []

    for filepath in modified_files:
        path = Path(filepath)
        if path.suffix not in SYNTAX_CHECK_EXTS:
            continue

        # Resolve full path
        full_path = path if path.is_absolute() else Path(workdir) / path
        if not full_path.exists():
            continue  # File may have been deleted — skip

        try:
            if path.suffix == ".py":
                # Use in-memory compile to avoid py_compile writing __pycache__/
                source = full_path.read_text(encoding="utf-8", errors="replace")
                compile(source, str(full_path), "exec")
            elif path.suffix == ".json":
                with open(full_path) as f:
                    json.load(f)
            elif path.suffix in (".yaml", ".yml"):
                # Lightweight YAML check — full parsing needs PyYAML
                # which may not be installed; skip if not available
                try:
                    import yaml

                    with open(full_path) as f:
                        yaml.safe_load(f)
                except ImportError:
                    pass  # YAML check optional
        except Exception as e:
            errors.append(f"{filepath}: {type(e).__name__}: {str(e)[:100]}")

    if errors:
        return LayerResult(
            name="basic_syntax_ok",
            passed=False,
            reason=f"{len(errors)} syntax error(s)",
            details={"errors": errors[:5], "total_errors": len(errors)},
        )

    return LayerResult(
        name="basic_syntax_ok", passed=True, reason="All checked files parse cleanly"
    )


# ─────────────────────────────────────────────────────────────────────
# Layer 7: goal_match — agent's "what I did" matches the task's stated goal
# ─────────────────────────────────────────────────────────────────────


def check_goal_match(task_body: str, agent_output: str) -> LayerResult:
    """Verify the agent's output actually addresses the task's stated goal.

    Heuristic: extract key nouns/verbs from the task body and check they
    appear in the agent's output. This is intentionally simple — it's a
    smoke test for "did the agent stay on topic", not semantic similarity.
    """
    if not task_body:
        return LayerResult(
            name="goal_match",
            passed=True,
            reason="No task body — skipped",
        )

    # Extract keywords (lowercase, length > 4, alphanumeric)
    task_words = set(
        word.lower().strip(".,;:()[]{}!?'\"")
        for word in re.findall(r"\b\w{5,}\b", task_body)
    )
    # Common stopwords
    stopwords = {
        "should",
        "would",
        "could",
        "these",
        "those",
        "their",
        "there",
        "where",
        "which",
        "while",
        "about",
        "after",
        "before",
        "other",
        "using",
        "based",
        "create",
        "update",
        "change",
        "implement",
        "please",
        "thanks",
        "hello",
        "regards",
        "agent",
        "task",
    }
    keywords = task_words - stopwords

    if not keywords:
        return LayerResult(
            name="goal_match",
            passed=True,
            reason="No extractable keywords — skipped",
        )

    agent_lower = agent_output.lower()
    matched = sum(1 for kw in keywords if kw in agent_lower)
    match_rate = matched / len(keywords) if keywords else 0

    details = {
        "keywords_total": len(keywords),
        "keywords_matched": matched,
        "match_rate": f"{match_rate:.0%}",
    }

    # Require 30% keyword match
    if match_rate < 0.30:
        return LayerResult(
            name="goal_match",
            passed=False,
            reason=f"Only {matched}/{len(keywords)} keywords matched ({match_rate:.0%})",
            details=details,
        )

    return LayerResult(
        name="goal_match",
        passed=True,
        reason=f"{matched}/{len(keywords)} keywords matched ({match_rate:.0%})",
        details=details,
    )


# ─────────────────────────────────────────────────────────────────────
# Convenience: run all 7 layers
# ─────────────────────────────────────────────────────────────────────


def run_verification(
    issue_id: str,
    identifier: str,
    task_body: str,
    agent_output: str,
    modified_files: list[str],
    declared_workdir: str = "",
    git_diff: str = "",
    commit_sha: str = "",
    branch_name: str = "",
    pr_check_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> VerificationVerdict:
    """Run all 7 verification layers and return a VerificationVerdict."""
    verdict = VerificationVerdict(issue_id=issue_id, identifier=identifier)
    verdict.layers = [
        check_shape(agent_output, task_body),
        check_workdir(modified_files, declared_workdir),
        check_files_changed(modified_files),
        check_diff_meaningful(git_diff, modified_files),
        check_linked_pr(issue_id, commit_sha, branch_name, pr_check_fn),
        check_basic_syntax(modified_files, workdir="."),
        check_goal_match(task_body, agent_output),
    ]
    return verdict


# ─────────────────────────────────────────────────────────────────────
# Gap 3: DriftGate — pre-commit drift detection
# ─────────────────────────────────────────────────────────────────────


@dataclass
class DriftReport:
    """Result of a drift check."""

    passed: bool
    out_of_workdir: list[str] = field(default_factory=list)
    total_files: int = 0
    total_lines: int = 0
    oversized_files: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        icon = "✅" if self.passed else "❌"
        lines = [
            f"## {icon} Drift Gate: {'PASS' if self.passed else 'FAIL'}",
            "",
            f"- **Total files modified**: {self.total_files}",
            f"- **Total lines changed**: {self.total_lines}",
            f"- **Files outside workdir**: {len(self.out_of_workdir)}",
            f"- **Oversized files (>500 lines)**: {len(self.oversized_files)}",
        ]
        if self.reasons:
            lines.extend(["", "### Reasons", ""])
            for r in self.reasons:
                lines.append(f"- {r}")
        if self.out_of_workdir:
            lines.extend(["", "### Out-of-workdir files", ""])
            for f in self.out_of_workdir[:10]:
                lines.append(f"- `{f}`")
        if self.oversized_files:
            lines.extend(["", "### Oversized files", ""])
            for f in self.oversized_files[:10]:
                lines.append(f"- `{f}`")
        return "\n".join(lines)


def check_drift(
    modified_files: list[str],
    declared_workdir: str,
    git_diff: str = "",
    max_files: int = MAX_FILES_CHANGED,
    max_lines_per_file: int = 500,
) -> DriftReport:
    """Pre-commit drift gate.

    Detects:
      - Files modified outside declared workdir (safe against `..` traversal
        and prefix collisions like 'docs_extra' vs 'docs')
      - Total file count exceeding max_files
      - Individual files exceeding max_lines_per_file

    Args:
        modified_files: List of file paths changed
        declared_workdir: The workdir the agent was scoped to (e.g. 'prismatic/quality/')
        git_diff: Full git diff text (for line counting)
        max_files: Maximum allowed files (default 50)
        max_lines_per_file: Maximum lines per file (default 500)

    Returns:
        DriftReport with verdict and details
    """
    report = DriftReport(passed=True)
    report.total_files = len(modified_files)

    if not declared_workdir:
        report.passed = False
        report.reasons.append("No declared workdir — cannot verify scope")
        return report

    # Normalize workdir to absolute path (safe path comparison)
    try:
        workdir_path = Path(declared_workdir).resolve()
    except (OSError, RuntimeError) as e:
        report.passed = False
        report.reasons.append(f"Cannot resolve workdir '{declared_workdir}': {e}")
        return report

    # Check out-of-workdir using safe relative_to() check
    out_of_workdir: list[str] = []
    for f in modified_files:
        try:
            # Try to resolve and check relative_to
            file_path = Path(f).resolve()
            file_path.relative_to(workdir_path)
        except (ValueError, OSError):
            # Try with normpath for non-existent files (e.g. new files)
            try:
                file_path = Path(os.path.normpath(f)).resolve()
                file_path.relative_to(workdir_path)
            except (ValueError, OSError):
                out_of_workdir.append(f)

    report.out_of_workdir = out_of_workdir

    if out_of_workdir:
        report.passed = False
        report.reasons.append(
            f"{len(out_of_workdir)} files outside workdir '{declared_workdir}'"
        )

    # Check file count
    if len(modified_files) > max_files:
        report.passed = False
        report.reasons.append(f"Too many files: {len(modified_files)} > {max_files}")

    # Check oversized files (count lines per file in diff)
    if git_diff:
        per_file_lines = _count_lines_per_file(git_diff)
        report.total_lines = sum(per_file_lines.values())
        for f, line_count in per_file_lines.items():
            if line_count > max_lines_per_file:
                report.oversized_files.append(f"{f} ({line_count} lines)")

        if report.oversized_files:
            report.passed = False
            report.reasons.append(
                f"{len(report.oversized_files)} files exceed {max_lines_per_file} lines"
            )

    return report


def _count_lines_per_file(git_diff: str) -> dict[str, int]:
    """Parse a unified diff and count changed lines per file."""
    per_file: dict[str, int] = {}
    current_file = None

    for line in git_diff.splitlines():
        # Match "diff --git a/path b/path" — use `.+` to allow filenames with spaces
        m = re.match(r"^diff --git a/(.+) b/(.+)$", line)
        if m:
            # Extract just the filename (strip "b/" prefix on right side)
            current_file = m.group(1)
            per_file.setdefault(current_file, 0)
            continue

        # Match "+++ b/path" (when diff is non-git format)
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m:
            current_file = m.group(1)
            per_file.setdefault(current_file, 0)
            continue

        # Count +/- lines for current file
        if (
            current_file
            and line.startswith(("+", "-"))
            and not line.startswith(("+++", "---"))
        ):
            per_file[current_file] += 1

    return per_file


# ─────────────────────────────────────────────────────────────────────
# Gap 1: ShapeRouter — auto-route NHR-style tasks to correct new label
# ─────────────────────────────────────────────────────────────────────

# Label name constants
TASK_SHAPE_VIOLATION = "task:shape-violation"
OUTPUT_REQUIRES_VERIFICATION = "output:requires-verification"
ARCHIVED_NEEDS_HUMAN_REVIEW = "ARCHIVED-agent:needs-human-review"


@dataclass
class RoutingDecision:
    """Decision about which label a task should carry."""

    should_relabel: bool
    new_label: str
    reason: str
    sla_hours: int = 24


def route_nhr_task(task_body: str, agent_output: str = "") -> RoutingDecision:
    """Determine which new label a NHR-style task should have.

    Logic:
      - If task body has shape violations → task:shape-violation (24h SLA)
      - If agent output has unresolved output issues → output:requires-verification (12h SLA)
      - Otherwise → no relabel needed
    """
    # Check task body for shape issues
    shape_issues = []
    for pattern, label in SHAPE_VIOLATION_PATTERNS:
        if re.search(pattern, task_body, re.IGNORECASE):
            shape_issues.append(label)

    if shape_issues:
        return RoutingDecision(
            should_relabel=True,
            new_label=TASK_SHAPE_VIOLATION,
            reason=f"Task body has shape issues: {', '.join(shape_issues[:3])}",
            sla_hours=24,
        )

    # Check if agent output exists but has issues
    if agent_output:
        # Look for unresolved indicators
        unresolved_indicators = [
            r"\?\?\?|TODO|FIXME|XXX",
            r"\bunable to (complete|finish|verify)",
            r"\bblocked on\b",
            r"\bneed[s]? (human |review|approval)",
        ]
        found_indicators = []
        for pattern in unresolved_indicators:
            if re.search(pattern, agent_output, re.IGNORECASE):
                found_indicators.append(pattern)

        if found_indicators:
            return RoutingDecision(
                should_relabel=True,
                new_label=OUTPUT_REQUIRES_VERIFICATION,
                reason=f"Output has unresolved indicators: {found_indicators[:2]}",
                sla_hours=12,
            )

    return RoutingDecision(
        should_relabel=False,
        new_label="",
        reason="No relabeling needed",
    )


# ─────────────────────────────────────────────────────────────────────
# Persistence — write verdict/drift records to disk
# ─────────────────────────────────────────────────────────────────────


def _safe_identifier(identifier: str) -> str:
    """Sanitize an identifier for use in a filename.

    Linear IDs like 'GRO-123' are safe, but this protects against future
    identifiers that might contain '/', spaces, or other filesystem-unsafe chars.
    """
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", identifier)


def save_verdict(verdict: VerificationVerdict, base_dir: str | None = None) -> str:
    """Save verdict to disk as JSON. Returns path."""
    if base_dir is None:
        base_dir = (
            os.environ.get(
                "PRISMATIC_HOME",
                os.path.expanduser("~/.hermes/profiles/orchestrator"),
            )
            + "/data/verification_records"
        )

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_id = _safe_identifier(verdict.identifier)
    filename = f"verdict_{safe_id}_{timestamp}.json"
    filepath = Path(base_dir) / filename

    with open(filepath, "w") as f:
        json.dump(verdict.to_dict(), f, indent=2)

    return str(filepath)


def save_drift_report(
    report: DriftReport, identifier: str, base_dir: str | None = None
) -> str:
    """Save drift report to disk as JSON. Returns path."""
    if base_dir is None:
        base_dir = (
            os.environ.get(
                "PRISMATIC_HOME",
                os.path.expanduser("~/.hermes/profiles/orchestrator"),
            )
            + "/data/drift_reports"
        )

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_id = _safe_identifier(identifier)
    filename = f"drift_{safe_id}_{timestamp}.json"
    filepath = Path(base_dir) / filename

    with open(filepath, "w") as f:
        json.dump(report.to_dict(), f, indent=2)

    return str(filepath)


# ─────────────────────────────────────────────────────────────────────
# Gap 4 (Phase 2): agent:ned-review trigger
# ─────────────────────────────────────────────────────────────────────
#
# Wires the ``agent:ned-review`` Linear label into the orchestrator:
#
#   1. Detect the label on a task
#   2. Call :class:`prismatic.review.PRReviewer` against the linked PR
#   3. Post the verdict as a Linear comment
#   4. ``APPROVE``         -> transition to Done
#      ``REQUEST_CHANGES`` -> re-route to original worker
#      ``NEEDS_DISCUSSION``-> leave state untouched, flag Michael
#
# Reference: okf/operations/phase2-quality-gates-plan.md (Gap 4, task #6).
# This module is the trigger; the heavy reviewer logic lives in
# ``prismatic.review.pr_reviewer`` (tasks #1-5 of Gap 4).


from prismatic.review.pr_reviewer import (  # noqa: E402  (placed after the gate code on purpose)
    APPROVE,
    NEEDS_DISCUSSION,
    NED_REVIEW_LABEL,
    PRReviewResult,
    PRReviewer,
    REQUEST_CHANGES,
)
from prismatic.review.pr_reviewer_impl import RealPRReviewer  # noqa: E402
from prismatic.review.pipeline import PipelineOrchestrator  # noqa: E402


# Linear state targets produced by the trigger.
NED_REVIEW_TARGET_STATE = {
    APPROVE: "Done",
    REQUEST_CHANGES: "In Progress",  # re-route to original worker
    NEEDS_DISCUSSION: "In Review",  # flag Michael; do not auto-transition
}


@dataclass
class NedReviewDecision:
    """Decision returned by :func:`trigger_ned_review`.

    Captures the verdict, the Linear comment body that was (or would
    be) posted, and the target Linear state. ``metadata`` is opaque —
    populated by the trigger for audit logging and test assertions.
    """

    identifier: str
    triggered: bool
    verdict: str
    target_state: str
    linear_comment: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.verdict == APPROVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "triggered": self.triggered,
            "verdict": self.verdict,
            "target_state": self.target_state,
            "linear_comment": self.linear_comment,
            "metadata": self.metadata,
        }


def has_ned_review_label(labels: list[str] | list[dict[str, Any]] | None) -> bool:
    """Return True iff the task carries the ``agent:ned-review`` label.

    Accepts either a flat list of label-name strings or the richer
    ``Linear`` ``labels.nodes`` shape (``[{"name": "..."}, ...]``).
    Comparison is case-insensitive so a label spelled ``Agent:Ned-Review``
    still matches — Linear normalizes label casing inconsistently across
    API versions.
    """
    if not labels:
        return False
    needle = NED_REVIEW_LABEL.lower()
    for entry in labels:
        if isinstance(entry, str):
            if entry.lower() == needle:
                return True
        elif isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name.lower() == needle:
                return True
    return False


def _format_linear_comment(result: PRReviewResult, pr_url: str) -> str:
    """Format the reviewer verdict as a Linear-ready markdown comment."""
    icon = {"APPROVE": "✅", "REQUEST_CHANGES": "❌", "NEEDS_DISCUSSION": "💬"}.get(
        result.verdict, "ℹ️"
    )
    lines = [
        f"## {icon} Ned-Review: `{result.verdict}`",
        "",
        f"**PR:** `{pr_url}`  ",
        f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}",
        "",
        result.summary.strip(),
    ]
    if result.inline_comments:
        lines.extend(["", "### Inline comments", ""])
        for c in result.inline_comments[:20]:  # cap at 20 for readability
            lines.append(f"- `{c.path}:{c.line}` — {c.body}")
        if len(result.inline_comments) > 20:
            lines.append(f"- …and {len(result.inline_comments) - 20} more")
    lines.extend(
        [
            "",
            "**Next action:** "
            + {
                APPROVE: "transitioning to `Done`.",
                REQUEST_CHANGES: "re-routing to original worker (`In Progress`).",
                NEEDS_DISCUSSION: "leaving `In Review` for Michael.",
            }.get(result.verdict, "no state change."),
        ]
    )
    return "\n".join(lines)


def trigger_ned_review(
    issue: dict[str, Any],
    reviewer: PRReviewer | None = None,
    *,
    pipeline: PipelineOrchestrator | None = None,
    post_comment: Callable[[str, str], None] | None = None,
    transition_state: Callable[[str, str], None] | None = None,
) -> NedReviewDecision:
    """Run the ``agent:ned-review`` trigger for one Linear issue.

    The orchestrator calls this after a worker marks a task as
    "Ready for Review". ``issue`` is the Linear issue payload (or any
    dict with ``identifier``, ``labels``, and a ``pr_url`` key under
    arbitrary nesting). The function:

    1. Checks for the ``agent:ned-review`` label.
    2. Calls ``reviewer.review_pr(pr_url)`` to get a :class:`PRReviewResult`.
    3. If a ``pipeline`` is provided, calls ``pipeline.process(...)`` to
       classify impact + decide next action (advance / hold / rework / give_up).
    4. Builds the Linear comment body via :func:`_format_linear_comment`.
    5. Calls ``post_comment(identifier, body)`` if provided.
    6. Calls ``transition_state(identifier, target_state)`` if provided.

    Defaults: ``reviewer`` defaults to :class:`RealPRReviewer` (Gap 4).
    Pass an explicit ``reviewer`` (e.g. ``StubPRReviewer()``) to override.

    The two I/O callbacks are dependency-injected so the trigger stays
    unit-testable without a real Linear client. Production callers wire
    ``post_comment`` / ``transition_state`` to the Linear GraphQL
    adapter; tests pass no-op callables and assert on the returned
    :class:`NedReviewDecision`.

    Returns a :class:`NedReviewDecision` describing what happened. When
    the label is absent the decision has ``triggered=False`` and the
    state is left untouched.
    """
    identifier = issue.get("identifier") or issue.get("id") or "<unknown>"
    labels = issue.get("labels")

    if not has_ned_review_label(labels):
        return NedReviewDecision(
            identifier=identifier,
            triggered=False,
            verdict="",
            target_state="",
            linear_comment="",
            metadata={"reason": "label_missing"},
        )

    pr_url = (
        issue.get("pr_url")
        or issue.get("pullRequestUrl")
        or issue.get("pull_request_url")
        or ""
    )
    if not pr_url:
        return NedReviewDecision(
            identifier=identifier,
            triggered=True,
            verdict=NEEDS_DISCUSSION,
            target_state=NED_REVIEW_TARGET_STATE[NEEDS_DISCUSSION],
            linear_comment=(
                f"## 💬 Ned-Review: `{NEEDS_DISCUSSION}`\n\n"
                f"Issue `{identifier}` carries `{NED_REVIEW_LABEL}` but "
                "no linked PR was found on the issue payload "
                "(`pr_url` / `pullRequestUrl` missing). "
                "Leaving `In Review` for Michael."
            ),
            metadata={"reason": "pr_url_missing"},
        )

    reviewer = reviewer or RealPRReviewer()
    result = reviewer.review_pr(pr_url)

    # Optional pipeline (Gap 8): classify impact, decide next action,
    # optionally build a rework payload. The pipeline decision is
    # attached to metadata so downstream tooling (the factory's
    # dispatcher) can route accordingly.
    pipeline_decision: dict[str, Any] | None = None
    if pipeline is not None:
        decision = pipeline.process(
            identifier=identifier,
            pr_url=pr_url,
            result=result,
        )
        pipeline_decision = {
            "impact": decision.impact,
            "action": decision.action,
            "rationale": decision.rationale,
            "rework_payload": (
                {
                    "issue_identifier": decision.rework_payload.issue_identifier,
                    "pr_url": decision.rework_payload.pr_url,
                    "verdict": decision.rework_payload.verdict,
                    "summary": decision.rework_payload.summary,
                    "findings": decision.rework_payload.findings,
                    "rework_attempt": decision.rework_payload.rework_attempt,
                    "max_rework_attempts": decision.rework_payload.max_rework_attempts,
                    "rework_label": decision.rework_payload.rework_label,
                }
                if decision.rework_payload is not None
                else None
            ),
            "metadata": decision.metadata,
        }

    target_state = NED_REVIEW_TARGET_STATE[result.verdict]
    comment = _format_linear_comment(result, pr_url)

    if post_comment is not None:
        try:
            post_comment(identifier, comment)
        except Exception as exc:  # pragma: no cover - I/O failure path
            # Surface as metadata; do NOT crash the trigger.
            result.metadata.setdefault("post_comment_error", str(exc))

    if transition_state is not None:
        try:
            transition_state(identifier, target_state)
        except Exception as exc:  # pragma: no cover - I/O failure path
            result.metadata.setdefault("transition_error", str(exc))

    # Attach pipeline decision to metadata if it ran.
    if pipeline_decision is not None:
        result.metadata["pipeline"] = pipeline_decision

    return NedReviewDecision(
        identifier=identifier,
        triggered=True,
        verdict=result.verdict,
        target_state=target_state,
        linear_comment=comment,
        metadata=result.metadata,
    )
