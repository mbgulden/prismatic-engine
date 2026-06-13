#!/usr/bin/env python3
"""
Credential Redaction Scanner (GRO-63)

Scans files/directories for hardcoded credentials using regex patterns.
Reports findings and optionally redacts with automatic .bak backups.

Patterns covered:
  - OpenAI / AI API keys (sk-, sk-or-, sk-admin-, sk-proj-, etc.)
  - GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
  - Linear API keys (lin_api_)
  - Google API keys (AIza...)
  - AWS access keys (AKIA..., ASIA...)
  - Generic Bearer/JWT tokens
  - Private key blocks (PEM, SSH)
  - Slack tokens
  - Database connection strings with credentials
  - Hardcoded password assignments
  - Generic API key / secret patterns

Usage:
  python credential-scanner.py scan /path/to/dir           # Scan only
  python credential-scanner.py redact /path/to/dir         # Scan + redact (with .bak)
  python credential-scanner.py scan /path/to/dir --json report.json  # JSON output
  python credential-scanner.py scan /path/to/dir --verbose            # Show context lines

Author: Hermes Agent (Nous Research)
Date: 2026-05-29
"""

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Pattern Definitions ──────────────────────────────────────────────────────

@dataclass
class CredentialPattern:
    name: str
    regex: str
    severity: str          # "critical", "high", "medium", "low"
    description: str
    mask_group: int = 1    # Which capture group contains the secret to redact
    redact_template: str = "***REDACTED-{name}***"


