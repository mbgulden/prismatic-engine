"""Prismatic Quality Gates — Phase 2 / Gap 5: Smoke Test Layer.

Verifies that agent output claims match filesystem reality. Catches:
- Agents that claim "I wrote file X" but X doesn't exist
- Agents that claim file X but X is empty / whitespace
- Agents that produce no output but mark themselves done
- Path traversal attempts in claimed paths

This is Layer 8 of the post-completion verification pipeline.

Reference: okf/operations/phase2-quality-gates-plan.md (Gap 5)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SmokeFinding:
    """One finding from the smoke test."""
    path: str
    status: str  # "missing" | "empty" | "whitespace_only" | "claimed_ok" | "traversal_attempt"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    """One finding from the filesystem verification helpers."""
    path: str
    passed: bool
    status: str  # "ok" | "missing" | "empty" | "whitespace_only" | "non-substantive" | "error"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)



@dataclass
class SmokeTestResult:
    """Result of the smoke test."""
    passed: bool
    claimed_paths: list[str] = field(default_factory=list)
    findings: list[SmokeFinding] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "claimed_paths": self.claimed_paths,
            "findings": [f.to_dict() for f in self.findings],
            "reason": self.reason,
        }

    def to_markdown(self) -> str:
        icon = "✅" if self.passed else "❌"
        lines = [
            f"## {icon} Smoke Test: {'PASS' if self.passed else 'FAIL'}",
            "",
            f"**Claimed paths:** {len(self.claimed_paths)}",
            f"**Findings:** {len(self.findings)}",
            f"**Reason:** {self.reason}",
            "",
        ]
        if self.findings:
            lines.append("### Findings")
            lines.append("")
            for f in self.findings[:20]:
                lines.append(f"- `{f.path}`: {f.status} — {f.detail}")
            if len(self.findings) > 20:
                lines.append(f"- …and {len(self.findings) - 20} more")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Claim extraction
# ─────────────────────────────────────────────────────────────────────


# Patterns for agent output claims. Each is a (regex, path_extractor) pair.
# We extract absolute paths AND repo-relative paths.
# Note: \w in Python regex matches [a-zA-Z0-9_], so we use [.\\w/-] to include dots
CLAIM_PATTERNS = [
    # "I created / wrote / edited / added / modified / touched <path>"
    # Allow leading ./, ../, and absolute paths. Allow paths WITHOUT extension
    # (e.g. /etc/passwd, .gitignore, README) by making extension optional.
    re.compile(
        r"\b(?:I\s+(?:created|wrote|edited|added|modified|touched|updated|generated)|created|wrote|edited|added|modified|touched|updated|generated)\s+"
        r"(?:a\s+(?:new\s+)?)?[`'\"]?((?:\.{0,2}/)?[\w./-]+)[`'\"]?",
        re.IGNORECASE,
    ),
    # "file: <path>" or "path: <path>"
    re.compile(
        r"\b(?:file|path|output|artifact)[:=]\s+[`'\"]?((?:\.{0,2}/)?[\w./-]+)[`'\"]?",
        re.IGNORECASE,
    ),
    # Backtick-wrapped paths (e.g. `prismatic/quality/gates.py` or `/etc/passwd`)
    re.compile(
        r"`((?:\.{0,2}/)?[\w./-]+)`",
    ),
    # "and <path>" or "also <path>" — picks up secondary claims
    re.compile(
        r"\b(?:and|also|plus)\s+[`'\"]?((?:\.{0,2}/)?[\w./-]+\.\w+)[`'\"]?",
        re.IGNORECASE,
    ),
]


# Patterns indicating path traversal attempts
TRAVERSAL_PATTERN = re.compile(r"\.\.[\\/]|[\\/]\.\.")


def extract_claimed_paths(agent_output: str) -> list[str]:
    """Extract file paths claimed in the agent's narrative output.

    Args:
        agent_output: The agent's "what I did" narrative

    Returns:
        Deduplicated list of claimed paths (may include repo-relative paths)
    """
    if not agent_output:
        return []

    claimed = set()
    for pattern in CLAIM_PATTERNS:
        for match in pattern.finditer(agent_output):
            path = match.group(1)
            # Filter out URLs, very short paths, and obvious noise
            if len(path) < 4:
                continue
            if path.startswith(("http://", "https://", "ftp://")):
                continue
            # Skip if it looks like a version string or pure number
            if re.match(r"^\d+\.\d+", path):
                continue
            # Skip git commit SHAs (40 hex chars in backticks is common in agent logs)
            # Per PR #36 review: hashes like 6c6ee9526abc123... were being extracted as paths
            if re.match(r"^[0-9a-f]{20,}$", path, re.IGNORECASE):
                continue
            claimed.add(path)

    return sorted(claimed)


def is_path_traversal(path: str) -> bool:
    """Check if a path contains traversal sequences (..\\ or ../)."""
    return bool(TRAVERSAL_PATTERN.search(path))


# ─────────────────────────────────────────────────────────────────────
# Filesystem verification
# ─────────────────────────────────────────────────────────────────────


# Thresholds for "substantive" content
MIN_FILE_SIZE = 10  # bytes — anything less is probably empty/error
MIN_SUBSTANTIVE_CHARS = 20  # chars after stripping whitespace/comments


def file_exists(path: str, workdir: str = ".") -> bool:
    """Check if a file exists, resolving relative paths against workdir."""
    try:
        full_path = Path(path) if os.path.isabs(path) else Path(workdir) / path
        return full_path.exists() and full_path.is_file()
    except (OSError, ValueError):
        return False


def file_has_substantive_content(path: str, workdir: str = ".") -> tuple[bool, str]:
    """Check if a file has substantive (non-whitespace, non-comment-only) content.

    Returns:
        (has_content, detail_string)
    """
    try:
        full_path = Path(path) if os.path.isabs(path) else Path(workdir) / path
        if not full_path.exists():
            return False, "file does not exist"

        size = full_path.stat().st_size
        if size == 0:
            return False, "empty file (0 bytes)"
        if size < MIN_FILE_SIZE:
            return False, f"suspiciously small ({size} bytes)"

        # Read raw bytes first to detect binary files
        try:
            raw_bytes = full_path.read_bytes()
        except OSError as e:
            return False, f"error reading file: {e}"

        # Detect binary: if 1% or more of bytes are null bytes, it's binary
        null_count = raw_bytes.count(b"\x00")
        if size > 0 and null_count / size >= 0.01:
            return True, f"binary file ({size} bytes, {null_count} null bytes)"

        # Try to decode as text
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Partial decode — treat as binary
            return True, f"binary file ({size} bytes, partial UTF-8 decode)"

        # Strip whitespace and comments
        stripped = re.sub(r"^\s*#.*$", "", content, flags=re.MULTILINE)  # Python comments
        stripped = re.sub(r"^\s*//.*$", "", stripped, flags=re.MULTILINE)  # JS comments
        stripped = stripped.strip()

        if len(stripped) < MIN_SUBSTANTIVE_CHARS:
            return False, f"only {len(stripped)} chars after stripping comments"

        return True, f"{len(stripped)} chars"

    except (OSError, ValueError) as e:
        return False, f"error reading file: {e}"


# ─────────────────────────────────────────────────────────────────────
# Main smoke_test function
# ─────────────────────────────────────────────────────────────────────


def smoke_test(agent_output: str, workdir: str = ".") -> SmokeTestResult:
    """Verify that agent output claims match filesystem reality.

    This is Layer 8 of the post-completion verification pipeline.
    Catches:
    - Agents that claim "I wrote file X" but X doesn't exist (LIES)
    - Agents that produce no output but mark themselves done (VACUOUS)
    - Path traversal attempts in claimed paths (SECURITY)

    Args:
        agent_output: The agent's narrative output ("I created X...")
                       NOTE: This should be the agent's RESULT/WALKTHROUGH section,
                       not the launcher log file. Launcher logs contain only
                       `[launcher] ...` metadata, which produces vacuous passes.
                       See okf/operations/pr36-review-feedback.md for details.
        workdir: Repository root for resolving relative paths

    Returns:
        SmokeTestResult with pass/fail and detailed findings
    """
    # Extract claimed paths
    claimed = extract_claimed_paths(agent_output)

    findings: list[SmokeFinding] = []

    if not claimed:
        # No claims made — vacuous success is suspicious but not a hard fail.
        # Mark as PASS but flag for review (consistent with how Verdict handles it).
        return SmokeTestResult(
            passed=True,
            claimed_paths=[],
            findings=[],
            reason="No file claims detected — agent output may be vacuous (review recommended)",
        )

    # Check each claimed path
    for path in claimed:
        # Security check: path traversal attempt
        if is_path_traversal(path):
            findings.append(SmokeFinding(
                path=path,
                status="traversal_attempt",
                detail="Path contains '..' — possible path traversal attack",
            ))
            continue

        # Existence check
        if not file_exists(path, workdir):
            findings.append(SmokeFinding(
                path=path,
                status="missing",
                detail=f"Claimed file does not exist on disk (workdir={workdir})",
            ))
            continue

        # Content check
        has_content, detail = file_has_substantive_content(path, workdir)
        if not has_content:
            findings.append(SmokeFinding(
                path=path,
                status="empty" if "empty" in detail else "whitespace_only",
                detail=detail,
            ))
            continue

        findings.append(SmokeFinding(
            path=path,
            status="claimed_ok",
            detail=detail,
        ))

    # Compute verdict
    missing_or_empty = [f for f in findings if f.status in ("missing", "empty", "whitespace_only")]
    traversal_attempts = [f for f in findings if f.status == "traversal_attempt"]

    if traversal_attempts:
        # Path traversal is a security issue — always fail
        passed = False
        reason = f"Path traversal attempt detected in {len(traversal_attempts)} claimed path(s)"
    elif missing_or_empty:
        passed = False
        reason = f"{len(missing_or_empty)} of {len(claimed)} claimed file(s) are missing or empty"
    else:
        passed = True
        reason = f"All {len(claimed)} claimed file(s) verified"

    return SmokeTestResult(
        passed=passed,
        claimed_paths=claimed,
        findings=findings,
        reason=reason,
    )


def verify_files_exist(paths: list[str | Path], workdir: str = ".") -> list[Finding]:
    """Check if each file exists, resolving relative paths against workdir."""
    findings = []
    for p in paths:
        path_str = str(p)
        try:
            full_path = Path(p) if os.path.isabs(path_str) else Path(workdir) / p
            if full_path.exists() and full_path.is_file():
                findings.append(Finding(path=path_str, passed=True, status="ok", detail="File exists"))
            else:
                findings.append(Finding(path=path_str, passed=False, status="missing", detail="File does not exist or is not a file"))
        except Exception as e:
            findings.append(Finding(path=path_str, passed=False, status="error", detail=f"Error checking file existence: {e}"))
    return findings


def verify_files_nonempty(paths: list[str | Path], workdir: str = ".") -> list[Finding]:
    """Check if each file exists and is larger than 100 bytes."""
    findings = []
    for p in paths:
        path_str = str(p)
        try:
            full_path = Path(p) if os.path.isabs(path_str) else Path(workdir) / p
            if not full_path.exists() or not full_path.is_file():
                findings.append(Finding(path=path_str, passed=False, status="missing", detail="File does not exist or is not a file"))
                continue

            size = full_path.stat().st_size
            if size > 100:
                findings.append(Finding(path=path_str, passed=True, status="ok", detail=f"File size is {size} bytes (> 100 bytes)"))
            else:
                findings.append(Finding(path=path_str, passed=False, status="empty", detail=f"File size is {size} bytes (<= 100 bytes)"))
        except Exception as e:
            findings.append(Finding(path=path_str, passed=False, status="error", detail=f"Error checking file size: {e}"))
    return findings


def verify_files_substantive(paths: list[str | Path], workdir: str = ".") -> list[Finding]:
    """Check if each file exists and has > 50 characters of substantive content after stripping comments and whitespace."""
    findings = []
    for p in paths:
        path_str = str(p)
        try:
            full_path = Path(p) if os.path.isabs(path_str) else Path(workdir) / p
            if not full_path.exists() or not full_path.is_file():
                findings.append(Finding(path=path_str, passed=False, status="missing", detail="File does not exist or is not a file"))
                continue

            try:
                content = full_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                findings.append(Finding(path=path_str, passed=False, status="error", detail=f"Error reading file as text: {e}"))
                continue

            # Remove block comments first
            content_no_comments = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            content_no_comments = re.sub(r"<!--.*?-->", "", content_no_comments, flags=re.DOTALL)
            # Remove line comments
            content_no_comments = re.sub(r"#.*", "", content_no_comments)
            content_no_comments = re.sub(r"//.*", "", content_no_comments)

            # Strip all whitespace to get true content character count
            content_no_ws = re.sub(r"\s+", "", content_no_comments)
            char_count = len(content_no_ws)

            if char_count > 50:
                findings.append(Finding(path=path_str, passed=True, status="ok", detail=f"Substantive content is {char_count} chars (> 50 chars)"))
            else:
                findings.append(Finding(path=path_str, passed=False, status="non-substantive", detail=f"Substantive content is only {char_count} chars (<= 50 chars)"))
        except Exception as e:
            findings.append(Finding(path=path_str, passed=False, status="error", detail=f"Error verifying file content: {e}"))
    return findings