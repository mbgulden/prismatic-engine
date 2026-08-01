import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_scan_credentials_synthetic_failure(tmp_path):
    # 1. Create a dummy python file with a secret
    dummy_dir = tmp_path / "dummy_src"
    dummy_dir.mkdir()
    dummy_file = dummy_dir / "dummy_code.py"

    secret_name = "test_auth_key"
    secret_value = "my-super-secret-token-xyz-123"

    # Write potential secret pattern
    dummy_file.write_text(f'{secret_name} = "{secret_value}"\n', encoding="utf-8")

    # 2. Run scan_credentials.py as a subprocess pointing to dummy_dir, passing --log-path
    script_path = Path(__file__).parent.parent / "scripts" / "scan_credentials.py"
    log_path = tmp_path / "credential_scan.log"

    result = subprocess.run(
        [sys.executable, str(script_path), "--log-path", str(log_path), str(dummy_dir)],
        capture_output=True,
        text=True,
    )

    # 3. Verify exit code is nonzero (should fail on high-confidence finding)
    assert result.returncode != 0

    # Compute expected location-based fingerprint: path is str(dummy_file), line is 1
    # Note: rel_path fails back to str(dummy_file) because it's not under workspace dir
    expected_fingerprint = hashlib.sha256(
        f"{dummy_file}:1".encode()
    ).hexdigest()[:16]

    # 4. Verify that stdout, stderr, and the log file do not contain the raw secret value
    # but do contain the location-based fingerprint and the rule ID
    stdout = result.stdout
    stderr = result.stderr

    assert secret_value not in stdout
    assert secret_value not in stderr

    assert expected_fingerprint in stdout
    assert "RULE_POTENTIAL_SECRET" in stdout

    # Check log file exists
    assert log_path.exists()

    # Verify log permissions are 0400 (read-only by owner)
    stat_info = os.stat(log_path)
    assert (stat_info.st_mode & 0o777) == 0o400

    log_content = log_path.read_text(encoding="utf-8")

    assert secret_value not in log_content
    assert expected_fingerprint in log_content
    assert "RULE_POTENTIAL_SECRET" in log_content


def test_scan_credentials_banned_digest(tmp_path):
    # 1. Create a dummy python file with a banned secret
    dummy_dir = tmp_path / "dummy_src"
    dummy_dir.mkdir()
    dummy_file = dummy_dir / "dummy_code.py"

    # "factory-admin-secret-xyz" is a banned default key
    dummy_file.write_text('ADMIN_KEY = "factory-admin-secret-xyz"\n', encoding="utf-8")

    # 2. Run scan_credentials.py
    script_path = Path(__file__).parent.parent / "scripts" / "scan_credentials.py"
    log_path = tmp_path / "credential_scan_banned.log"

    result = subprocess.run(
        [sys.executable, str(script_path), "--log-path", str(log_path), str(dummy_dir)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0

    stdout = result.stdout
    # Verify raw secret value is NOT in output
    assert "factory-admin-secret-xyz" not in stdout
    # Verify rule ID is reported
    assert "RULE_DEFAULT_CREDENTIAL" in stdout

    # Check log file
    assert log_path.exists()
    stat_info = os.stat(log_path)
    assert (stat_info.st_mode & 0o777) == 0o400

    log_content = log_path.read_text(encoding="utf-8")
    assert "factory-admin-secret-xyz" not in log_content
    assert "RULE_DEFAULT_CREDENTIAL" in log_content


@pytest.mark.parametrize(
    "bypass_word", ["env", "config", "test", "mock", "dummy", "example"]
)
def test_scan_credentials_bypass_words_findings(tmp_path, bypass_word):
    # 1. Create a dummy python file with a secret containing the bypass word
    dummy_dir = tmp_path / f"dummy_src_{bypass_word}"
    dummy_dir.mkdir()
    dummy_file = dummy_dir / "dummy_code.py"

    secret_name = f"my_{bypass_word}_secret"
    # Ensure the secret value contains the bypass word but is distinct/synthetic
    secret_value = f"super-{bypass_word}-secret-value-123"

    dummy_file.write_text(f'{secret_name} = "{secret_value}"\n', encoding="utf-8")

    # 2. Run scan_credentials.py
    script_path = Path(__file__).parent.parent / "scripts" / "scan_credentials.py"
    log_path = tmp_path / f"credential_scan_{bypass_word}.log"

    result = subprocess.run(
        [sys.executable, str(script_path), "--log-path", str(log_path), str(dummy_dir)],
        capture_output=True,
        text=True,
    )

    # 3. Verify exit code is nonzero (should fail on high-confidence finding)
    assert result.returncode != 0

    # 4. Verify that stdout, stderr, and the log file do not contain the raw secret value
    stdout = result.stdout
    stderr = result.stderr

    assert secret_value not in stdout
    assert secret_value not in stderr

    # Check log file exists
    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")

    assert secret_value not in log_content
    assert "RULE_POTENTIAL_SECRET" in stdout
    assert "RULE_POTENTIAL_SECRET" in log_content
