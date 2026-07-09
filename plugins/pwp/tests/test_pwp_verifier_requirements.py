from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]


def test_pwp_verifier_artifact_requirements_are_documented() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/verify_pwp_verifier_requirements.py", "--json"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"verdict": "PASS"' in completed.stdout