CREDENTIAL_PATTERNS: list[CredentialPattern] = [
    # ── AI / LLM API keys ───────────────────────────────────────────────
    CredentialPattern(
        name="openai-api-key",
        regex=r'(sk-(?:or-|admin-|proj-|svcacct-|ant-)?[A-Za-z0-9+/=_]{20,})',
        severity="critical",
        description="OpenAI API key (sk-...) — project, admin, or service account variants",
    ),
    CredentialPattern(
        name="anthropic-api-key",
        regex=r'(sk-ant-[A-Za-z0-9+/=_]{20,})',
        severity="critical",
        description="Anthropic/Claude API key",
    ),
    CredentialPattern(
        name="google-ai-api-key",
        regex=r'(AIza[0-9A-Za-z\-_]{35})',
        severity="critical",
        description="Google Generative AI / Firebase API key",
    ),
    CredentialPattern(
        name="groq-api-key",
        regex=r'(gsk_[A-Za-z0-9]{30,60})',
        severity="critical",
        description="Groq API key",
    ),
    CredentialPattern(
        name="cohere-api-key",
        regex=r'([A-Za-z0-9]{32,48})\b.*?\bcohere',
        severity="high",
        description="Possible Cohere API key (contextual)",
    ),

    # ── GitHub tokens ───────────────────────────────────────────────────
    CredentialPattern(
        name="github-personal-token",
        regex=r'(ghp_[A-Za-z0-9]{36,40})',
        severity="critical",
        description="GitHub Personal Access Token (classic)",
    ),
    CredentialPattern(
        name="github-oauth-token",
        regex=r'(gho_[A-Za-z0-9]{36,40})',
        severity="critical",
        description="GitHub OAuth access token",
    ),
    CredentialPattern(
        name="github-app-token",
        regex=r'(ghu_[A-Za-z0-9]{36,40})',
        severity="critical",
        description="GitHub user-to-server token",
    ),
    CredentialPattern(
        name="github-server-token",
        regex=r'(ghs_[A-Za-z0-9]{36,40})',
        severity="critical",
        description="GitHub server-to-server token",
    ),
    CredentialPattern(
        name="github-refresh-token",
        regex=r'(ghr_[A-Za-z0-9]{36,40})',
        severity="critical",
        description="GitHub refresh token",
    ),
    CredentialPattern(
        name="github-fine-grained-token",
        regex=r'(github_pat_[A-Za-z0-9_]{30,90})',
        severity="critical",
        description="GitHub fine-grained personal access token",
    ),

    # ── Linear ──────────────────────────────────────────────────────────
    CredentialPattern(
        name="linear-api-key",
        regex=r'(lin_api_[A-Za-z0-9]{30,50})',
        severity="critical",
        description="Linear API key",
    ),

    # ── AWS ─────────────────────────────────────────────────────────────
    CredentialPattern(
        name="aws-access-key",
        regex=r'(AKIA[0-9A-Z]{16})',
        severity="critical",
        description="AWS IAM access key ID",
    ),
    CredentialPattern(
        name="aws-secret-key",
        regex=r'aws[_\-\s]?(?:secret|secretkey|secret_access_key)[_\-\s]?[=:]\s*[\'"]?([A-Za-z0-9/+=]{30,60})[\'"]?',
        severity="critical",
        description="AWS secret access key in assignment",
    ),

    # ── Slack ───────────────────────────────────────────────────────────
    CredentialPattern(
        name="slack-bot-token",
        regex=r'(xoxb-[0-9]{10,15}-[0-9]{10,15}-[A-Za-z0-9]{20,30})',
        severity="critical",
        description="Slack bot token",
    ),
    CredentialPattern(
        name="slack-user-token",
        regex=r'(xoxp-[0-9]{10,15}-[0-9]{10,15}-[A-Za-z0-9]{20,30})',
        severity="critical",
        description="Slack user token",
    ),
    CredentialPattern(
        name="slack-webhook",
        regex=r'(https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{20,30})',
        severity="critical",
        description="Slack incoming webhook URL",
    ),

    # ── Private Keys ────────────────────────────────────────────────────
    CredentialPattern(
        name="private-key-pem",
        regex=r'(-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----(?:(?!-----END).)+-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----)',
        severity="critical",
        description="PEM-encoded private key block",
    ),
    CredentialPattern(
        name="ssh-private-key",
        regex=r'(-----BEGIN\s+OPENSSH\s+PRIVATE\s+KEY-----(?:(?!-----END).)+-----END\s+OPENSSH\s+PRIVATE\s+KEY-----)',
        severity="critical",
        description="OpenSSH private key",
    ),

    # ── Database Connection Strings ─────────────────────────────────────
    CredentialPattern(
        name="db-connection-string",
        regex=r'(?:mongodb|postgres|mysql|postgresql|redis)://[^/\s]+:[^@\s]+@[^\s"\']+',
        severity="high",
        description="Database connection string with embedded credentials",
    ),

    # ── Bearer / JWT Tokens ─────────────────────────────────────────────
    CredentialPattern(
        name="jwt-token",
        regex=r'(eyJ[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{10,})',
        severity="high",
        description="JWT token (observed in source code)",
    ),
    CredentialPattern(
        name="generic-bearer-token",
        regex=r'(?:bearer|token|auth)[\s:=]+[\'"]?([A-Za-z0-9+/=_-]{40,})[\'"]?',
        severity="high",
        description="Generic bearer token or auth header value",
    ),

    # ── Generic Secrets ─────────────────────────────────────────────────
    CredentialPattern(
        name="password-assignment",
        regex=r'(?:password|passwd|pwd|secret)\s*[=:]\s*[\'"]([^\'"]{4,60})[\'"]',
        severity="high",
        description="Hardcoded password/secret assignment in config or source",
    ),
    CredentialPattern(
        name="generic-api-key",
        regex=r'(?:api[_\-\s]?key|apikey|api_secret|secret_key)\s*[=:]\s*[\'"]([A-Za-z0-9+/=_-]{16,80})[\'"]',
        severity="high",
        description="Generic API key or secret assignment",
    ),
    CredentialPattern(
        name="oauth-client-secret",
        regex=r'(?:client[_\-\s]?secret|oauth[_\-\s]?secret)\s*[=:]\s*[\'"]([A-Za-z0-9+/=._\-]{16,80})[\'"]',
        severity="high",
        description="OAuth client secret in config or source",
    ),

    # ── Other common services ───────────────────────────────────────────
    CredentialPattern(
        name="stripe-api-key",
        regex=r'(sk_live_[A-Za-z0-9]{24,40})',
        severity="critical",
        description="Stripe live secret key",
    ),
    CredentialPattern(
        name="stripe-test-key",
        regex=r'(sk_test_[A-Za-z0-9]{24,40})',
        severity="medium",
        description="Stripe test secret key",
    ),
    CredentialPattern(
        name="twilio-api-key",
        regex=r'(SK[0-9a-fA-F]{32})',
        severity="critical",
        description="Twilio API key (SK...)",
    ),
    CredentialPattern(
        name="telegram-bot-token",
        regex=r'(\d{8,10}:[A-Za-z0-9\-_]{30,45})',
        severity="high",
        description="Telegram bot token (numeric:alphanumeric)",
    ),
]


