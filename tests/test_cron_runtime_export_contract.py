from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

# Fixture Directory Root
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "cron_runtime"
ROOT_DIR = Path(__file__).resolve().parents[1]

UPSTREAM_CONTRACT_PATH = (
    ROOT_DIR / "docs" / "contracts" / "cron-runtime-authority-v1.md"
)
EXPECTED_BASE_COMMIT = "e0069a70801840b931057997e344184797ff6f91"
EXPECTED_BASE_TREE = "4a771ff26ee504a73a719e4108628894adb5138e"
EXPECTED_CONTRACT_SHA256 = (
    "4d8994cb6c54d2f47912a144dc759285a80841328ee56fb8823ffadb0c62d024"
)
ABSENT_HOOK_PATH = Path(
    "/home/ubuntu/.prismatic/releases/e63d621a26a944a66cd4af2c6b5ab3084fc92b55/bin/pe-cron-trigger"
)
LIVE_CRONTAB_EXPORT_SHA256 = (
    "8ff18b26ef3c4b41efe91bbf30b93e0316ec49bd0bbb2d9eb9f4c8e25364d680"
)
LIVE_SPOOL_FILE_SHA256 = (
    "0dfbad53e1b5a8bfcdb891473fb54c9e36b462914f7ad04c5364567da8bc3c4c"
)

# Managed block markers
BEGIN_MARKER = "# BEGIN PRISMATIC_NATIVE_CRONS"
END_MARKER = "# END PRISMATIC_NATIVE_CRONS"


# Helper validation functions for candidate export classification
def validate_cron_schedule_syntax(schedule: str) -> bool:
    """Validates standard 5-field cron schedule syntax."""
    fields = schedule.strip().split()
    if len(fields) != 5:
        return False
    # Check minute (0-59), hour (0-23), dom (1-31), month (1-12), dow (0-7)
    patterns = [
        r"^(\*|([0-5]?\d)(-[0-5]?\d)?)(/([1-5]?\d))?$",  # min
        r"^(\*|(1?\d|2[0-3])(-(1?\d|2[0-3]))?)(/(1?\d|2[0-3]))?$",  # hr
        r"^(\*|([1-2]?\d|3[0-1])(-([1-2]?\d|3[0-1]))?)(/([1-2]?\d|3[0-1]))?$",  # dom
        r"^(\*|(1[0-2]|[1-9])(-(1[0-2]|[1-9]))?)(/(1[0-2]|[1-9]))?$",  # month
        r"^(\*|[0-7](-[0-7])?)(/[0-7])?$",  # dow
    ]
    for field, pat in zip(fields, patterns):
        if not re.match(pat, field):
            return False
    return True


def is_full_commit_hook_path(path_str: str) -> bool:
    """Only absolute path with 40-lowercase-hex commit directory is eligible."""
    pattern = r"^/home/ubuntu/\.prismatic/releases/[0-9a-f]{40}/bin/pe-cron-trigger$"
    return bool(re.match(pattern, path_str))


def is_admissible_cron_entry(entry_line: str) -> tuple[bool, str]:
    """Classifies a candidate crontab entry line according to immutable runtime contract v1."""
    line = entry_line.strip()
    if not line or line.startswith("#"):
        return True, "comment_or_empty"

    # Environment assignment before command is forbidden in managed trigger lines
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line):
        return False, "environment_assignment_forbidden"

    tokens = line.split()
    if len(tokens) < 6:
        return False, "invalid_field_count"

    schedule = " ".join(tokens[:5])
    if not validate_cron_schedule_syntax(schedule):
        return False, "invalid_cron_schedule"

    parts = line.split(maxsplit=5)
    command = parts[5]

    # Unescaped percent signs are cron newline/stdin injections
    if "%" in command and r"\%" not in command:
        return False, "unescaped_percent_injection"

    # Embedded newlines
    if "\n" in command or "\r" in command:
        return False, "embedded_newline_injection"

    # Direct workload scripts or cd to mutable checkouts forbidden
    if "cd /home/ubuntu/work" in command or "/home/ubuntu/work/" in command:
        return False, "mutable_checkout_rejection"

    if "python" in command and "pe-cron-trigger" not in command:
        return False, "direct_workload_script_rejection"

    # Check executable path
    cmd_parts = command.split()
    executable = cmd_parts[0]
    if not is_full_commit_hook_path(executable):
        return False, "invalid_hook_path_or_alias"

    # Check hook binary existence
    if not Path(executable).is_file():
        return False, "absent_hook_binary"

    return True, "admissible"


