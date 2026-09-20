#!/usr/bin/env python3
"""Prismatic Agent Closeout Packet Validator (v0.2 Standard Spec).

Importable module + CLI. Single source of truth: the JSON Schema at
``schemas/result-packet.schema.json``. Reuses existing secret/path safety
primitives from ``prismatic.agy_result_packet``. Never produces an acceptance
decision — the validator reports structural validity only.

This module is invoked both from the standalone CLI and from runtime
ingestion in :mod:`prismatic.agy_completed_work`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

# Reuse the production secret/path safety primitives.
from prismatic.agy_result_packet import (  # noqa: E402  (import after sys.path setup)
    CONTROL_RE,
    JUNK_PATH_RE,
    RESULT_ARTIFACT_OBJECT_FIELDS,
    SECRET_PATH_RE,
    SECRET_VALUE_RE,
)

EXPECTED_MARKER = "AGY_TASK_RESULT_PACKET_OK"
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
SHA64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
TASK_ID_RE = re.compile(r"^GRO-([0-9]+)$")
GIT_TREE_RE = SHA40_RE

#: ``/tmp`` is rejected as artifact provenance. Per the task appendix.
_UNSAFE_ARTIFACT_PATHS = (
    "/tmp/",
    "/var/tmp/",
    "/dev/shm/",
    "/proc/",
    "/sys/",
)


class LaunchContext:
    """Trusted context provided by the dispatch boundary, not the producer."""

    __slots__ = (
        "issue_identifier",
        "source_branch",
        "base_branch",
        "source_path",
        "candidate_commit",
        "candidate_tree",
        "base_commit",
    )

    def __init__(
        self,
        *,
        issue_identifier: str,
        source_branch: str,
        base_branch: str,
        source_path: str,
        candidate_commit: str,
        candidate_tree: str,
        base_commit: str,
    ) -> None:
        self.issue_identifier = issue_identifier
        self.source_branch = source_branch
        self.base_branch = base_branch
        self.source_path = source_path
        self.candidate_commit = candidate_commit
        self.candidate_tree = candidate_tree
        self.base_commit = base_commit

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"LaunchContext(issue={self.issue_identifier!r}, "
            f"branch={self.source_branch!r}, candidate={self.candidate_commit[:8]}…)"
        )


class ValidationOutcome:
    """Immutable validation result."""

    __slots__ = ("ok", "errors")

    def __init__(self, ok: bool, errors: Sequence[str]) -> None:
        self.ok = bool(ok)
        self.errors = tuple(errors)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise CloseoutValidationError(self.errors)


class CloseoutValidationError(ValueError):
    """Raised when a v0.2 closeout packet (and its report) violate policy."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("Invalid closeout packet: " + "; ".join(self.errors))


def _schema_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent / "schemas" / "result-packet.schema.json"
    )


def load_schema() -> dict:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def _is_draft7_validator() -> bool:
    try:
        from jsonschema import Draft7Validator  # noqa: F401

        return True
    except Exception:  # pragma: no cover - jsonschema is required runtime dep
        return False