# ── File classification ──────────────────────────────────────────────────────

TEXT_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".rb", ".java",
    ".kt", ".swift", ".c", ".cpp", ".h", ".hpp", ".cs", ".php",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".envrc", ".env.example", ".env.template",
    ".md", ".txt", ".rst", ".tex", ".log",
    ".html", ".css", ".scss", ".xml", ".svg",
    ".tf", ".tfvars", ".hcl",
    ".sql", ".graphql",
    ".dockerfile", "dockerfile", ".dockerignore",
    ".gitignore", ".gitattributes",
    ".makefile", "makefile",
    ".nix", ".lua", ".vim", ".vimrc",
    ".csv", ".tsv",
    ".proto",
}

EXCLUDED_DIRS: set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", ".cache",
    "vendor", "bower_components", ".terraform",
    ".idea", ".vscode", "target",  # IDE / build
}

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB — skip larger files


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Finding:
    file_path: str
    line_number: int
    pattern_name: str
    severity: str
    matched_text: str
    context_line: str
    redacted: bool = False


@dataclass
class ScanReport:
    scan_root: str
    scan_time: str
    mode: str  # "scan" or "redact"
    total_files_scanned: int
    total_findings: int
    findings: list[dict] = field(default_factory=list)
    summary_by_severity: dict = field(default_factory=dict)
    redacted_files: list[str] = field(default_factory=list)


# ── Core Logic ───────────────────────────────────────────────────────────────

def is_text_file(filepath: Path) -> bool:
    """Check if file is likely a text file based on extension or content sniffing."""
    suffix = filepath.suffix.lower()
    name = filepath.name.lower()

    if suffix in TEXT_EXTENSIONS or name in TEXT_EXTENSIONS:
        return True

    # No extension — try content sniffing
    if not suffix:
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(1024)
            # Text files typically have no null bytes
            return b"\x00" not in chunk
        except (OSError, PermissionError):
            return False

    return False


def should_skip_path(path: Path) -> bool:
    """Check if a path should be excluded from scanning."""
    parts = set(path.parts)
    if parts & EXCLUDED_DIRS:
        return True
    # Skip hidden files/dirs (except .env files)
    for part in path.parts:
        if part.startswith(".") and part not in (".env", ".envrc", ".env.example", ".env.template", ".gitignore", ".gitattributes", ".dockerignore"):
            if part != ".":  # don't skip the root "."
                return True
    return False


def scan_file(filepath: Path, patterns: list[CredentialPattern]) -> list[Finding]:
    """Scan a single file for credential patterns."""
    findings: list[Finding] = []

    try:
        stat = filepath.stat()
        if stat.st_size > MAX_FILE_SIZE_BYTES:
            return findings
    except OSError:
        return findings

    if not is_text_file(filepath):
        return findings

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, PermissionError, UnicodeDecodeError):
        return findings

    for line_num, line in enumerate(lines, start=1):
        # Skip comment lines for some lower-severity patterns to reduce noise
        stripped = line.strip()
        is_comment = stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*")

        for pattern in patterns:
            # For high/critical severity, always check even in comments
            # For medium/low, skip comment-only lines to reduce false positives
            if pattern.severity in ("medium", "low") and is_comment:
                # Still check — comments can contain real tokens pasted accidentally
                pass

            # Use DOTALL for private key blocks (multiline)
            flags = re.DOTALL if "PRIVATE KEY" in pattern.regex else 0

            for match in re.finditer(pattern.regex, line if not flags else "\n".join(lines), flags):
                matched_text = match.group(1) if match.lastindex and match.lastindex >= 1 else match.group(0)

                # Skip if matched text is shorter than 6 chars (likely false positive)
                if len(matched_text) < 6:
                    continue

                # Skip known false-positive patterns
                if is_false_positive(matched_text, pattern.name):
                    continue

                findings.append(Finding(
                    file_path=str(filepath),
                    line_number=line_num,
                    pattern_name=pattern.name,
                    severity=pattern.severity,
                    matched_text=matched_text,
                    context_line=line.rstrip(),
                ))

    return findings


