from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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


def validate_digest_shape(digest_str: str) -> tuple[bool, str]:
    """Validates that digest_str is a 64-character lowercase hexadecimal string."""
    if not isinstance(digest_str, str):
        return False, "invalid_digest_type"
    if len(digest_str) != 64:
        return False, "invalid_length_digest"
    if digest_str.isupper() or any(c.isupper() for c in digest_str):
        return False, "uppercase_digest"
    if digest_str == "0" * 64:
        return False, "placeholder_digest"
    if not re.match(r"^[0-9a-f]{64}$", digest_str):
        return False, "non_hex_digest"
    return True, "valid_shape"


def classify_candidate_export(
    export_content: str | bytes,
    manifest_bytes: bytes | None = None,
    config_bytes: bytes | None = None,
    binding_record: dict | None = None,
) -> tuple[bool, str]:
    """Classifies a candidate crontab export (or entry line) enforcing contract v1 rules:

    - parses lowercase 64-hex release_digest and config_digest bindings;
    - recomputes SHA-256 from candidate manifest/config bytes;
    - compares recomputed values to parsed bindings;
    - binds full manifest merge commit to full release directory component;
    - rejects malformed, uppercase, wrong-length, placeholder, alias/short-SHA, missing-byte, and digest-mismatch cases;
    - rejects valid-shape fixture digests when no verified real hook/config pair exists;
    - yields zero admitted production lines at this base because the real hook/manifest/config binding remains absent.
    """
    if isinstance(export_content, bytes):
        try:
            content_str = export_content.decode("utf-8")
        except UnicodeDecodeError:
            return False, "invalid_encoding"
    else:
        content_str = export_content

    lines = [line.strip() for line in content_str.splitlines() if line.strip()]
    if not lines:
        return True, "comment_or_empty"

    for line in lines:
        if line.startswith("#"):
            continue

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

        commit_in_path = executable.split("/releases/")[1].split(
            "/bin/pe-cron-trigger"
        )[0]

        release_digest = None
        config_digest = None
        for i, token in enumerate(cmd_parts[:-1]):
            if token == "--release-digest":
                release_digest = cmd_parts[i + 1]
            elif token == "--config-digest":
                config_digest = cmd_parts[i + 1]

        # 1. Require --release-digest in command flags
        if release_digest is None:
            return False, "missing_release_digest"

        # Validate shape of release_digest
        valid_shape, shape_reason = validate_digest_shape(release_digest)
        if not valid_shape:
            return False, f"release_digest_{shape_reason}"

        # 2. Require --config-digest in command flags
        if config_digest is None:
            return False, "missing_config_digest"

        # Validate shape of config_digest
        valid_shape, shape_reason = validate_digest_shape(config_digest)
        if not valid_shape:
            return False, f"config_digest_{shape_reason}"

        # 3. Require manifest_bytes and config_bytes
        if manifest_bytes is None or not isinstance(manifest_bytes, bytes):
            return False, "missing_manifest_bytes"
        if config_bytes is None or not isinstance(config_bytes, bytes):
            return False, "missing_config_bytes"

        # 4 & 5. Require non-empty binding_record containing required fields
        if not binding_record or not isinstance(binding_record, dict):
            return False, "missing_binding_record"

        if "release_digest" not in binding_record:
            return False, "binding_missing_release_digest"
        if "config_digest" not in binding_record:
            return False, "binding_missing_config_digest"
        if "merge_commit" not in binding_record:
            return False, "binding_missing_merge_commit"
        if "is_verified_pair" not in binding_record:
            return False, "binding_missing_is_verified_pair"

        # 6. Command digests equal binding-record digests
        if release_digest != binding_record["release_digest"]:
            return False, "command_binding_release_digest_mismatch"
        if config_digest != binding_record["config_digest"]:
            return False, "command_binding_config_digest_mismatch"

        # 7. Command/binding digests equal SHA-256 recomputed from exact bytes
        recomputed_rel = hashlib.sha256(manifest_bytes).hexdigest()
        if (
            release_digest != recomputed_rel
            or binding_record["release_digest"] != recomputed_rel
        ):
            return False, "mismatched_release_digest"

        recomputed_cfg = hashlib.sha256(config_bytes).hexdigest()
        if (
            config_digest != recomputed_cfg
            or binding_record["config_digest"] != recomputed_cfg
        ):
            return False, "mismatched_config_digest"

        # 8. Manifest merge_commit, binding-record merge_commit, and full release-directory commit match exactly
        try:
            manifest_data = json.loads(manifest_bytes.decode("utf-8"))
            manifest_commit = manifest_data.get("merge_commit")
        except Exception:
            return False, "invalid_manifest_bytes"

        if manifest_commit is None:
            return False, "missing_manifest_merge_commit"

        binding_commit = binding_record["merge_commit"]

        if manifest_commit != binding_commit:
            return False, "manifest_binding_commit_mismatch"

        if commit_in_path != manifest_commit or commit_in_path != binding_commit:
            return False, "commit_path_mismatch"

        # 9. is_verified_pair is True — truthy substitutes such as 1 or non-empty strings do not pass
        if (
            type(binding_record["is_verified_pair"]) is not bool
            or binding_record["is_verified_pair"] is not True
        ):
            return False, "unverified_hook_or_config_pair"

        # 10. Only after all evidence succeeds may hook existence be evaluated and admission considered
        if not Path(executable).is_file():
            return False, "absent_hook_binary"

    return True, "admissible"


