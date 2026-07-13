#!/usr/bin/env python3
"""Public repository security/readiness audit for Prismatic Engine.

The audit is intentionally high-signal and local-only. It checks security
posture needed before public sharing with external users; it does not require
network access, credentials, or hosted infrastructure.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

HIGH_CONFIDENCE_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{24,}"),
)

TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv_dev",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "prismatic_state",
}

REQUIRED_DOC_MARKERS = {
    "docs/public-security-readiness.md": [
        "Secret scanning",
        "Environment variable examples",
        "Redaction tests",
        "API auth expectations",
        "Dashboard exposure review",
        "Local vs remote deployment assumptions",
        "CORS review",
        "Destructive action policy",
        "Dependency audit",
        "Artifact path traversal checks",
        "Plugin sandbox assumptions",
        "External service credential flow",
    ],
    "SECURITY.md": ["Secret handling rules", "High-risk surfaces"],
    "README.md": ["Security", "public_security_readiness_audit.py"],
    ".env.example": ["PRISMATIC_CORS_ORIGINS", "PRISMATIC_PUBLIC_DEMO_MODE=1"],
}


def _iter_public_text_files(root: Path = REPO_ROOT) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.name in {".env"}:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {
            "README.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
        }:
            files.append(path)
    return files


def scan_for_raw_secrets(root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in _iter_public_text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                {"path": str(path.relative_to(root)), "issue": f"unreadable: {exc}"}
            )
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        {
                            "path": str(path.relative_to(root)),
                            "line": line_no,
                            "issue": "high-confidence secret-like value",
                        }
                    )
    return findings


def check_env_example() -> list[str]:
    path = REPO_ROOT / ".env.example"
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for bad in ["***", "...", "PWP_UB", "PRIS...", "LINE..."]:
        if bad in text:
            failures.append(
                f".env.example contains malformed/redacted fragment {bad!r}"
            )
    required = [
        "PRISMATIC_STATE_DIR=./prismatic_state",
        "PRISMATIC_CORS_ORIGINS=http://127.0.0.1:9000,http://localhost:9000",
        "PRISMATIC_PUBLIC_DEMO_MODE=1",
    ]
    for marker in required:
        if marker not in text:
            failures.append(f".env.example missing {marker!r}")
    forbidden_assignments = ["TOKEN=", "PASSWORD=", "SECRET=", "API_KEY="]
    for marker in forbidden_assignments:
        if marker in text:
            failures.append(
                f".env.example should not contain credential assignment marker {marker!r}"
            )
    return failures


def check_docs() -> list[str]:
    failures: list[str] = []
    for rel, markers in REQUIRED_DOC_MARKERS.items():
        path = REPO_ROOT / rel
        if not path.exists():
            failures.append(f"missing {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                failures.append(f"{rel} missing {marker!r}")
    return failures


def check_cors() -> list[str]:
    failures: list[str] = []
    from prismatic.gateway import server

    old = os.environ.get("PRISMATIC_CORS_ORIGINS")
    try:
        os.environ.pop("PRISMATIC_CORS_ORIGINS", None)
        defaults = server._configured_cors_origins()
        if "*" in defaults:
            failures.append("default CORS contains wildcard")
        if (
            "http://127.0.0.1:9000" not in defaults
            or "http://localhost:9000" not in defaults
        ):
            failures.append(f"default CORS should be local-only, got {defaults}")
        os.environ["PRISMATIC_CORS_ORIGINS"] = "*,https://dashboard.example.test/"
        explicit = server._configured_cors_origins()
        if "*" in explicit:
            failures.append("wildcard CORS was not rejected")
        if "https://dashboard.example.test" not in explicit:
            failures.append(f"explicit CORS origin was not preserved, got {explicit}")
    finally:
        if old is None:
            os.environ.pop("PRISMATIC_CORS_ORIGINS", None)
        else:
            os.environ["PRISMATIC_CORS_ORIGINS"] = old
    return failures


def check_redaction_and_policy() -> list[str]:
    failures: list[str] = []
    from prismatic.plugin_policy import (
        RISKY_ACTION_TOKENS,
        evaluate_job_request_policy,
        redact_secrets,
    )

    redacted = redact_secrets(
        {
            "nested": {"api_key": "sk" + "-" + "abcdefghijklmnopqrstuvwxyz"},
            "safe_env_name": "PWP_SERVICE_API_KEY",
        }
    )
    if redacted["nested"]["api_key"] != "[REDACTED]":
        failures.append("policy redaction did not redact nested api_key")
    if redacted["safe_env_name"] != "PWP_SERVICE_API_KEY":
        failures.append("policy redaction should preserve env var names")

    required_risky = {
        "publish",
        "export",
        "deploy",
        "delete",
        "destroy",
        "write",
        "overwrite",
        "external-service",
        "credentialed",
        "production",
        "public",
    }
    missing = required_risky - set(RISKY_ACTION_TOKENS)
    if missing:
        failures.append(f"destructive/risky action policy missing {sorted(missing)}")

    secret_job = evaluate_job_request_policy(
        "pwp-design-token-plugin",
        "smoke_validate",
        input_summary={"token": "sk-abc...wxyz"},
    )
    if secret_job["decision"] != "block":
        failures.append("job policy should block raw secret-like input")
    if "sk-" in json.dumps(secret_job):
        failures.append("job policy leaked raw secret-like input")
    return failures


def check_artifact_path_safety() -> list[str]:
    failures: list[str] = []
    from prismatic.plugin_artifacts import safe_local_artifact_path

    outside = safe_local_artifact_path("/etc/passwd")
    if outside is not None:
        failures.append("artifact path safety allowed /etc/passwd")
    traversal = safe_local_artifact_path("../../../../etc/passwd")
    if traversal is not None:
        failures.append("artifact path safety allowed traversal outside allowed roots")
    tmp_file = Path("/tmp/prismatic-public-security-audit-artifact.txt")
    tmp_file.write_text("safe", encoding="utf-8")
    try:
        allowed = safe_local_artifact_path(str(tmp_file))
        if allowed != tmp_file.resolve():
            failures.append(
                f"artifact path safety should allow /tmp artifact, got {allowed}"
            )
    finally:
        try:
            tmp_file.unlink()
        except FileNotFoundError:
            pass
    return failures


def check_dependency_metadata() -> list[str]:
    failures: list[str] = []
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    required = [
        'requires-python = ">=3.10"',
        '"jsonschema>=4.18.0,<5"',
        'gateway = ["fastapi>=0.110", "httpx>=0.27", "uvicorn>=0.27", "websockets>=12.0"]',
    ]
    for marker in required:
        if marker not in pyproject:
            failures.append(f"pyproject missing dependency marker {marker!r}")
    return failures


def run_audit() -> dict[str, Any]:
    checks = {
        "secret_scanning": scan_for_raw_secrets(),
        "env_var_examples": check_env_example(),
        "docs": check_docs(),
        "cors": check_cors(),
        "redaction_and_policy": check_redaction_and_policy(),
        "artifact_path_traversal": check_artifact_path_safety(),
        "dependency_metadata": check_dependency_metadata(),
    }
    failures: list[str] = []
    for name, result in checks.items():
        if result:
            failures.append(f"{name}: {result}")
    return {"ok": not failures, "failures": failures, "checks": checks}


def main() -> int:
    result = run_audit()
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["ok"]:
        print("PUBLIC_SECURITY_READINESS_OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