def is_false_positive(text: str, pattern_name: str) -> bool:
    """Filter common false positives."""
    text_lower = text.lower()

    # Skip obviously placeholder/dummy values
    placeholders = {
        "your_api_key", "your_token", "your_secret", "your_password",
        "placeholder", "example_api_key", "test_token_here",
        "1234567890:abcdefghijklmnopqrstuvwxyz",  # common Telegram example
        "***", "xxxxx", "<your", "<token", "change_me",
        "password123", "admin123", "test123",
    }
    for ph in placeholders:
        if ph in text_lower:
            return True

    # Skip if it's clearly a variable name not a value
    if pattern_name == "generic-api-key" and text in ("api_key", "apikey", "API_KEY"):
        return True

    # Skip common environment variable references
    if text.startswith("${") or text.startswith("$("):
        return True

    return False


def scan_directory(
    root: Path,
    patterns: list[CredentialPattern],
    redact: bool = False,
    verbose: bool = False,
) -> tuple[list[Finding], list[str]]:
    """Recursively scan a directory for credential patterns."""
    all_findings: list[Finding] = []
    redacted_files: list[str] = []
    files_scanned = 0

    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        if should_skip_path(entry):
            continue

        findings = scan_file(entry, patterns)
        files_scanned += 1

        if findings:
            all_findings.extend(findings)

            if redact:
                success = redact_file(entry, findings)
                if success:
                    redacted_files.append(str(entry))

            if verbose:
                print(f"  {entry}: {len(findings)} finding(s)")

    return all_findings, redacted_files


def redact_file(filepath: Path, findings: list[Finding]) -> bool:
    """Redact findings in a file, creating a .bak backup first."""
    backup_path = filepath.with_suffix(filepath.suffix + ".bak")

    try:
        # Create backup
        shutil.copy2(filepath, backup_path)

        # Read file
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            lines = content.splitlines(keepends=True)

        # Build replacement map: line_number -> [(old_text, new_text)]
        replacements: dict[int, list[tuple[str, str]]] = {}
        for fnd in findings:
            if fnd.line_number not in replacements:
                replacements[fnd.line_number] = []
            redacted = f"***REDACTED-{fnd.pattern_name}***"
            replacements[fnd.line_number].append((fnd.matched_text, redacted))

        # Apply replacements (process lines in reverse to avoid offset issues on same line)
        for line_idx, reps in replacements.items():
            if 1 <= line_idx <= len(lines):
                line = lines[line_idx - 1]
                for old_text, new_text in reps:
                    line = line.replace(old_text, new_text)
                lines[line_idx - 1] = line

        # Write back
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return True

    except (OSError, PermissionError) as e:
        print(f"  ERROR: Could not redact {filepath}: {e}", file=sys.stderr)
        return False