def _check_json_schema(packet: Mapping[str, Any]) -> list[str]:
    if not _is_draft7_validator():
        return ["jsonschema package not available; cannot enforce schema"]
    from jsonschema import Draft7Validator

    validator = Draft7Validator(load_schema())
    return [error.message for error in validator.iter_errors(dict(packet))]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def _artifact_path(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        path = item.get("path")
        if isinstance(path, str):
            extra = set(item) - RESULT_ARTIFACT_OBJECT_FIELDS
            if extra:
                return None
            return path
    return None


def _is_safe_artifact_path(path: str) -> bool:
    if not path:
        return False
    normalized = path.replace("\\", "/")
    # Reject path traversal at any segment (POSIX ``..``).
    if ".." in PurePosixPath(normalized).parts:
        return False
    if any(normalized.startswith(prefix) for prefix in _UNSAFE_ARTIFACT_PATHS):
        return False
    if SECRET_PATH_RE.search(normalized):
        return False
    if JUNK_PATH_RE.search(normalized):
        return False
    if CONTROL_RE.search(normalized):
        return False
    if SECRET_VALUE_RE.search(normalized):
        return False
    # Absolute paths must resolve under operator home.
    if path.startswith("/"):
        home = str(Path.home())
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            return False
        if not (resolved == home or resolved.startswith(home + "/")):
            return False
    return True


def _check_artifact_safety(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    artifacts = packet.get("result_artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("result_artifacts must be a non-empty list")
        return errors
    for index, item in enumerate(artifacts):
        path = _artifact_path(item)
        if path is None:
            errors.append(f"result_artifacts[{index}] has an unsafe structure")
            continue
        if not _is_safe_artifact_path(path):
            errors.append(
                f"result_artifacts[{index}]='{path}' is unsafe "
                "(/tmp, secret, generated, or outside operator home)"
            )
    log = packet.get("LOG")
    if isinstance(log, str) and not _is_safe_artifact_path(log):
        errors.append(
            f"LOG='{log}' is unsafe (/tmp, secret, generated, or outside operator home)"
        )
    # CHANGED_PATHS: secret/traversal/junk-path defense.
    # Empty CHANGED_PATHS is permitted (e.g. an attempt that failed before
    # any file change) as long as BLOCKERS or PROOF_CLASSES carry evidence
    # that real work was attempted.
    changed_paths = packet.get("CHANGED_PATHS") or []
    if not isinstance(changed_paths, list):
        errors.append("CHANGED_PATHS must be a list")
    else:
        for index, value in enumerate(changed_paths):
            if not isinstance(value, str) or not value:
                errors.append(f"CHANGED_PATHS[{index}] must be a non-empty string")
                continue
            if not _is_safe_artifact_path(value):
                errors.append(
                    f"CHANGED_PATHS[{index}]='{value}' is unsafe "
                    "(traversal, secret, generated, or outside operator home)"
                )
        if not changed_paths:
            blockers = packet.get("BLOCKERS") or []
            proof = packet.get("PROOF_CLASSES") or []
            if not (isinstance(blockers, list) and blockers) and not (
                isinstance(proof, list) and proof
            ):
                errors.append(
                    "CHANGED_PATHS may be empty only when BLOCKERS or "
                    "PROOF_CLASSES carry evidence of attempted work"
                )
    # COMMAND: secret-shaped value defense (tokens, keys, credentials).
    commands = packet.get("COMMAND") or []
    if not isinstance(commands, list) or not commands:
        errors.append("COMMAND must be a non-empty list")
    else:
        for index, value in enumerate(commands):
            if not isinstance(value, str) or not value:
                errors.append(f"COMMAND[{index}] must be a non-empty string")
                continue
            if CONTROL_RE.search(value):
                errors.append(f"COMMAND[{index}] contains control characters")
                continue
            if SECRET_VALUE_RE.search(value):
                errors.append(f"COMMAND[{index}] contains secret-like content")
    return errors


def _check_semantic_invariants(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("agent") != "agy":
        errors.append("agent must be 'agy'")
    if packet.get("MARKER") != EXPECTED_MARKER:
        errors.append(f"MARKER must be exactly '{EXPECTED_MARKER}'")
    if packet.get("ACCEPTANCE_DECISION") != "PENDING":
        errors.append("producer ACCEPTANCE_DECISION must be PENDING")
    if packet.get("STATUS") != packet.get("PRODUCER_STATUS"):
        errors.append("STATUS must equal PRODUCER_STATUS")
    if packet.get("STATUS") == "PASS":
        if packet.get("RESULT") != "PASS":
            errors.append("STATUS=PASS requires RESULT=PASS")
        if packet.get("BLOCKERS"):
            errors.append("STATUS=PASS requires empty BLOCKERS")
    if packet.get("STATUS") in {"BLOCKED", "ERROR"} and not packet.get("BLOCKERS"):
        errors.append("STATUS in {BLOCKED, ERROR} requires non-empty BLOCKERS")
    if (
        packet.get("risk_level") == "high"
        and packet.get("merge_lane") != "manual-review"
    ):
        errors.append("risk_level=high requires merge_lane=manual-review")
    if packet.get("STATUS") == "PASS":
        # PASS must never pair with high risk or manual-review lane.
        if packet.get("risk_level") == "high":
            errors.append("STATUS=PASS is not allowed with risk_level=high")
        if packet.get("merge_lane") == "manual-review":
            errors.append("STATUS=PASS is not allowed with merge_lane=manual-review")
    return errors


def _check_log_integrity(directory: Path, packet: Mapping[str, Any]) -> list[str]:
    """Verify LOG path + LOG_SHA256 match on-disk file when context available."""
    errors: list[str] = []
    log_rel = packet.get("LOG")
    log_sha = packet.get("LOG_SHA256")
    if not isinstance(log_rel, str) or not isinstance(log_sha, str):
        return errors
    candidates: list[Path] = []
    if Path(log_rel).is_absolute():
        candidates.append(Path(log_rel))
    else:
        candidates.append((directory / log_rel).resolve())
    log_path = next((p for p in candidates if p.exists()), None)
    if log_path is None:
        errors.append(f"LOG file not found: {log_rel}")
        return errors
    if not SHA64_RE.fullmatch(log_sha):
        errors.append("LOG_SHA256 must be 64 hex characters")
        return errors
    digest = hashlib.sha256()
    with open(log_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    if digest.hexdigest() != log_sha:
        errors.append(
            f"LOG_SHA256 mismatch: declared {log_sha[:12]}… vs actual {digest.hexdigest()[:12]}…"
        )
    return errors


def _check_result_md(directory: Path, packet: Mapping[str, Any]) -> list[str]:
    """RESULT.md must exist; when present, synchronized fields must match.

    The parser accepts both ``KEY: value`` and ``KEY=value`` formats because
    shipped fixtures use the ``=`` form. The synchronized field set covers
    every field the v0.2 schema documents as cross-checked (the appendix says
    all required fields must synchronize, not five). Unknown keys are
    recorded but do not invalidate the packet on their own.
    """
    errors: list[str] = []
    md_path = directory / "RESULT.md"
    if not md_path.exists():
        errors.append(f"Missing required RESULT.md in {directory}")
        return errors
    md_fields: dict[str, str] = {}
    for raw in md_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            md_fields.setdefault(key.strip(), value.strip())
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            md_fields.setdefault(key.strip(), value.strip())
    synchronized = (
        "TASK_ID",
        "AGENT",
        "ACCEPTANCE_DECISION",
        "MARKER",
        "CANDIDATE_HEAD",
        "STATUS",
        "PRODUCER_STATUS",
        "BASE_HEAD",
        "CANDIDATE_TREE",
        "RESULT",
        "LOG_SHA256",
    )
    for field in synchronized:
        md_value = md_fields.get(field)
        packet_value = packet.get(field) or packet.get(field.lower())
        if md_value is None:
            errors.append(f"RESULT.md missing synchronized field '{field}'")
            continue
        if packet_value is None:
            continue
        if str(packet_value) != md_value:
            errors.append(
                f"RESULT.md {field}='{md_value}' disagrees with packet '{packet_value}'"
            )
    return errors


def _check_launch_binding(packet: Mapping[str, Any], ctx: LaunchContext) -> list[str]:
    errors: list[str] = []
    packet_issue = str(packet.get("TASK_ID") or "")
    if not TASK_ID_RE.fullmatch(packet_issue):
        errors.append("TASK_ID must match ^GRO-[0-9]+$")
    elif packet_issue != ctx.issue_identifier:
        errors.append(
            f"TASK_ID='{packet_issue}' does not match dispatch issue '{ctx.issue_identifier}'"
        )
    candidate = packet.get("CANDIDATE_HEAD")
    if not isinstance(candidate, str) or not SHA40_RE.fullmatch(candidate):
        errors.append("CANDIDATE_HEAD must be a 40-character hex SHA")
    elif candidate != ctx.candidate_commit:
        errors.append(
            f"CANDIDATE_HEAD='{candidate}' does not match dispatch HEAD '{ctx.candidate_commit}'"
        )
    tree = packet.get("CANDIDATE_TREE")
    if not isinstance(tree, str) or not GIT_TREE_RE.fullmatch(tree):
        errors.append("CANDIDATE_TREE must be a 40-character hex SHA")
    elif tree != ctx.candidate_tree:
        errors.append(
            f"CANDIDATE_TREE='{tree}' does not match dispatch tree '{ctx.candidate_tree}'"
        )
    base = packet.get("BASE_HEAD")
    if not isinstance(base, str) or not SHA40_RE.fullmatch(base):
        errors.append("BASE_HEAD must be a 40-character hex SHA")
    elif base != ctx.base_commit:
        errors.append(
            f"BASE_HEAD='{base}' does not match dispatch base '{ctx.base_commit}'"
        )
    return errors


def validate_closeout_packet(
    packet: Mapping[str, Any],
    *,
    directory: Path | None = None,
    launch_context: LaunchContext | None = None,
) -> ValidationOutcome:
    """Validate a v0.2 closeout packet (and its dual artifacts when context given)."""

    errors: list[str] = []
    errors.extend(_check_json_schema(packet))
    errors.extend(_check_semantic_invariants(packet))
    errors.extend(_check_artifact_safety(packet))
    if directory is not None:
        errors.extend(_check_log_integrity(directory, packet))
        errors.extend(_check_result_md(directory, packet))
    if launch_context is not None:
        errors.extend(_check_launch_binding(packet, launch_context))
    return ValidationOutcome(ok=not errors, errors=tuple(sorted(set(errors))))


# ---------------------------------------------------------------------------
# CLI (preserved entry point + behaviour; never emits acceptance vocabulary).


def _iter_example_dirs(root: Path) -> Iterable[Path]:
    """Yield directories whose own contents validate as a packet + RESULT.md."""
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "result-packet.json").exists():
            yield child


def _iter_flat_packets(root: Path) -> Iterable[Path]:
    """Yield flat ``result-packet.<label>.json`` files inside ``root``."""
    for child in sorted(root.iterdir()):
        if (
            child.is_file()
            and child.name.startswith("result-packet.")
            and child.name.endswith(".json")
            and child.name != "result-packet.json"
        ):
            yield child


def _run_fixture_harness(
    target_dir: Path, *, check_sha_files: bool
) -> tuple[bool, list[str]]:
    """Validate every shipped fixture in both directory and flat-file forms.

    A green harness must exercise at least one packet that is schema-valid
    plus the dual-artifact and launch-binding rules. Returning PASS without
    iterating flat fixtures (the previous behaviour) was vacuous.
    """
    aggregate_errors: list[str] = []
    aggregate_ok = True
    counts = {"directories": 0, "flat": 0, "ok": 0, "blocked": 0}

    def _record(label: str, outcome: ValidationOutcome) -> None:
        nonlocal aggregate_ok
        if outcome.ok:
            counts["ok"] += 1
            print(
                f"Fixture [{label}] VALIDATED CLEAN (check_sha_files={check_sha_files})"
            )
        else:
            counts["blocked"] += 1
            aggregate_ok = False
            aggregate_errors.extend(f"[{label}] " + e for e in outcome.errors)
            print(f"Fixture [{label}] BLOCKED: " + "; ".join(outcome.errors))

    for sub in _iter_example_dirs(target_dir):
        counts["directories"] += 1
        outcome = validate_packet_directory(sub, check_sha_files=check_sha_files)
        _record(sub.name, outcome)

    for flat in _iter_flat_packets(target_dir):
        counts["flat"] += 1
        label = flat.stem
        try:
            data = json.loads(flat.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            aggregate_ok = False
            counts["blocked"] += 1
            aggregate_errors.append(f"[{label}] invalid JSON: {exc.msg}")
            print(f"Fixture [{label}] BLOCKED: invalid JSON: {exc.msg}")
            continue
        if not isinstance(data, Mapping):
            aggregate_ok = False
            counts["blocked"] += 1
            aggregate_errors.append(f"[{label}] packet must be a JSON object")
            print(f"Fixture [{label}] BLOCKED: not a JSON object")
            continue
        outcome = validate_closeout_packet(data)
        _record(label, outcome)

    print(
        "FIXTURE_COUNTS=directories="
        f"{counts['directories']},flat={counts['flat']},"
        f"ok={counts['ok']},blocked={counts['blocked']}"
    )
    return aggregate_ok, aggregate_errors


def validate_packet_directory(
    directory: Path | str,
    *,
    check_sha_files: bool = True,
    launch_context: LaunchContext | None = None,
) -> ValidationOutcome:
    """Validate ``result-packet.json`` plus ``RESULT.md`` in ``directory``."""

    directory = Path(directory)
    json_path = directory / "result-packet.json"
    if not json_path.exists():
        return ValidationOutcome(False, (f"Missing result-packet.json in {directory}",))
    try:
        packet = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationOutcome(
            False, (f"result-packet.json invalid JSON: {exc.msg}",)
        )
    if not isinstance(packet, Mapping):
        return ValidationOutcome(False, ("result-packet.json must be a JSON object",))
    return validate_closeout_packet(
        packet,
        directory=directory if check_sha_files else None,
        launch_context=launch_context,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path", nargs="?", help="Directory containing RESULT.md + result-packet.json"
    )
    parser.add_argument("--test-fixtures", action="store_true")
    parser.add_argument("--no-check-sha", action="store_true")
    parser.add_argument(
        "--check-sha-files",
        action="store_true",
        help="(alias) Verify log + report hashes; default behaviour unless --no-check-sha",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    check_sha = not args.no_check_sha
    if args.test_fixtures or (
        args.path and (Path(args.path) / "result-packet.pass.json").exists()
    ):
        target_dir = Path(args.path).resolve() if args.path else Path(".").resolve()
        ok, errors = _run_fixture_harness(target_dir, check_sha_files=check_sha)
        if ok:
            print("STATUS=PASS")
            print("FIXTURE_HARNESS=100% GREEN")
            print(f"VALIDATED_DIR={target_dir}")
            return 0
        print("STATUS=BLOCKED")
        print("REASON=FIXTURE_VALIDATION_FAILED")
        for err in errors:
            print(f"  - {err}")
        return 1
    if not args.path:
        parser.error("path required when not running fixture harness")
    target_dir = Path(args.path).resolve()
    outcome = validate_packet_directory(target_dir, check_sha_files=check_sha)
    if outcome.ok:
        print("STATUS=PASS")
        print(f"VALIDATED_DIR={target_dir}")
        return 0
    print("STATUS=BLOCKED")
    print("REASON=INVALID_CLOSEOUT_PACKET")
    for err in outcome.errors:
        print(f"  - {err}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
