"""Prismatic PR Reviewer — Real Implementation (Phase 2 / Gap 4).

Replaces the stub with a real PR reviewer that:
- Fetches PR diff via GitHub CLI (`gh pr diff`)
- Detects hardcoded secrets (AWS keys, private keys, API tokens)
- Measures code-quality metrics (function length, file length)
- Checks test coverage heuristic (does diff touch tests?)
- Returns verdict: APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION

The contract is the same as the stub: PRReviewer.review_pr(pr_url) -> PRReviewResult.

Reference: okf/operations/phase2-quality-gates-plan.md (Gap 4)
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

# Re-use the stub's contract
from .apply_impact_rules import fire_hook
from .hooks import HOOK_BEFORE_QUALITY_CHECKS, HOOK_BEFORE_SECRET_SCAN
from .pr_reviewer import (
    APPROVE,
    NEEDS_DISCUSSION,
    REQUEST_CHANGES,
    InlineComment,
    PRReviewResult,
)


# ─────────────────────────────────────────────────────────────────────
# Secret detection patterns
# ─────────────────────────────────────────────────────────────────────

SECRET_PATTERNS: list[tuple[str, str, str]] = [
    # AWS access keys
    (r"AKIA[0-9A-Z]{16}", "aws_access_key", "critical"),
    # AWS secret keys (40 char base64 after 'aws_secret_access_key=')
    (
        r"aws_secret_access_key\s*=\s*[\"']?([A-Za-z0-9/+=]{40})",
        "aws_secret_key",
        "critical",
    ),
    # GitHub personal access tokens (ghp_)
    (r"ghp_[A-Za-z0-9]{36}", "github_pat", "critical"),
    # GitHub OAuth tokens (gho_)
    (r"gho_[A-Za-z0-9]{36}", "github_oauth", "critical"),
    # Slack tokens (xoxb-, xoxp-)
    (r"xox[bpars]-[A-Za-z0-9-]{10,}", "slack_token", "critical"),
    # Stripe secret keys (sk_live_, sk_test_)
    (r"sk_(live|test)_[A-Za-z0-9]{24,}", "stripe_key", "critical"),
    # Private keys (PEM format)
    (
        r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        "private_key",
        "critical",
    ),
    # Database URLs with passwords
    (r"(postgres|mysql|mongodb)://[^:]+:[^@]+@", "db_url_with_password", "high"),
    # Generic API key patterns
    (r"api[_-]?key\s*=\s*[\"']?([A-Za-z0-9]{32,})", "api_key", "high"),
    # Generic secret patterns
    (r"secret\s*=\s*[\"']?([A-Za-z0-9]{32,})", "secret", "medium"),
]


# ─────────────────────────────────────────────────────────────────────
# GitHub integration via gh CLI
# ─────────────────────────────────────────────────────────────────────


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, pr_number).

    Examples:
        https://github.com/owner/repo/pull/123 → (owner, repo, 123)
        owner/repo#123 → (owner, repo, 123)
        owner/repo/pull/123 → (owner, repo, 123)
    """
    # Full URL
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    # Short form
    m = re.match(r"([^/]+)/([^/#]+)#(\d+)", pr_url)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    # owner/repo/pull/N
    m = re.match(r"([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    raise ValueError(f"Cannot parse PR URL: {pr_url}")


def fetch_pr_diff(pr_url: str, timeout: int = 30) -> str:
    """Fetch the unified diff for a PR using the gh CLI.

    Falls back to empty string if gh CLI is unavailable or auth fails.
    """
    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError:
        return ""

    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", f"{owner}/{repo}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return ""


# ─────────────────────────────────────────────────────────────────────
# Code-quality metrics
# ─────────────────────────────────────────────────────────────────────

# Thresholds (from plan)
MAX_FUNCTION_LINES = 50
MAX_FILE_LINES = 500
MAX_LINE_LENGTH = 120


@dataclass
class QualityFinding:
    """A code-quality issue found in the diff."""

    path: str
    line: int
    severity: str  # "info" | "warning" | "error"
    message: str


def check_function_length(diff: str) -> list[QualityFinding]:
    """Detect functions >MAX_FUNCTION_LINES lines."""
    findings: list[QualityFinding] = []
    current_file = None
    current_func = None
    func_start_line = 0
    func_lines = 0

    for i, line in enumerate(diff.splitlines()):
        # Track file
        if line.startswith("diff --git"):
            current_file = None
            current_func = None
            continue
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("---"):
            continue
        if not current_file:
            continue

        # Track function start (Python)
        m = re.match(r"\+\s*def\s+(\w+)", line)
        if m:
            if current_func and func_lines > MAX_FUNCTION_LINES:
                findings.append(
                    QualityFinding(
                        path=current_file,
                        line=func_start_line,
                        severity="warning",
                        message=f"Function `{current_func}` is {func_lines} lines (max {MAX_FUNCTION_LINES})",
                    )
                )
            current_func = m.group(1)
            func_start_line = i
            func_lines = 0
            continue

        # Count function body lines
        if current_func and line.startswith("+"):
            func_lines += 1

    # Check final function
    if current_func and func_lines > MAX_FUNCTION_LINES:
        findings.append(
            QualityFinding(
                path=current_file,
                line=func_start_line,
                severity="warning",
                message=f"Function `{current_func}` is {func_lines} lines (max {MAX_FUNCTION_LINES})",
            )
        )

    return findings


def check_file_length(diff: str) -> list[QualityFinding]:
    """Detect files >MAX_FILE_LINES."""
    findings: list[QualityFinding] = []
    current_file = None
    file_lines = 0

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            # Report previous file
            if current_file and file_lines > MAX_FILE_LINES:
                findings.append(
                    QualityFinding(
                        path=current_file,
                        line=0,
                        severity="warning",
                        message=f"File is {file_lines} lines added (max {MAX_FILE_LINES})",
                    )
                )
            current_file = line[6:]
            file_lines = 0
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if not current_file:
            continue
        if line.startswith("+"):
            file_lines += 1

    # Check final file
    if current_file and file_lines > MAX_FILE_LINES:
        findings.append(
            QualityFinding(
                path=current_file,
                line=0,
                severity="warning",
                message=f"File is {file_lines} lines added (max {MAX_FILE_LINES})",
            )
        )

    return findings


# Minimum lines of source code in a *new* file before the coverage heuristic
# fires. Below this, a new source file is trivial (one-liner, stub, docstring)
# and the "missing tests" signal is too noisy to escalate the verdict.
COVERAGE_MIN_NEW_SOURCE_LINES = 10


def check_test_coverage_heuristic(diff: str) -> list[QualityFinding]:
    """Detect if *new* source files were added without corresponding tests.

    Only flags files that are genuinely new (``--- /dev/null`` followed by
    ``+++ b/path``). Edits to existing files are intentionally ignored — they
    may already be well-tested and the diff may be a refactor, docstring, or
    style change where adding tests is not warranted.

    Within new files, only flags source files with at least
    ``COVERAGE_MIN_NEW_SOURCE_LINES`` added lines, so trivial stubs and
    one-liners don't pollute the verdict.
    """
    findings: list[QualityFinding] = []

    test_paths = {"test", "tests", "__tests__", "spec"}
    src_extensions = (".py", ".js", ".ts", ".java", ".go", ".rs")
    test_suffixes = (
        "_test.py",
        "Test.java",
        ".test.ts",
        ".test.js",
        ".spec.ts",
        ".spec.js",
    )

    def classify(path: str) -> str | None:
        if any(p in path.split("/") for p in test_paths) or path.endswith(
            test_suffixes
        ):
            return "test"
        if path.endswith(src_extensions):
            return "source"
        return None

    # First pass: identify genuinely-new files via the ``--- /dev/null`` marker
    # that precedes every newly-created file in unified diff. Track their class.
    new_source_files: dict[str, int] = {}  # path -> added lines (counted in pass 2)
    new_test_files: set[str] = set()
    prev_line = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path and path != "/dev/null" and prev_line.startswith("--- /dev/null"):
                kind = classify(path)
                if kind == "test":
                    new_test_files.add(path)
                elif kind == "source":
                    new_source_files.setdefault(path, 0)
        prev_line = line

    # Second pass: count added lines per new source file so we can apply the
    # minimum-size threshold. Cheap (diff already in memory).
    current_new_file: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            current_new_file = path if path in new_source_files else None
            continue
        if line.startswith("--- ") or line.startswith("diff "):
            continue
        if current_new_file and line.startswith("+") and not line.startswith("+++"):
            new_source_files[current_new_file] += 1

    # Flag new source files that crossed the line threshold without tests.
    if new_source_files and not new_test_files:
        for f in sorted(new_source_files):
            added = new_source_files[f]
            if added < COVERAGE_MIN_NEW_SOURCE_LINES:
                continue
            findings.append(
                QualityFinding(
                    path=f,
                    line=0,
                    severity="warning",
                    message=(
                        f"New source file added without corresponding tests "
                        f"({added} lines)"
                    ),
                )
            )

    return findings


# ─────────────────────────────────────────────────────────────────────
# Secret detection
# ─────────────────────────────────────────────────────────────────────


def detect_secrets(diff: str) -> list[QualityFinding]:
    """Scan the diff for hardcoded secrets."""
    findings: list[QualityFinding] = []

    current_file: str | None = None
    for i, line in enumerate(diff.splitlines()):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("---") or line.startswith("+++") or line.startswith("diff "):
            continue
        if not current_file:
            continue
        # Only scan added lines (lines starting with +)
        if not line.startswith("+"):
            continue

        # Strip the leading +
        content = line[1:]

        for pattern, secret_type, severity in SECRET_PATTERNS:
            if re.search(pattern, content):
                findings.append(
                    QualityFinding(
                        path=current_file,
                        line=i,
                        severity=severity,
                        message=f"Potential {secret_type} detected — DO NOT COMMIT",
                    )
                )
                break  # Don't report multiple types per line

    return findings


# ─────────────────────────────────────────────────────────────────────
# Verdict computation
# ─────────────────────────────────────────────────────────────────────


def compute_verdict(findings: list[QualityFinding]) -> tuple[str, str]:
    """Compute the review verdict from findings.

    Returns:
        (verdict, summary_markdown)
    """
    critical = [f for f in findings if f.severity == "critical"]
    high = [f for f in findings if f.severity == "high"]
    warnings = [f for f in findings if f.severity == "warning"]

    if critical:
        verdict = REQUEST_CHANGES
        summary_lines = [
            f"## ❌ {len(critical)} critical issue(s) — must fix before merge",
            "",
        ]
        for f in critical[:10]:
            summary_lines.append(f"- **`{f.path}:{f.line}`** — {f.message}")
        if high:
            summary_lines.append("")
            summary_lines.append(f"### High severity ({len(high)})")
            for f in high[:5]:
                summary_lines.append(f"- `{f.path}:{f.line}` — {f.message}")
        if warnings:
            summary_lines.append("")
            summary_lines.append(f"### Warnings ({len(warnings)})")
            for f in warnings[:5]:
                summary_lines.append(f"- `{f.path}:{f.line}` — {f.message}")
        return verdict, "\n".join(summary_lines)

    if high:
        verdict = REQUEST_CHANGES
        summary_lines = [
            f"## ❌ {len(high)} high-severity issue(s)",
            "",
        ]
        for f in high[:10]:
            summary_lines.append(f"- **`{f.path}:{f.line}`** — {f.message}")
        if warnings:
            summary_lines.append("")
            summary_lines.append(f"### Warnings ({len(warnings)})")
            for f in warnings[:5]:
                summary_lines.append(f"- `{f.path}:{f.line}` — {f.message}")
        return verdict, "\n".join(summary_lines)

    # Treat medium as warning-equivalent: it does not block the merge
    # (unlike critical/high) but should never silently disappear into APPROVE.
    # Without this branch, a credential leak detected with severity="medium"
    # (e.g. generic "secret = ..." pattern) would pass review unchecked.
    if any(f.severity == "medium" for f in findings):
        mediums = [f for f in findings if f.severity == "medium"]
        verdict = NEEDS_DISCUSSION
        summary_lines = [
            f"## 💬 {len(mediums)} medium-severity issue(s) — review recommended",
            "",
        ]
        for f in mediums[:10]:
            summary_lines.append(f"- `{f.path}:{f.line}` — {f.message}")
        if warnings:
            summary_lines.append("")
            summary_lines.append(f"### Warnings ({len(warnings)})")
            for f in warnings[:5]:
                summary_lines.append(f"- `{f.path}:{f.line}` — {f.message}")
        return verdict, "\n".join(summary_lines)

    if warnings:
        verdict = NEEDS_DISCUSSION
        summary_lines = [
            f"## 💬 {len(warnings)} warning(s) — review recommended",
            "",
        ]
        for f in warnings[:10]:
            summary_lines.append(f"- `{f.path}:{f.line}` — {f.message}")
        return verdict, "\n".join(summary_lines)

    return APPROVE, "## ✅ All checks passed — no issues found"


# ─────────────────────────────────────────────────────────────────────
# Real PRReviewer implementation
# ─────────────────────────────────────────────────────────────────────


class RealPRReviewer:
    """Real PR reviewer with secret detection, code-quality metrics, and test coverage check.

    Uses the `gh` CLI to fetch the PR diff. Falls back to empty diff if gh fails
    (returns NEEDS_DISCUSSION with a note explaining the failure).

    Optional ``registry`` (Gap 9 / Part B): a :class:`ReviewerRegistry`
    holding plugin contributions. The reviewer composes the registry
    once per ``review_pr()`` call and merges contributions into the
    built-in checks. Subclasses can still override ``SECRET_PATTERNS``
    or individual check functions for full-replacement customization;
    the registry composes with (does not replace) subclassing.
    """

    def __init__(
        self,
        timeout_seconds: int = 30,
        registry: Any | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.registry = registry

    def review_pr(self, pr_url: str) -> PRReviewResult:
        """Review the PR and return a structured result."""
        # Fetch diff
        diff = fetch_pr_diff(pr_url, timeout=self.timeout_seconds)

        if not diff:
            # gh CLI failed or auth missing
            return PRReviewResult(
                verdict=NEEDS_DISCUSSION,
                summary=(
                    f"⚠️ **Could not fetch PR diff** for `{pr_url}`\n\n"
                    "Possible causes:\n"
                    "- `gh` CLI not installed or not authenticated\n"
                    "- Network issue\n"
                    "- PR URL is invalid\n\n"
                    "Falling back to manual review (Michael should review)."
                ),
                inline_comments=[],
                metadata={"reviewer": "real", "error": "diff_fetch_failed"},
            )

        # Compose registry contributions once per review (Gap 9 / Part B).
        spec = self.registry.compose() if self.registry is not None else None

        # Run all checks -- built-in first, then registry-added checks.
        findings: list[QualityFinding] = []

        # Gap 11: fire HOOK_BEFORE_SECRET_SCAN (side-effect only; return ignored).
        fire_hook(HOOK_BEFORE_SECRET_SCAN, args=(diff,), spec=spec)

        findings.extend(self._detect_secrets_with_registry(diff, spec))

        # Gap 11: fire HOOK_BEFORE_QUALITY_CHECKS (side-effect only; return ignored).
        fire_hook(HOOK_BEFORE_QUALITY_CHECKS, args=(diff,), spec=spec)

        findings.extend(check_function_length(diff))
        findings.extend(check_file_length(diff))
        findings.extend(check_test_coverage_heuristic(diff))

        # Run registry-added custom checks (post built-ins).
        if spec is not None:
            for check in spec.checks:
                try:
                    extra = check(diff)
                    if extra:
                        findings.extend(extra)
                except Exception as exc:  # plugin crash isolation
                    findings.append(
                        QualityFinding(
                            path="<plugin-error>",
                            line=0,
                            severity="warning",
                            message=f"Plugin check failed: {exc}",
                        )
                    )

        # Compute verdict
        verdict, summary = compute_verdict(findings)

        # Build inline comments from findings
        inline_comments = [
            InlineComment(
                path=f.path,
                line=f.line if f.line > 0 else 1,
                body=f.message,
            )
            for f in findings
        ]

        metadata: dict[str, Any] = {
            "reviewer": "real",
            "pr_url": pr_url,
            "findings_count": len(findings),
            "critical_count": len([f for f in findings if f.severity == "critical"]),
            "high_count": len([f for f in findings if f.severity == "high"]),
            "warning_count": len([f for f in findings if f.severity == "warning"]),
        }
        # Only emit registry metadata when a registry is attached
        # (avoids "0 patterns" ambiguity for the no-registry case).
        if spec is not None:
            metadata["registry_pattern_count"] = len(spec.secret_patterns)
            metadata["registry_check_count"] = len(spec.checks)

        return PRReviewResult(
            verdict=verdict,
            summary=summary,
            inline_comments=inline_comments,
            metadata=metadata,
        )

    def _detect_secrets_with_registry(
        self, diff: str, spec: Any | None
    ) -> list[QualityFinding]:
        """Run built-in secret detection + registry-added patterns.

        When a registry is attached, its extra patterns are merged with
        the built-in patterns for this call. Registry patterns are
        scanned AFTER built-in patterns (so built-ins still catch
        common cases first).
        """
        findings = detect_secrets(diff)
        if spec is None:
            return findings

        current_file: str | None = None
        for i, line in enumerate(diff.splitlines()):
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if (
                line.startswith("---")
                or line.startswith("+++")
                or line.startswith("diff ")
            ):
                continue
            if not current_file:
                continue
            if not line.startswith("+"):
                continue
            content = line[1:]
            for pattern, secret_type, severity in spec.secret_patterns:
                if re.search(pattern, content):
                    findings.append(
                        QualityFinding(
                            path=current_file,
                            line=i,
                            severity=severity,
                            message=f"Potential {secret_type} detected (plugin) — DO NOT COMMIT",
                        )
                    )
                    break
        return findings