def build_report(
    scan_root: str,
    findings: list[Finding],
    redacted_files: list[str],
    mode: str,
) -> ScanReport:
    """Build a structured scan report."""
    severity_counts: dict[str, int] = {}
    for f in findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    return ScanReport(
        scan_root=scan_root,
        scan_time=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        total_files_scanned=0,  # filled in by caller
        total_findings=len(findings),
        findings=[asdict(f) for f in findings],
        summary_by_severity=severity_counts,
        redacted_files=redacted_files,
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Credential Redaction Scanner — detect and redact hardcoded secrets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s scan /home/user/project
  %(prog)s redact /home/user/project
  %(prog)s scan /home/user/project --json report.json
  %(prog)s scan /home/user/project --verbose --severity critical,high
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── scan subcommand ──
    scan_parser = subparsers.add_parser("scan", help="Scan for credentials (report only)")
    scan_parser.add_argument("path", type=str, help="Directory or file to scan")
    scan_parser.add_argument("--json", type=str, metavar="FILE", help="Write JSON report to FILE")
    scan_parser.add_argument("--verbose", "-v", action="store_true", help="Show per-file progress")
    scan_parser.add_argument("--severity", type=str, metavar="LEVELS",
                             help="Comma-separated severity levels (critical,high,medium,low). Default: all")
    scan_parser.add_argument("--pattern", type=str, action="append", metavar="NAME",
                             help="Only run specific pattern(s). Repeatable. Use 'list' to show all pattern names.")

    # ── redact subcommand ──
    redact_parser = subparsers.add_parser("redact", help="Scan and redact credentials (creates .bak backups)")
    redact_parser.add_argument("path", type=str, help="Directory or file to scan and redact")
    redact_parser.add_argument("--json", type=str, metavar="FILE", help="Write JSON report to FILE")
    redact_parser.add_argument("--verbose", "-v", action="store_true", help="Show per-file progress")
    redact_parser.add_argument("--severity", type=str, metavar="LEVELS",
                               help="Comma-separated severity levels. Default: all")
    redact_parser.add_argument("--pattern", type=str, action="append", metavar="NAME",
                               help="Only run specific pattern(s). Repeatable.")
    redact_parser.add_argument("--dry-run", action="store_true", help="Report-only, don't actually redact")
    redact_parser.add_argument("--no-backup", action="store_true", help="Skip .bak backup (DANGEROUS)")

    args = parser.parse_args()

    target_path = Path(args.path).expanduser().resolve()
    if not target_path.exists():
        print(f"Error: Path does not exist: {target_path}", file=sys.stderr)
        sys.exit(1)

    # ── Pattern selection ──
    patterns = CREDENTIAL_PATTERNS

    # --pattern list
    if args.pattern and "list" in args.pattern:
        print("Available patterns:")
        for p in patterns:
            print(f"  {p.name:35s} [{p.severity:8s}] {p.description}")
        sys.exit(0)

    # --pattern filter
    if args.pattern:
        pattern_names = set(args.pattern)
        patterns = [p for p in patterns if p.name in pattern_names]
        if not patterns:
            print(f"Error: No matching patterns for: {args.pattern}", file=sys.stderr)
            sys.exit(1)

    # --severity filter
    if args.severity:
        severity_set = set(s.strip().lower() for s in args.severity.split(","))
        patterns = [p for p in patterns if p.severity.lower() in severity_set]

    # ── Determine mode ──
    do_redact = args.command == "redact" and not args.dry_run
    mode = "redact" if do_redact else "scan"
    if args.command == "redact" and args.dry_run:
        mode = "scan (dry-run)"

    print(f"\n{'='*60}")
    print(f"  Credential Redaction Scanner")
    print(f"  Mode: {mode}")
    print(f"  Target: {target_path}")
    print(f"  Patterns active: {len(patterns)}")
    print(f"{'='*60}\n")

    # ── Scan ──
    if target_path.is_file():
        findings = scan_file(target_path, patterns)
        redacted_files = []
        if do_redact and findings:
            success = redact_file(target_path, findings)
            if success:
                redacted_files = [str(target_path)]
        total_files = 1
    else:
        findings, redacted_files = scan_directory(
            target_path, patterns, redact=do_redact, verbose=args.verbose
        )
        # Count files scanned
        total_files = sum(1 for _ in target_path.rglob("*") if _.is_file() and not should_skip_path(_))

    # ── Report ──
    report = build_report(str(target_path), findings, redacted_files, mode)
    report.total_files_scanned = total_files

    # ── Print summary ──
    if findings:
        print(f"\n  ⚠  CREDENTIALS FOUND: {len(findings)}")
        print(f"  ────────────────────────────────────────")

        # Group by severity
        for sev in ("critical", "high", "medium", "low"):
            group = [f for f in findings if f.severity == sev]
            if group:
                icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(sev, "  ")
                print(f"\n  {icon} {sev.upper()} ({len(group)} findings):")
                for f in group:
                    # Truncate matched text for display
                    display_text = f.matched_text
                    if len(display_text) > 60:
                        display_text = display_text[:57] + "..."
                    status = " [REDACTED]" if f.redacted else ""
                    print(f"      {f.file_path}:{f.line_number}  [{f.pattern_name}]")
                    if not do_redact:
                        print(f"        → {display_text}")

        if redacted_files:
            print(f"\n  ✅ REDACTED FILES ({len(redacted_files)}):")
            for rf in redacted_files:
                print(f"      {rf}  (backup: {rf}.bak)")

    else:
        print(f"\n  ✅ No credentials found. Clean!")

    # ── JSON output ──
    if args.json:
        with open(args.json, "w") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"\n  📄 JSON report written: {args.json}")

    print(f"\n  Files scanned: {total_files}")
    print(f"  Findings: {len(findings)}")
    print()

    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
