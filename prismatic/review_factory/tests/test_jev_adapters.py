"""Tests for the L0 harness adapters (plan §9, "Adapters").

Per-adapter: a fixture harness output maps to a valid artifact
(``artifact.validate`` on ``artifact.to_dict()`` raises nothing);
``explicit_gaps`` lists what the harness could not provide and nothing is
fabricated (absent fields are null, never invented); unknown/malformed
harness output fails closed.

Adapters are pure translators — a source scan asserts no network-capable
imports in any adapter module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from prismatic.review_factory.adapters import (
    AdapterError,
    HarnessAdapter,
    adapter_harness_ids,
    get_adapter,
)
from prismatic.review_factory.artifact import (
    MAX_UNIFIED_DIFF_BYTES,
    ReviewArtifact,
    TRUNCATION_MARKER,
    validate,
)

ADAPTER_DIR = Path(__file__).resolve().parent.parent / "adapters"


def assert_valid(artifact: ReviewArtifact) -> dict[str, Any]:
    """Boundary validation passes; return the mapping form."""
    payload = artifact.to_dict()
    validate(payload)  # raises on any boundary violation
    return payload


def _rehashed(payload: dict[str, Any]) -> dict[str, Any]:
    """Recompute artifact_id after hand-editing a payload (test helper)."""
    core = {k: v for k, v in payload.items() if k != "artifact_id"}
    canonical = json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    payload["artifact_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


# ── fixtures: one realistic raw output per harness ─────────────────────────


def claude_raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "cc-20260923-001",
        "user_prompt": "Add retry logic to the deploy hook",
        "acceptance_criteria": ["retries 3x with backoff", "tests cover timeout"],
        "plan_path": "plans/deploy-retry.md",
        "base_tree": "a" * 64,
        "head_tree": "b" * 64,
        "diff": "diff --git a/hook.py b/hook.py\n+retry\n",
        "files_changed": [
            {
                "path": "hook.py",
                "change_type": "modified",
                "lines_added": 12,
                "lines_removed": 3,
            }
        ],
        "commands_run": [
            {
                "command": "pytest -q",
                "exit_code": 0,
                "log": "3 passed",
                "ran_at": "2026-09-23T23:10:00Z",
            }
        ],
        "prior_receipts": ["rcpt-1"],
        "submitted_at": "2026-09-23T23:15:00Z",
    }
    base.update(over)
    return base


def gemini_raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "gem-20260923-001",
        "prompt": "Refactor the retry helper",
        "model": "gemini-2.5-pro",
        "goals": ["helper is pure"],
        "plan_ref": "plans/retry.md",
        "base_commit": "c" * 40,
        "head_commit": "d" * 40,
        "diff_text": "diff --git a/retry.py b/retry.py\n+pure\n",
        "file_operations": [
            {
                "path": "retry.py",
                "operation": "modify",
                "lines_added": 8,
                "lines_removed": 2,
            }
        ],
        "shell_commands": [
            {
                "cmd": "pytest -q",
                "exit_code": 0,
                "output": "3 passed",
                "ran_at": "2026-09-23T23:10:00Z",
            }
        ],
        "submitted_at": "2026-09-23T23:15:00Z",
    }
    base.update(over)
    return base


def codex_raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "codex-20260923-001",
        "instructions": "Fix the flaky test",
        "goals": ["test passes 10/10 runs"],
        "plan_ref": "plans/flaky.md",
        "git_base": "e" * 40,
        "git_head": "f" * 40,
        "diff_patch": "diff --git a/test_x.py b/test_x.py\n+stable\n",
        "events": [
            {
                "type": "file_change",
                "path": "test_x.py",
                "operation": "modify",
                "lines_added": 4,
                "lines_removed": 1,
            },
            {"type": "reasoning", "text": "thinking..."},  # ignored, not a gap
            {
                "type": "command",
                "command": "pytest -q",
                "exit_status": 0,
                "output": "10 passed",
                "ran_at": "2026-09-23T23:10:00Z",
            },
        ],
        "submitted_at": "2026-09-23T23:15:00Z",
    }
    base.update(over)
    return base


def hermes_raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "hermes-fred-a1b2c3d4e5f6",
        "target": "fred",
        "service": "hermes-fred.service",
        "created_at": "2026-09-23T23:00:00Z",
        "finished_at": "2026-09-23T23:14:00Z",
        "status": "completed",
        "task": {
            "brief": "Triage the inbox",
            "goals": ["no message older than 24h unread"],
            "plan_ref": "plans/inbox.md",
        },
        "base_tree": "1" * 64,
        "head_tree": "2" * 64,
        "diff": "diff --git a/triage.py b/triage.py\n+triage\n",
        "files_changed": [
            {
                "path": "triage.py",
                "change_type": "modified",
                "lines_added": 5,
                "lines_removed": 1,
            }
        ],
        "check_results": [
            {
                "name": "pytest -q",
                "exit_code": 0,
                "log": "3 passed",
                "ran_at": "2026-09-23T23:10:00Z",
            }
        ],
        "submitted_at": "2026-09-23T23:15:00Z",
    }
    base.update(over)
    return base


def github_pr_raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pr_number": 545,
        "pr_title": "Close the repair loop",
        "pr_body": "Routes drainer through assigned-agent dispatch.",
        "head_sha": "73692e27" + "0" * 32,
        "base_sha": "daa72c6a" + "0" * 32,
        "state": "open",
        "mergeable": "MERGEABLE",
        # bare paths: the shadow feed's shape — no change_type, no line counts
        "files": ["prismatic/review_factory/drainer.py"],
        "diff_unified": "diff --git a/drainer.py b/drainer.py\n+route\n",
        "check_runs": [
            {
                "name": "review factory gate (tier A)",
                "status": "completed",
                "conclusion": "success",
                "completed_at": "2026-09-23T23:10:00Z",
            },
            {
                "name": "slow-integration",
                "status": "in_progress",
                "conclusion": None,
                "completed_at": None,
            },
        ],
        "submitted_at": "2026-09-23T23:15:00Z",
    }
    base.update(over)
    return base


_FIXTURES: dict[str, Any] = {
    "claude-code-cli": claude_raw,
    "gemini-cli": gemini_raw,
    "codex-cli": codex_raw,
    "hermes": hermes_raw,
    "github-pr": github_pr_raw,
}

_MINIMAL_RAW: dict[str, dict[str, Any]] = {
    "claude-code-cli": {"session_id": "cc-min"},
    "gemini-cli": {"session_id": "gem-min"},
    "codex-cli": {"session_id": "codex-min"},
    "hermes": {"run_id": "hermes-min-1"},
    "github-pr": {"pr_number": 1, "head_sha": "abc123"},
}

# Nullable schema paths that a minimal raw leaves null.
_MINIMAL_GAPS: dict[str, set[str]] = {
    "claude-code-cli": {
        "intent.brief",
        "intent.plan_ref",
        "diff.base_tree",
        "diff.head_tree",
        "diff.unified",
    },
    "gemini-cli": {
        "intent.brief",
        "intent.plan_ref",
        "diff.base_tree",
        "diff.head_tree",
        "diff.unified",
    },
    "codex-cli": {
        "intent.brief",
        "intent.plan_ref",
        "diff.base_tree",
        "diff.head_tree",
        "diff.unified",
    },
    "hermes": {
        "intent.brief",
        "intent.plan_ref",
        "diff.base_tree",
        "diff.head_tree",
        "diff.unified",
    },
    "github-pr": {
        "intent.brief",
        "intent.plan_ref",
        "diff.base_tree",
        "diff.unified",
    },
}


# ── registry ──────────────────────────────────────────────────────────────


def test_registry_covers_all_five_shipped_adapters():
    assert set(adapter_harness_ids()) == set(_FIXTURES)


def test_get_adapter_unknown_harness_id_fails_closed():
    with pytest.raises(KeyError):
        get_adapter("not-a-harness")


def test_adapters_satisfy_protocol():
    for harness_id in adapter_harness_ids():
        adapter = get_adapter(harness_id)
        assert isinstance(adapter, HarnessAdapter)
        assert adapter.harness_id == harness_id


# ── per-adapter: fixture -> valid artifact ─────────────────────────────────


@pytest.mark.parametrize("harness_id", sorted(_FIXTURES))
def test_fixture_maps_to_valid_artifact(harness_id: str):
    adapter = get_adapter(harness_id)
    raw = _FIXTURES[harness_id]()
    artifact = adapter.to_artifact(raw)
    payload = assert_valid(artifact)
    assert payload["schema_version"] == "artifact-v1"
    assert payload["harness_id"] == harness_id
    assert payload["harness_run_id"]  # opaque but non-empty
    assert len(payload["artifact_id"]) == 64
    # explicit_gaps on the artifact agrees with the standalone call
    assert payload["explicit_gaps"] == adapter.explicit_gaps(raw)
    # round-trip through the boundary
    assert ReviewArtifact.from_dict(payload) == artifact


def test_claude_code_field_mapping():
    artifact = get_adapter("claude-code-cli").to_artifact(claude_raw())
    assert_valid(artifact)
    assert artifact.intent.brief == "Add retry logic to the deploy hook"
    assert artifact.intent.goals == ["retries 3x with backoff", "tests cover timeout"]
    assert artifact.intent.plan_ref == "plans/deploy-retry.md"
    assert artifact.diff.files[0].path == "hook.py"
    assert artifact.diff.files[0].change_type == "modified"
    assert artifact.diff.files[0].lines_added == 12
    assert artifact.checks[0].name == "pytest -q"
    assert artifact.checks[0].exit_code == 0
    assert artifact.checks[0].ran_at == "2026-09-23T23:10:00Z"
    assert artifact.prior_receipts == ["rcpt-1"]
    assert artifact.novelty_context.first_seen_paths == ["hook.py"]
    assert artifact.explicit_gaps == []


def test_gemini_field_mapping():
    artifact = get_adapter("gemini-cli").to_artifact(gemini_raw())
    assert_valid(artifact)
    assert artifact.intent.brief == "Refactor the retry helper"
    # "modify" normalized into the closed vocabulary
    assert artifact.diff.files[0].change_type == "modified"
    assert artifact.checks[0].name == "pytest -q"
    assert artifact.explicit_gaps == []


def test_codex_event_stream_mapping():
    artifact = get_adapter("codex-cli").to_artifact(codex_raw())
    assert_valid(artifact)
    assert artifact.intent.brief == "Fix the flaky test"
    assert artifact.diff.files[0].path == "test_x.py"
    assert artifact.checks[0].exit_code == 0  # exit_status mapped
    assert artifact.explicit_gaps == []  # the ignored reasoning event is not a gap


def test_hermes_run_record_mapping():
    artifact = get_adapter("hermes").to_artifact(hermes_raw())
    assert_valid(artifact)
    assert artifact.harness_run_id == "hermes-fred-a1b2c3d4e5f6"
    assert artifact.intent.brief == "Triage the inbox"
    assert artifact.intent.goals == ["no message older than 24h unread"]
    assert artifact.explicit_gaps == []


def test_hermes_plain_string_task():
    raw = hermes_raw()
    raw["task"] = "Just do the triage"
    del raw["check_results"]
    artifact = get_adapter("hermes").to_artifact(raw)
    assert_valid(artifact)
    assert artifact.intent.brief == "Just do the triage"
    assert artifact.intent.goals == []
    assert artifact.explicit_gaps == ["intent.plan_ref"]


def test_github_pr_shadow_feed_mapping():
    adapter = get_adapter("github-pr")
    artifact = adapter.to_artifact(github_pr_raw())
    assert_valid(artifact)
    assert artifact.harness_run_id.startswith("github-pr-545-")
    assert artifact.intent.brief.startswith("Close the repair loop")
    assert artifact.diff.head_tree == "73692e27" + "0" * 32
    # completed check run mapped; conclusion success -> exit 0
    gate = next(c for c in artifact.checks if c.name == "review factory gate (tier A)")
    assert gate.exit_code == 0
    assert gate.ran_at == "2026-09-23T23:10:00Z"
    # mergeable MERGEABLE -> deterministic merge-state check, exit 0
    merge_state = next(c for c in artifact.checks if c.name == "merge-state")
    assert merge_state.exit_code == 0
    # unsettled run is NOT a check (an exit code would be fabricated)
    assert not any(c.name == "slow-integration" for c in artifact.checks)
    # bare paths: no honest diff.files entry — never guessed; path kept
    # for novelty
    assert artifact.diff.files == []
    assert artifact.novelty_context.first_seen_paths == [
        "prismatic/review_factory/drainer.py"
    ]
    # PRs carry no plan_ref
    assert artifact.explicit_gaps == ["intent.plan_ref"]


def test_github_pr_rich_file_entries_map():
    raw = github_pr_raw(
        files=[
            {
                "path": "new.py",
                "additions": 10,
                "deletions": 0,
                "lines_added": 10,
                "lines_removed": 0,
            },
            {
                "path": "old.py",
                "change_type": "deleted",
                "lines_added": 0,
                "lines_removed": 4,
            },
        ]
    )
    artifact = get_adapter("github-pr").to_artifact(raw)
    assert_valid(artifact)
    by_path = {f.path: f for f in artifact.diff.files}
    assert by_path["new.py"].change_type == "added"  # derived from additions/deletions
    assert by_path["old.py"].change_type == "deleted"


def test_github_pr_conflicting_merge_state():
    raw = github_pr_raw(mergeable="CONFLICTING")
    artifact = get_adapter("github-pr").to_artifact(raw)
    assert_valid(artifact)
    merge_state = next(c for c in artifact.checks if c.name == "merge-state")
    assert merge_state.exit_code == 1


def test_github_pr_failed_check_conclusion_maps_to_exit_1():
    raw = github_pr_raw(
        check_runs=[
            {
                "name": "tests",
                "status": "completed",
                "conclusion": "failure",
                "completed_at": "2026-09-23T23:10:00Z",
            }
        ]
    )
    artifact = get_adapter("github-pr").to_artifact(raw)
    assert_valid(artifact)
    check = next(c for c in artifact.checks if c.name == "tests")
    assert check.exit_code == 1


def test_check_without_ran_at_gaps_ran_at():
    raw = claude_raw(
        commands_run=[{"command": "pytest -q", "exit_code": 0, "log": "ok"}]
    )
    artifact = get_adapter("claude-code-cli").to_artifact(raw)
    assert_valid(artifact)
    assert artifact.checks[0].ran_at is None
    assert artifact.explicit_gaps == ["checks[].ran_at"]


def test_command_without_exit_code_is_omitted_not_fabricated():
    raw = claude_raw(commands_run=[{"command": "pytest -q", "log": "still running"}])
    artifact = get_adapter("claude-code-cli").to_artifact(raw)
    assert_valid(artifact)
    assert artifact.checks == []


# ── explicit_gaps: listed, never fabricated ────────────────────────────────


@pytest.mark.parametrize("harness_id", sorted(_MINIMAL_RAW))
def test_minimal_raw_lists_gaps_never_fabricates(harness_id: str):
    adapter = get_adapter(harness_id)
    raw = _MINIMAL_RAW[harness_id]
    artifact = adapter.to_artifact(raw)
    assert_valid(artifact)
    assert set(artifact.explicit_gaps) == _MINIMAL_GAPS[harness_id]
    assert adapter.explicit_gaps(raw) == artifact.explicit_gaps
    # every gap path resolves to a null value on the payload
    payload = artifact.to_dict()
    for gap in artifact.explicit_gaps:
        if gap == "checks[].log_sha256":
            assert all(c["log_sha256"] is None for c in payload["checks"])
        elif gap == "checks[].ran_at":
            assert all(c["ran_at"] is None for c in payload["checks"])
        else:
            target: Any = payload
            for part in gap.split("."):
                target = target[part]
            assert target is None, gap


# ── anti-fabrication boundary (workstream A's fail-closed validate) ─────────


def test_null_without_gap_fails_validation():
    artifact = get_adapter("claude-code-cli").to_artifact(claude_raw())
    payload = _rehashed(artifact.to_dict())
    payload["intent"]["brief"] = None  # null but not gapped
    payload = _rehashed(payload)
    with pytest.raises(Exception):
        validate(payload)


def test_gap_naming_non_null_field_fails_validation():
    artifact = get_adapter("claude-code-cli").to_artifact(claude_raw())
    payload = artifact.to_dict()
    payload["explicit_gaps"] = ["intent.brief"]  # brief is NOT null
    payload = _rehashed(payload)
    with pytest.raises(Exception):
        validate(payload)


def test_unknown_gap_path_fails_validation():
    artifact = get_adapter("github-pr").to_artifact(github_pr_raw())
    payload = artifact.to_dict()
    payload["explicit_gaps"] = payload["explicit_gaps"] + ["checks:slow:unsettled"]
    payload = _rehashed(payload)
    with pytest.raises(Exception):
        validate(payload)


def test_unknown_field_fails_validation():
    artifact = get_adapter("gemini-cli").to_artifact(gemini_raw())
    payload = _rehashed(artifact.to_dict())
    payload["bogus"] = 1
    payload = _rehashed(payload)
    with pytest.raises(Exception):
        validate(payload)


def test_tampered_artifact_id_fails_validation():
    artifact = get_adapter("gemini-cli").to_artifact(gemini_raw())
    payload = artifact.to_dict()
    payload["artifact_id"] = "0" * 64
    with pytest.raises(Exception):
        validate(payload)


# ── fail-closed ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("harness_id", sorted(_FIXTURES))
@pytest.mark.parametrize("bad_raw", ["garbage", None, 42, ["not", "a", "dict"]])
def test_non_mapping_raw_fails_closed(harness_id: str, bad_raw: Any):
    adapter = get_adapter(harness_id)
    with pytest.raises(AdapterError):
        adapter.to_artifact(bad_raw)
    with pytest.raises(AdapterError):
        adapter.explicit_gaps(bad_raw)


@pytest.mark.parametrize("harness_id", sorted(_FIXTURES))
def test_missing_run_id_fails_closed(harness_id: str):
    adapter = get_adapter(harness_id)
    with pytest.raises(AdapterError):
        adapter.to_artifact({"unrelated": "shape"})


def test_wrong_typed_goals_fails_closed():
    adapter = get_adapter("claude-code-cli")
    with pytest.raises(AdapterError):
        adapter.to_artifact(claude_raw(acceptance_criteria="not-a-list"))


# ── artifact properties ───────────────────────────────────────────────────


def test_artifact_id_is_deterministic_and_content_addressed():
    adapter = get_adapter("hermes")
    first = adapter.to_artifact(hermes_raw())
    second = adapter.to_artifact(hermes_raw())
    assert first.artifact_id == second.artifact_id
    changed = adapter.to_artifact(hermes_raw(submitted_at="2026-09-23T23:16:00Z"))
    assert changed.artifact_id != first.artifact_id


def test_oversize_diff_is_capped_with_truncation_marker():
    big_diff = "x" * (MAX_UNIFIED_DIFF_BYTES + 10_000)
    artifact = get_adapter("claude-code-cli").to_artifact(claude_raw(diff=big_diff))
    assert_valid(artifact)  # capped diff still passes the boundary check
    unified = artifact.diff.unified
    assert unified is not None
    assert len(unified.encode("utf-8")) <= MAX_UNIFIED_DIFF_BYTES
    assert TRUNCATION_MARKER.strip() in unified


def test_from_dict_round_trip():
    artifact = get_adapter("codex-cli").to_artifact(codex_raw())
    assert ReviewArtifact.from_dict(artifact.to_dict()) == artifact


# ── purity: adapters never touch the network ──────────────────────────────


def test_adapter_modules_have_no_network_or_side_effect_imports():
    banned = (
        "socket",
        "urllib",
        "http.client",
        "requests",
        "subprocess",
        "os.system",
        "os.popen",
        "shutil",
    )
    modules = [
        "__init__.py",
        "_artifact.py",
        "claude_code.py",
        "gemini_cli.py",
        "codex_cli.py",
        "hermes.py",
        "github_pr.py",
    ]
    for module in modules:
        source = (ADAPTER_DIR / module).read_text(encoding="utf-8")
        for token in banned:
            assert token not in source, f"{module} contains banned token {token!r}"