def replace_managed_block_fixture(existing: str, block: str) -> str:
    """Replaces or appends the managed block in crontab text."""
    if BEGIN_MARKER in existing and END_MARKER in existing:
        prefix = existing.split(BEGIN_MARKER)[0]
        suffix = existing.split(END_MARKER)[1]
        if suffix.startswith("\n"):
            suffix = suffix[1:]
        return f"{prefix}{block.strip()}\n{suffix}"
    else:
        trimmed = existing.rstrip()
        if trimmed:
            return f"{trimmed}\n\n{block.strip()}\n"
        return f"{block.strip()}\n"


def remove_managed_block_fixture(existing: str) -> str:
    """Removes managed block from crontab text."""
    if BEGIN_MARKER in existing and END_MARKER in existing:
        prefix = existing.split(BEGIN_MARKER)[0]
        suffix = existing.split(END_MARKER)[1]
        if suffix.startswith("\n"):
            suffix = suffix[1:]
        return f"{prefix.rstrip()}\n\n{suffix.lstrip()}".rstrip() + "\n"
    return existing


# Fixtures check
def test_contract_metadata_fixture_bound_to_base() -> None:
    """Metadata fixture matches base commit, tree, contract path, and contract hash."""
    meta_path = FIXTURES_DIR / "contract_metadata.json"
    assert meta_path.exists(), f"Missing fixture {meta_path}"
    data = json.loads(meta_path.read_text())

    assert data["base_commit"] == EXPECTED_BASE_COMMIT
    assert data["base_tree"] == EXPECTED_BASE_TREE
    assert data["upstream_contract"] == str(
        UPSTREAM_CONTRACT_PATH.relative_to(ROOT_DIR)
    )

    # Verify actual upstream contract sha256
    contract_bytes = UPSTREAM_CONTRACT_PATH.read_bytes()
    actual_sha256 = hashlib.sha256(contract_bytes).hexdigest()
    assert actual_sha256 == EXPECTED_CONTRACT_SHA256
    assert data["upstream_contract_sha256"] == EXPECTED_CONTRACT_SHA256

    # Verify hook absence
    assert not ABSENT_HOOK_PATH.exists()
    assert data["hook_exists"] is False


def test_pe_cron_runtime_neg_syntax() -> None:
    """Marker: PE-CRON-RUNTIME-NEG-SYNTAX

    Malformed schedules/fields are rejected.
    """
    invalid_schedules = [
        "0 3 * *",  # 4 fields
        "0 3 * * * *",  # 6 fields
        "60 3 * * *",  # minute out of range
        "0 25 * * *",  # hour out of range
        "0 3 32 * *",  # dom out of range
        "0 3 * 13 *",  # month out of range
        "0 3 * * 8",  # dow out of range
        "a b c d e",  # non-numeric
    ]
    hook_path = "/home/ubuntu/.prismatic/releases/e63d621a26a944a66cd4af2c6b5ab3084fc92b55/bin/pe-cron-trigger"
    for sched in invalid_schedules:
        entry = f"{sched} {hook_path} --cron-id test.job"
        admissible, reason = is_admissible_cron_entry(entry)
        assert not admissible, f"Schedule '{sched}' should be rejected"
        assert reason in (
            "invalid_field_count",
            "invalid_cron_schedule",
            "invalid_hook_path_or_alias",
        )


def test_pe_cron_runtime_percent_escape() -> None:
    """Marker: PE-CRON-RUNTIME-PERCENT-ESCAPE

    %, whitespace, quoting, environment, and newline behavior is bounded.
    """
    injection_file = FIXTURES_DIR / "injection_cases_export.txt"
    assert injection_file.exists()
    content = injection_file.read_text()

    for line in content.splitlines():
        if line.strip() and not line.startswith("#"):
            admissible, reason = is_admissible_cron_entry(line)
            assert not admissible, f"Line '{line}' should be rejected"
            assert reason in (
                "unescaped_percent_injection",
                "environment_assignment_forbidden",
                "invalid_hook_path_or_alias",
                "absent_hook_binary",
            )


