#!/usr/bin/env python3
"""Credential scanner script for GRO-4111.

Scans the codebase (specifically runtime paths) to prove no embedded usable credentials exist.
Outputs results to both stdout and a scan log.
Findings never include matched values, source lines, or guessable fingerprints.
"""

import argparse
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

# Config
WORKSPACE_DIR = Path(__file__).parent.parent.resolve()
RUNTIME_DIR = WORKSPACE_DIR / "prismatic"
DEFAULT_LOG_FILE = WORKSPACE_DIR / "reports" / "credential_scan.log"

# Known default keys we must ensure are not embedded in runtime files
# Replaced with SHA-256 digests
BANNED_DIGESTS = {
    # RULE_DEFAULT_CREDENTIAL
    "cc2a98d21f650abea957919da03e23c2abd237a818404f20da53307365ecbc80",
    # RULE_DEFAULT_CREDENTIAL
    "0e8b7add6969d7432ba2505c179e699c4505eda2bdbd86abc0969986d264598a",
    # RULE_DEFAULT_CREDENTIAL
    "071318fcf4dd9fc808115e015f07ca210e2d2660ce68e78c8549d95464950e4b",
    # RULE_DEFAULT_CREDENTIAL
    "54197827177a17447de6890c2cb43100059438b9fc0285fc302c1b68847b530a",
}

# Regex patterns for potential embedded secrets
POTENTIAL_SECRET_PATTERNS = [
    re.compile(
        r'(?i)(secret|token|password|api_key|auth_key|private_key)\s*=\s*["\']([a-zA-Z0-9_\-]{8,})["\']'
    ),
]


def scan_file(file_path: Path) -> list:
    issues = []
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"ERROR: Could not read file: {e}"]

    try:
        rel_path = file_path.relative_to(WORKSPACE_DIR)
    except ValueError:
        rel_path = file_path

    # 1. Check for banned default credentials using digests (generic word rules)
    tokens = re.findall(r"[a-zA-Z0-9_\-]{8,}", content)
    for token in tokens:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if token_hash in BANNED_DIGESTS:
            lines = content.splitlines()
            for idx, line in enumerate(lines, 1):
                if token in line:
                    # Non-guessable fingerprint (hash of location rather than value)
                    fingerprint = hashlib.sha256(
                        f"{rel_path}:{idx}".encode()
                    ).hexdigest()[:16]
                    issues.append(
                        {
                            "path": str(rel_path),
                            "line": idx,
                            "rule_id": "RULE_DEFAULT_CREDENTIAL",
                            "fingerprint": fingerprint,
                        }
                    )
                    break

    # 2. Check for other hardcoded secrets
    for pattern in POTENTIAL_SECRET_PATTERNS:
        for match in pattern.finditer(content):
            var_name, val = match.groups()
            start_pos = match.start()
            line_no = content[:start_pos].count("\n") + 1
            # Non-guessable fingerprint (hash of location)
            fingerprint = hashlib.sha256(
                f"{rel_path}:{line_no}".encode()
            ).hexdigest()[:16]
            issues.append(
                {
                    "path": str(rel_path),
                    "line": line_no,
                    "rule_id": "RULE_POTENTIAL_SECRET",
                    "fingerprint": fingerprint,
                }
            )

    return issues


def write_log_atomically(log_path: Path, content: str):
    log_path = Path(log_path).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Create a temporary file in the destination directory with 0600 mode
    fd, temp_path_str = tempfile.mkstemp(
        dir=str(log_path.parent), prefix="scan_tmp_", suffix=".log"
    )
    temp_path = Path(temp_path_str)
    try:
        # Set 0600 explicitly
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # 2. Atomically rename/replace
        temp_path.replace(log_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    # 3. Finalize the claimed result log read-only (0400)
    os.chmod(log_path, 0o400)


def main():
    parser = argparse.ArgumentParser(
        description="Scan codebase for embedded credentials."
    )
    parser.add_argument("target_dir", nargs="?", default=None, help="Directory to scan")
    parser.add_argument(
        "--log-path", default=None, help="Explicit log path to save the scan report"
    )
    args = parser.parse_args()

    target_dir = RUNTIME_DIR
    if args.target_dir:
        target_dir = Path(args.target_dir).resolve()

    log_path = DEFAULT_LOG_FILE
    if args.log_path:
        log_path = Path(args.log_path).resolve()

    print(f"Starting credential-pattern scan over runtime path: {target_dir}")

    log_messages = []
    log_messages.append("====================================================")
    log_messages.append("PRISMATIC ENGINE RUNTIME CREDENTIAL SCAN LOG")
    log_messages.append("====================================================")

    total_files = 0
    total_issues = 0

    for root, _, files in os.walk(target_dir):
        for file in files:
            if (
                file.endswith(".py")
                and not file.startswith("test_")
                and not file.endswith("_test.py")
            ):
                file_path = Path(root) / file
                total_files += 1
                issues = scan_file(file_path)
                if issues:
                    total_issues += len(issues)
                    for iss in issues:
                        if isinstance(iss, str):  # Error reading file
                            msg = iss
                        else:
                            msg = f"{iss['path']}:{iss['line']}: [{iss['rule_id']}] (Fingerprint: {iss['fingerprint']})"
                        log_messages.append(msg)
                        print(msg)

    log_messages.append(
        f"\nScan completed. Scanned {total_files} files. Found {total_issues} issues."
    )

    log_content = "\n".join(log_messages) + "\n"
    write_log_atomically(log_path, log_content)
    print(f"\nScan log written to: {log_path}")

    if total_issues > 0:
        print("Scan result: FAIL (embedded credentials/secrets found)")
        sys.exit(1)
    else:
        print("Scan result: PASS (no embedded credentials found)")
        sys.exit(0)


if __name__ == "__main__":
    main()
