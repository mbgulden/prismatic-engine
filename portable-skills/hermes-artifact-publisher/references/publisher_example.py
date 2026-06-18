"""Example workspace configuration for the Hermes Artifact Publisher.

The published version lives in your orchestrator profile
(see the `hermes-artifact-publisher` skill). This file is a minimal,
portable example showing the structure of the ALLOWED_ROOTS workspace
map.

Replace the paths below with your deployment's actual directories.
Run locally with:
  PRISMATIC_HOME=$PWD python3 publisher_example.py
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

ALLOWED_ROOTS: dict[str, str] = {
    "published": str(BASE_DIR / "published"),
    "my-project-reports": "${PRISMATIC_HOME}/my-project/reports",
    "my-project-src": "${PRISMATIC_HOME}/my-project/src",
}

# Blocklists (lowercased). Refuse to serve any path whose name matches.
import re
BLOCKED_NAME_PATTERNS = [
    re.compile(r"^\.env($|\.)", re.IGNORECASE),
    re.compile(r"id_(rsa|dsa|ecdsa|ed25519)$", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"credentials", re.IGNORECASE),
    re.compile(r"\.db$", re.IGNORECASE),
    re.compile(r"\.sqlite($|-)", re.IGNORECASE),
    re.compile(r"state\.json$", re.IGNORECASE),
    re.compile(r"auth\.json$", re.IGNORECASE),
]