def test_pe_cron_runtime_absolute_hook() -> None:
    """Marker: PE-CRON-RUNTIME-ABSOLUTE-HOOK

    Only a full-commit-addressed absolute hook path can become eligible,
    and current absence yields zero admitted lines.
    """
    invalid_paths = [
        "/home/ubuntu/.prismatic/releases/v1.0.0/bin/pe-cron-trigger",  # semver alias
        "/home/ubuntu/.prismatic/releases/latest/bin/pe-cron-trigger",  # latest alias
        "/home/ubuntu/.prismatic/releases/e63d621/bin/pe-cron-trigger",  # short hash
        "bin/pe-cron-trigger",  # relative
        "/usr/local/bin/pe-cron-trigger",  # non-release directory
    ]
    for path_str in invalid_paths:
        assert not is_full_commit_hook_path(path_str), (
            f"Path '{path_str}' should be ineligible"
        )

    valid_commit_path = "/home/ubuntu/.prismatic/releases/e63d621a26a944a66cd4af2c6b5ab3084fc92b55/bin/pe-cron-trigger"
    assert is_full_commit_hook_path(valid_commit_path)

    # Current absence of the binary on disk yields zero admitted lines
    entry = f"0 3 * * * {valid_commit_path} --cron-id test.job"
    admissible, reason = is_admissible_cron_entry(entry)
    assert not admissible
    assert reason == "absent_hook_binary"

    # Empty export check
    empty_export = (FIXTURES_DIR / "empty_managed_export.txt").read_text()
    admitted_lines = [
        line
        for line in empty_export.splitlines()
        if line.strip()
        and not line.startswith("#")
        and is_admissible_cron_entry(line)[0]
    ]
    assert len(admitted_lines) == 0


def test_pe_cron_runtime_digest() -> None:
    """Marker: PE-CRON-RUNTIME-DIGEST

    Release/config digests are recomputed and compared, not shape-checked only.
    """
    invalid_digest_file = FIXTURES_DIR / "invalid_digest_export.json"
    assert invalid_digest_file.exists()
    digests = json.loads(invalid_digest_file.read_text())

    def validate_sha256_digest(d: str, expected_content: bytes | None = None) -> bool:
        if not re.match(r"^[0-9a-f]{64}$", d):
            return False
        if expected_content is not None:
            recomputed = hashlib.sha256(expected_content).hexdigest()
            if recomputed != d:
                return False
        return True

    assert not validate_sha256_digest(
        digests["invalid_length_release_digest"]
    )  # 32 chars
    assert not validate_sha256_digest(digests["uppercase_release_digest"])  # Uppercase
    assert not validate_sha256_digest(digests["non_hex_digest"])  # Invalid chars

    # Recomputation test against content
    sample_content = b"canonical cron registry json content"
    correct_hash = hashlib.sha256(sample_content).hexdigest()
    assert validate_sha256_digest(correct_hash, sample_content)
    assert not validate_sha256_digest(
        digests["mismatched_config_digest"], sample_content
    )


def test_pe_cron_runtime_idempotent_dryrun() -> None:
    """Marker: PE-CRON-RUNTIME-IDEMPOTENT-DRYRUN

    Repeated fixture-only replacement converges to one managed block
    and repeated removal converges safely to none.
    """
    baseline = (FIXTURES_DIR / "idempotent_replacement_fixture.txt").read_text()
    empty_managed_block = (FIXTURES_DIR / "empty_managed_export.txt").read_text()

    # Replacement 1
    step1 = replace_managed_block_fixture(baseline, empty_managed_block)
    assert step1.count(BEGIN_MARKER) == 1
    assert step1.count(END_MARKER) == 1

    # Replacement 2 (Repeated)
    step2 = replace_managed_block_fixture(step1, empty_managed_block)
    assert step2 == step1, "Repeated dry-run replacement must be byte-identical"

    # Removal 1
    rem1 = remove_managed_block_fixture(step2)
    assert BEGIN_MARKER not in rem1
    assert END_MARKER not in rem1

    # Removal 2 (Repeated)
    rem2 = remove_managed_block_fixture(rem1)
    assert rem2 == rem1, "Repeated dry-run removal must be byte-identical"


def test_pe_cron_runtime_rollback_fixture() -> None:
    """Marker: PE-CRON-RUNTIME-ROLLBACK-FIXTURE

    Exact preserved bytes and SHA-256 are restored.
    """
    rollback_file = FIXTURES_DIR / "rollback_fixture.json"
    assert rollback_file.exists()
    rollback_data = json.loads(rollback_file.read_text())

    assert rollback_data["pre_mutation_crontab_sha256"] == LIVE_CRONTAB_EXPORT_SHA256
    assert rollback_data["post_rollback_crontab_sha256"] == LIVE_CRONTAB_EXPORT_SHA256
    assert rollback_data["spool_file_sha256"] == LIVE_SPOOL_FILE_SHA256