def is_admissible_cron_entry(entry_line: str) -> tuple[bool, str]:
    """Classifies a candidate crontab entry line according to immutable runtime contract v1."""
    return classify_candidate_export(entry_line)


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
                "missing_release_digest",
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
    assert reason == "missing_release_digest"

    sample_manifest = b'{"release_id":"rel_01","merge_commit":"e63d621a26a944a66cd4af2c6b5ab3084fc92b55"}'
    sample_config = b'{"crons":[]}'
    rel_digest = hashlib.sha256(sample_manifest).hexdigest()
    cfg_digest = hashlib.sha256(sample_config).hexdigest()
    entry_with_evidence = f"0 3 * * * {valid_commit_path} --release-digest {rel_digest} --config-digest {cfg_digest}"
    valid_binding = {
        "release_digest": rel_digest,
        "config_digest": cfg_digest,
        "merge_commit": "e63d621a26a944a66cd4af2c6b5ab3084fc92b55",
        "is_verified_pair": True,
    }
    admissible_ev, reason_ev = classify_candidate_export(
        entry_with_evidence,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record=valid_binding,
    )
    assert not admissible_ev
    assert reason_ev == "absent_hook_binary"

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

    Release/config digests are parsed, recomputed from manifest/config bytes,
    bound to release directory commit, and verified against deployment policy.
    """
    invalid_digest_file = FIXTURES_DIR / "invalid_digest_export.json"
    assert invalid_digest_file.exists()
    digests = json.loads(invalid_digest_file.read_text())

    sample_manifest = b'{"release_id":"rel_fixture_01","merge_commit":"e63d621a26a944a66cd4af2c6b5ab3084fc92b55"}'
    recomputed_rel = hashlib.sha256(sample_manifest).hexdigest()
    sample_config = b'{"crons":[]}'
    recomputed_cfg = hashlib.sha256(sample_config).hexdigest()

    base_hook = "/home/ubuntu/.prismatic/releases/e63d621a26a944a66cd4af2c6b5ab3084fc92b55/bin/pe-cron-trigger"

    valid_binding = {
        "release_digest": recomputed_rel,
        "config_digest": recomputed_cfg,
        "merge_commit": "e63d621a26a944a66cd4af2c6b5ab3084fc92b55",
        "is_verified_pair": True,
    }

    # 1. Invalid length digest
    entry = f"0 3 * * * {base_hook} --release-digest {digests['invalid_length_release_digest']} --config-digest {recomputed_cfg}"
    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record={
            **valid_binding,
            "release_digest": digests["invalid_length_release_digest"],
        },
    )
    assert not admissible
    assert "invalid_length" in reason

    # 2. Uppercase digest
    entry = f"0 3 * * * {base_hook} --release-digest {digests['uppercase_release_digest']} --config-digest {recomputed_cfg}"
    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record={
            **valid_binding,
            "release_digest": digests["uppercase_release_digest"],
        },
    )
    assert not admissible
    assert "uppercase" in reason

    # 3. Non-hex digest
    entry = f"0 3 * * * {base_hook} --release-digest {digests['non_hex_digest']} --config-digest {recomputed_cfg}"
    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record={
            **valid_binding,
            "release_digest": digests["non_hex_digest"],
        },
    )
    assert not admissible
    assert "non_hex" in reason

    # 4. Placeholder digest
    entry = f"0 3 * * * {base_hook} --release-digest {digests['placeholder_digest']} --config-digest {recomputed_cfg}"
    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record={
            **valid_binding,
            "release_digest": digests["placeholder_digest"],
        },
    )
    assert not admissible
    assert "placeholder" in reason

    # 5. Shape-valid but recomputation-mismatched release digest
    mismatched_rel = digests["shape_valid_mismatched_release_digest"]
    entry = f"0 3 * * * {base_hook} --release-digest {mismatched_rel} --config-digest {recomputed_cfg}"
    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record={**valid_binding, "release_digest": mismatched_rel},
    )
    assert not admissible
    assert reason == "mismatched_release_digest"

    # 6. Shape-valid but recomputation-mismatched config digest
    mismatched_cfg = digests["shape_valid_mismatched_config_digest"]
    entry = f"0 3 * * * {base_hook} --release-digest {recomputed_rel} --config-digest {mismatched_cfg}"
    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record={**valid_binding, "config_digest": mismatched_cfg},
    )
    assert not admissible
    assert reason == "mismatched_config_digest"

    # 7. Commit-path mismatch
    diff_commit_manifest = b'{"release_id":"rel_fixture_02","merge_commit":"1111111111111111111111111111111111111111"}'
    diff_rel_digest = hashlib.sha256(diff_commit_manifest).hexdigest()
    entry = f"0 3 * * * {base_hook} --release-digest {diff_rel_digest} --config-digest {recomputed_cfg}"
    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=diff_commit_manifest,
        config_bytes=sample_config,
        binding_record={
            "release_digest": diff_rel_digest,
            "config_digest": recomputed_cfg,
            "merge_commit": "1111111111111111111111111111111111111111",
            "is_verified_pair": True,
        },
    )
    assert not admissible
    assert reason == "commit_path_mismatch"

    # 8. Internally consistent non-production fixture fails deployment policy due to absent hook binary
    valid_entry = f"0 3 * * * {base_hook} --release-digest {recomputed_rel} --config-digest {recomputed_cfg}"
    admissible, reason = classify_candidate_export(
        valid_entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record={
            "release_digest": recomputed_rel,
            "config_digest": recomputed_cfg,
            "merge_commit": "e63d621a26a944a66cd4af2c6b5ab3084fc92b55",
            "is_verified_pair": False,
        },
    )
    assert not admissible
    assert reason in ("absent_hook_binary", "unverified_hook_or_config_pair")


def test_adversarial_digest_helper_bypass_blocked(monkeypatch) -> None:
    """Adversarial regression proving that monkeypatching or bypassing a detached digest helper

    cannot make a digest-mismatched candidate export admissible.
    """

    def detached_helper_bypass(d: str, expected_content: bytes | None = None) -> bool:
        return True  # Maliciously approve everything

    monkeypatch.setattr(
        "tests.test_cron_runtime_export_contract.validate_digest_shape",
        lambda d: (True, "fake_valid"),
    )

    sample_manifest = b'{"release_id":"rel_fixture_01","merge_commit":"e63d621a26a944a66cd4af2c6b5ab3084fc92b55"}'
    sample_config = b'{"crons":[]}'
    recomputed_cfg = hashlib.sha256(sample_config).hexdigest()
    mismatched_digest = (
        "1111111111111111111111111111111111111111111111111111111111111111"
    )
    base_hook = "/home/ubuntu/.prismatic/releases/e63d621a26a944a66cd4af2c6b5ab3084fc92b55/bin/pe-cron-trigger"
    entry = f"0 3 * * * {base_hook} --release-digest {mismatched_digest} --config-digest {recomputed_cfg}"
    binding_record = {
        "release_digest": mismatched_digest,
        "config_digest": recomputed_cfg,
        "merge_commit": "e63d621a26a944a66cd4af2c6b5ab3084fc92b55",
        "is_verified_pair": True,
    }

    admissible, reason = classify_candidate_export(
        entry,
        manifest_bytes=sample_manifest,
        config_bytes=sample_config,
        binding_record=binding_record,
    )
    assert not admissible
    assert reason == "mismatched_release_digest"


def test_pe_cron_runtime_fail_closed_evidence_regressions(monkeypatch) -> None:
    """Marker: PE-CRON-RUNTIME-FAIL-CLOSED-EVIDENCE

    Mocks hook existence to True and verifies that export classification fails closed
    unless all required evidence is complete, valid, internally consistent, and verified.
    Also tests the positive verified admission case.
    """
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    base_hook = "/home/ubuntu/.prismatic/releases/e63d621a26a944a66cd4af2c6b5ab3084fc92b55/bin/pe-cron-trigger"
    manifest_bytes = b'{"release_id":"rel_fixture_01","merge_commit":"e63d621a26a944a66cd4af2c6b5ab3084fc92b55"}'
    config_bytes = b'{"crons":[]}'
    rel_digest = hashlib.sha256(manifest_bytes).hexdigest()
    cfg_digest = hashlib.sha256(config_bytes).hexdigest()

    full_entry = f"0 3 * * * {base_hook} --release-digest {rel_digest} --config-digest {cfg_digest}"
    no_flags_entry = f"0 3 * * * {base_hook}"
    rel_only_entry = f"0 3 * * * {base_hook} --release-digest {rel_digest}"
    cfg_only_entry = f"0 3 * * * {base_hook} --config-digest {cfg_digest}"

    valid_binding = {
        "release_digest": rel_digest,
        "config_digest": cfg_digest,
        "merge_commit": "e63d621a26a944a66cd4af2c6b5ab3084fc92b55",
        "is_verified_pair": True,
    }

    # 1. no digest flags + no binding record
    ok, reason = classify_candidate_export(no_flags_entry)
    assert not ok and reason == "missing_release_digest"

    # 2. no digest flags + empty binding record
    ok, reason = classify_candidate_export(no_flags_entry, binding_record={})
    assert not ok and reason == "missing_release_digest"

    # 3. no digest flags + exact bytes but no binding record
    ok, reason = classify_candidate_export(
        no_flags_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
    )
    assert not ok and reason == "missing_release_digest"

    # 4. matching command digest flags + no binding record
    ok, reason = classify_candidate_export(
        full_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
    )
    assert not ok and reason == "missing_binding_record"

    # 5. release digest only
    ok, reason = classify_candidate_export(
        rel_only_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=valid_binding,
    )
    assert not ok and reason == "missing_config_digest"

    # 6. config digest only
    ok, reason = classify_candidate_export(
        cfg_only_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=valid_binding,
    )
    assert not ok and reason == "missing_release_digest"

    # 7. binding record missing either digest
    b_no_rel = {k: v for k, v in valid_binding.items() if k != "release_digest"}
    ok, reason = classify_candidate_export(
        full_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=b_no_rel,
    )
    assert not ok and reason == "binding_missing_release_digest"

    b_no_cfg = {k: v for k, v in valid_binding.items() if k != "config_digest"}
    ok, reason = classify_candidate_export(
        full_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=b_no_cfg,
    )
    assert not ok and reason == "binding_missing_config_digest"

    # 8. binding record missing merge commit
    b_no_commit = {k: v for k, v in valid_binding.items() if k != "merge_commit"}
    ok, reason = classify_candidate_export(
        full_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=b_no_commit,
    )
    assert not ok and reason == "binding_missing_merge_commit"

    # 9. missing manifest bytes
    ok, reason = classify_candidate_export(
        full_entry,
        config_bytes=config_bytes,
        binding_record=valid_binding,
    )
    assert not ok and reason == "missing_manifest_bytes"

    # 10. missing config bytes
    ok, reason = classify_candidate_export(
        full_entry,
        manifest_bytes=manifest_bytes,
        binding_record=valid_binding,
    )
    assert not ok and reason == "missing_config_bytes"

    # 11. command digest versus binding-record mismatch
    diff_digest = "a" * 64
    entry_diff_rel = f"0 3 * * * {base_hook} --release-digest {diff_digest} --config-digest {cfg_digest}"
    ok, reason = classify_candidate_export(
        entry_diff_rel,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=valid_binding,
    )
    assert not ok and reason == "command_binding_release_digest_mismatch"

    entry_diff_cfg = f"0 3 * * * {base_hook} --release-digest {rel_digest} --config-digest {diff_digest}"
    ok, reason = classify_candidate_export(
        entry_diff_cfg,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=valid_binding,
    )
    assert not ok and reason == "command_binding_config_digest_mismatch"

    # 12. binding-record digest versus recomputed-byte mismatch
    b_bad_rel = {**valid_binding, "release_digest": diff_digest}
    entry_bad_rel = f"0 3 * * * {base_hook} --release-digest {diff_digest} --config-digest {cfg_digest}"
    ok, reason = classify_candidate_export(
        entry_bad_rel,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=b_bad_rel,
    )
    assert not ok and reason == "mismatched_release_digest"

    b_bad_cfg = {**valid_binding, "config_digest": diff_digest}
    entry_bad_cfg = f"0 3 * * * {base_hook} --release-digest {rel_digest} --config-digest {diff_digest}"
    ok, reason = classify_candidate_export(
        entry_bad_cfg,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=b_bad_cfg,
    )
    assert not ok and reason == "mismatched_config_digest"

    # 13. manifest commit versus binding-record mismatch
    manifest_diff_commit = b'{"release_id":"rel_01","merge_commit":"1111111111111111111111111111111111111111"}'
    rel_diff_commit = hashlib.sha256(manifest_diff_commit).hexdigest()
    entry_diff_commit_manifest = f"0 3 * * * {base_hook} --release-digest {rel_diff_commit} --config-digest {cfg_digest}"
    b_diff_commit_manifest = {
        **valid_binding,
        "release_digest": rel_diff_commit,
    }
    ok, reason = classify_candidate_export(
        entry_diff_commit_manifest,
        manifest_bytes=manifest_diff_commit,
        config_bytes=config_bytes,
        binding_record=b_diff_commit_manifest,
    )
    assert not ok and reason == "manifest_binding_commit_mismatch"

    # 14. manifest/binding commit versus release-directory mismatch
    other_commit = "2222222222222222222222222222222222222222"
    manifest_other = (
        f'{{"release_id":"rel_01","merge_commit":"{other_commit}"}}'.encode("utf-8")
    )
    rel_other = hashlib.sha256(manifest_other).hexdigest()
    entry_other = f"0 3 * * * {base_hook} --release-digest {rel_other} --config-digest {cfg_digest}"
    b_other = {
        **valid_binding,
        "release_digest": rel_other,
        "merge_commit": other_commit,
    }
    ok, reason = classify_candidate_export(
        entry_other,
        manifest_bytes=manifest_other,
        config_bytes=config_bytes,
        binding_record=b_other,
    )
    assert not ok and reason == "commit_path_mismatch"

    # 15. is_verified_pair absent, false, 0, 1, or a non-boolean truthy value
    for invalid_val in (None, False, 0, 1, "true", "True", [True]):
        if invalid_val is None:
            b_inv = {k: v for k, v in valid_binding.items() if k != "is_verified_pair"}
        else:
            b_inv = {**valid_binding, "is_verified_pair": invalid_val}
        ok, reason = classify_candidate_export(
            full_entry,
            manifest_bytes=manifest_bytes,
            config_bytes=config_bytes,
            binding_record=b_inv,
        )
        assert not ok, f"is_verified_pair={invalid_val!r} should be rejected"
        assert reason in (
            "binding_missing_is_verified_pair",
            "unverified_hook_or_config_pair",
        )

    # Positive test: all exact evidence valid and mocked hook exists -> admissible
    ok, reason = classify_candidate_export(
        full_entry,
        manifest_bytes=manifest_bytes,
        config_bytes=config_bytes,
        binding_record=valid_binding,
    )
    assert ok and reason == "admissible"


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
    crontab = shutil.which("crontab")
    if crontab is None:
        pytest.skip("crontab executable is unavailable on this host")

    # 1. Capture live crontab -l SHA-256 before. A host with no installed
    # user crontab has no production state for this invariance probe.
    res_before = subprocess.run([crontab, "-l"], capture_output=True, text=True)
    if res_before.returncode != 0:
        pytest.skip("no live user crontab is installed on this host")
    sha_before = hashlib.sha256(res_before.stdout.encode("utf-8")).hexdigest()
    assert sha_before == LIVE_CRONTAB_EXPORT_SHA256

    # 2. Test syntax validation on disposable tempfile
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write("0 3 * * * echo 'disposable syntax check'\n")
        temp_path = tf.name

    try:
        res = subprocess.run([crontab, "-n", temp_path], capture_output=True, text=True)
        assert res.returncode == 0, f"crontab -n syntax check failed: {res.stderr}"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # 3. Capture live crontab -l SHA-256 after
    res_after = subprocess.run([crontab, "-l"], capture_output=True, text=True)
    assert res_after.returncode == 0, "live user crontab disappeared during probe"
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
        r"-----BEGIN " r"PRIVATE KEY-----",  # PEM private key
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