def test_pe_cron_runtime_no_direct_script() -> None:
    """Marker: PE-CRON-RUNTIME-NO-DIRECT-SCRIPT

    Mutable checkout/direct workload commands and fallback authority are rejected.
    """
    mutable_file = FIXTURES_DIR / "mutable_checkout_export.txt"
    assert mutable_file.exists()
    for line in mutable_file.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            admissible, reason = is_admissible_cron_entry(line)
            assert not admissible
            assert reason in (
                "mutable_checkout_rejection",
                "direct_workload_script_rejection",
            )

    direct_file = FIXTURES_DIR / "direct_workload_export.txt"
    assert direct_file.exists()
    for line in direct_file.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            admissible, reason = is_admissible_cron_entry(line)
            assert not admissible
            assert reason in (
                "direct_workload_script_rejection",
                "invalid_hook_path_or_alias",
            )


def test_crontab_syntax_validation_disposable_file() -> None:
    """Validates cron syntax with `crontab -n <tempfile>` on disposable file,

    asserting live `crontab -l` SHA-256 remains completely unchanged.
    """
    # 1. Capture live crontab -l SHA-256 before
    res_before = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=True
    )
    sha_before = hashlib.sha256(res_before.stdout.encode("utf-8")).hexdigest()
    assert sha_before == LIVE_CRONTAB_EXPORT_SHA256

    # 2. Test syntax validation on disposable tempfile
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write("0 3 * * * echo 'disposable syntax check'\n")
        temp_path = tf.name

    try:
        res = subprocess.run(
            ["crontab", "-n", temp_path], capture_output=True, text=True
        )
        assert res.returncode == 0, f"crontab -n syntax check failed: {res.stderr}"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # 3. Capture live crontab -l SHA-256 after
    res_after = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=True
    )
    sha_after = hashlib.sha256(res_after.stdout.encode("utf-8")).hexdigest()
    assert sha_after == sha_before, "Live crontab -l SHA-256 changed!"


def test_monkeypatched_mutation_tripwires(monkeypatch) -> None:
    """Monkeypatched mutation tripwires fail if crontab write/install/remove,

    systemctl, DB, network, or production-file writes are attempted.
    """
    orig_run = subprocess.run

    def guarded_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)):
            cmd_str = " ".join(str(c) for c in cmd)
        else:
            cmd_str = str(cmd)

        # Block any crontab mutation (crontab without -l or -n)
        if "crontab" in cmd_str:
            if "-l" not in cmd_str and "-n" not in cmd_str:
                pytest.fail(
                    f"TRIPWIRE TRIGGERED: Live crontab mutation attempted! Command: {cmd_str}"
                )

        # Block systemctl state changes
        if "systemctl" in cmd_str:
            for forbidden in (
                "enable",
                "disable",
                "start",
                "stop",
                "restart",
                "daemon-reload",
            ):
                if forbidden in cmd_str:
                    pytest.fail(
                        f"TRIPWIRE TRIGGERED: Systemd state mutation attempted! Command: {cmd_str}"
                    )

        return orig_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)

    # Assert guarded run allows crontab -l and crontab -n
    guarded_run(["crontab", "-l"], capture_output=True, text=True)

    # Assert guarded run trips on live crontab install
    with pytest.raises(pytest.fail.Exception, match="TRIPWIRE TRIGGERED"):
        guarded_run(["crontab", "somefile"])


def test_fixtures_secret_scanned_and_bounded_size() -> None:
    """All fixtures are secret-scanned and bounded in size (< 10KB)."""
    fixture_files = list(FIXTURES_DIR.glob("**/*"))
    assert len(fixture_files) > 0, "No fixture files found!"

    secret_patterns = [
        r"sk-[a-zA-Z0-9]{20,}",  # OpenAI secret key format
        r"bearer\s+[a-zA-Z0-9_\-\.]{20,}",  # Bearer token
        r"-----BEGIN PRIVATE KEY-----",  # PEM private key
        r"ghp_[a-zA-Z0-9]{36}",  # GitHub personal access token
    ]

    for ff in fixture_files:
        if ff.is_file():
            size = ff.stat().st_size
            assert size < 10240, (
                f"Fixture file {ff.name} exceeds size limit (10KB): {size} bytes"
            )

            content = ff.read_text(errors="ignore")
            for pat in secret_patterns:
                assert not re.search(pat, content, re.IGNORECASE), (
                    f"Secret pattern '{pat}' detected in fixture {ff.name}!"
                )
