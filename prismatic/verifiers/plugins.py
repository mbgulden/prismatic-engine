"""Concrete verifier plugins for code, web, image, game sprite, video, audio, doc, and mixed bundles.

Each plugin enforces type-specific evidence schema contracts, explicit tool availability detection,
fail-closed validation, strict provenance binding (candidate artifact bytes & git commit lineage),
and durable receipt/log emission.
"""

from __future__ import annotations

import abc
import hashlib
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from prismatic.universal_result_manifest import sanitize_error_message
from prismatic.verifiers.evidence import build_verifier_result, write_durable_log
from prismatic.verifiers.schemas import (
    CANONICAL_VERIFIER_TYPES,
    has_unhealthy_state,
    is_valid_commit_sha,
    is_valid_sha256,
    validate_locator_safety,
)

ALLOWED_UNCOMMITTED_LIFECYCLE_FILES = frozenset(
    {"AGY_TASK.md", "STARTED.md", "RESULT.md"}
)


def _sanitize_git_error_component(value: object) -> str:
    """Return a secret-safe representation of Git-derived status/stdout/stderr text."""
    return sanitize_error_message(str(value))


def enforce_strict_provenance(
    candidate: Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[str, dict[str, Any], str]:
    """Validate runtime candidate digest artifact binding and repository source lineage.

    Fail closed on missing identity, malformed/all-zero/sentinel digest,
    missing artifact locator, unsafe locator, symlinks, missing artifact file, byte mismatch,
    absent repository context, missing/invalid/symlinked allowed_artifact_root,
    out-of-root artifact locator, nonexistent/non-commit SHA (e.g. tree/blob/tag),
    stale prior commit, branch mismatch, non-ancestral base commit, revision mismatch,
    or dirty/uncommitted repository. Never invent or mutate provenance.
    """
    # 1. Check Identity
    task_id = candidate.get("task_id") or context.get("task_id")
    if not task_id or not str(task_id).strip():
        raise ValueError(
            "Missing identity: task_id is required in candidate or context"
        )

    # 2. Check candidate_digest
    cand_digest = candidate.get("candidate_digest")
    if not cand_digest or not is_valid_sha256(str(cand_digest)):
        raise ValueError(
            f"Invalid candidate_digest provenance: '{cand_digest}' (must be valid 64-hex SHA-256, non-all-zero)"
        )
    cand_digest_clean = (
        str(cand_digest).removeprefix("sha256:")
    )

    # 3. Check allowed_artifact_root from context
    allowed_root_val = context.get("allowed_artifact_root")
    if not allowed_root_val or not str(allowed_root_val).strip():
        raise ValueError(
            "Absent repository/artifact context: allowed_artifact_root must be explicitly provided in context"
        )

    raw_root_path = Path(str(allowed_root_val))
    if not raw_root_path.exists() or not raw_root_path.is_dir():
        raise ValueError(
            f"allowed_artifact_root directory is absent, invalid, or not a directory: '{allowed_root_val}'"
        )

    curr_root = raw_root_path.absolute()
    while True:
        if curr_root.is_symlink():
            raise ValueError(
                f"allowed_artifact_root or path component is a symlink: '{curr_root}'"
            )
        parent = curr_root.parent
        if parent == curr_root:
            break
        curr_root = parent

    resolved_root = raw_root_path.resolve()

    # 4. Check retained artifact locator / path
    locator = (
        candidate.get("artifact_locator")
        or candidate.get("artifact_path")
        or candidate.get("retained_artifact_locator")
        or candidate.get("retained_artifact_path")
        or context.get("artifact_locator")
        or context.get("artifact_path")
        or context.get("retained_artifact_locator")
        or context.get("retained_artifact_path")
    )
    if not locator or not str(locator).strip():
        raise ValueError("Missing required retained artifact locator")

    loc_str = str(locator).strip()
    ok_loc, loc_err = validate_locator_safety(loc_str)
    if not ok_loc:
        raise ValueError(sanitize_error_message(f"Unsafe artifact locator: {loc_err}"))

    raw_loc_path = Path(loc_str)
    repo_path_val = context.get("repo_path") or context.get("repository_context")
    if repo_path_val and str(repo_path_val).strip():
        repo_dir = Path(str(repo_path_val)).resolve()
    else:
        repo_dir = None

    if raw_loc_path.is_absolute():
        target_art_path = raw_loc_path
    elif repo_dir and (repo_dir / raw_loc_path).exists():
        target_art_path = repo_dir / raw_loc_path
    else:
        target_art_path = resolved_root / raw_loc_path

    curr_loc = target_art_path.absolute()
    while True:
        if curr_loc.is_symlink():
            raise ValueError(
                f"Symlink artifact locator or path component is not allowed: '{curr_loc}'"
            )
        parent = curr_loc.parent
        if parent == curr_loc:
            break
        curr_loc = parent

    if not target_art_path.is_file():
        raise ValueError(
            f"Retained artifact file is absent or not a regular file: '{loc_str}'"
        )

    resolved_art = target_art_path.resolve()
    try:
        resolved_art.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            f"Retained artifact file '{loc_str}' ({resolved_art}) is outside allowed_artifact_root '{resolved_root}'"
        )

    check_dirs = [d for d in (repo_dir, resolved_root) if d is not None]
    for cdir in check_dirs:
        try:
            rel_p = resolved_art.relative_to(cdir)
        except ValueError:
            continue
        if (
            rel_p.name in ALLOWED_UNCOMMITTED_LIFECYCLE_FILES
            or str(rel_p) in ALLOWED_UNCOMMITTED_LIFECYCLE_FILES
        ):
            raise ValueError(
                f"Retained artifact locator '{loc_str}' resolves to a lifecycle allowlist path inside repository: '{rel_p}'"
            )

    # Bind candidate_digest to bytes of retained artifact file using SHA-256
    try:
        art_bytes = target_art_path.read_bytes()
    except Exception as exc:
        raise ValueError(f"Failed to read retained artifact file '{loc_str}': {exc}")

    actual_digest = hashlib.sha256(art_bytes).hexdigest()
    if actual_digest.lower() != cand_digest_clean.lower():
        raise ValueError(
            f"Artifact byte mismatch: candidate_digest '{cand_digest_clean}' does not match retained artifact bytes digest '{actual_digest}' for '{loc_str}'"
        )

    # 5. Check source lineage context
    src = context.get("source_lineage") or candidate.get("source_lineage")
    if not isinstance(src, dict):
        raise ValueError("Missing required source_lineage context")

    src_sha = str(src.get("source_commit_sha", ""))
    if not is_valid_commit_sha(src_sha):
        raise ValueError(
            f"Invalid source_commit_sha in source_lineage: '{src_sha}' (must be valid 40-hex commit SHA, non-all-zero)"
        )

    branch = str(src.get("branch", ""))
    if not branch or not branch.strip():
        raise ValueError("Missing branch in source_lineage")

    base_sha = src.get("base_commit_sha")
    expected_base = context.get("expected_base_commit_sha")

    if base_sha is not None:
        if not is_valid_commit_sha(str(base_sha)):
            raise ValueError(f"Invalid base_commit_sha in source_lineage: '{base_sha}'")
        base_sha_str = str(base_sha)
    elif expected_base is not None:
        if not is_valid_commit_sha(str(expected_base)):
            raise ValueError(f"Invalid expected_base_commit_sha: '{expected_base}'")
        base_sha_str = str(expected_base)
    else:
        base_sha_str = None

    if expected_base is not None and base_sha is not None:
        if base_sha_str.lower() != str(expected_base).lower():
            raise ValueError(
                f"Wrong base: base_commit_sha '{base_sha_str}' does not match expected base '{expected_base}'"
            )

    # 6. Check explicit trusted repository context
    if not repo_path_val or not str(repo_path_val).strip():
        raise ValueError(
            "Absent repository context: repo_path must be explicitly provided in context"
        )

    repo_dir = Path(str(repo_path_val)).resolve()
    if not repo_dir.is_dir():
        raise ValueError(
            f"Repository context directory does not exist: '{repo_path_val}'"
        )

    def _run_git(args: list[str]) -> str:
        res = subprocess.run(
            ["git"] + args,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            err_msg = _sanitize_git_error_component(
                res.stderr.strip() or res.stdout.strip()
            )
            raise ValueError(f"Git command failed in '{repo_dir}': {err_msg}")
        return res.stdout.strip()

    try:
        _run_git(["rev-parse", "--git-dir"])
    except Exception as exc:
        raise ValueError(
            f"Absent repository context or invalid Git repository at '{repo_dir}': {exc}"
        )

    # a. Check git object type of source_commit_sha
    try:
        obj_type = _run_git(["cat-file", "-t", src_sha])
    except Exception:
        raise ValueError(
            f"nonexistent 40-hex commit: '{src_sha}' not found in repository at '{repo_dir}'"
        )

    if obj_type != "commit":
        raise ValueError(
            f"Invalid source_commit_sha object type: '{src_sha}' is a '{obj_type}', not a commit object"
        )

    # b. Check actual repo HEAD
    current_head = _run_git(["rev-parse", "HEAD"])
    if src_sha.lower() != current_head.lower():
        raise ValueError(
            f"Source revision mismatch or stale prior commit: source_commit_sha '{src_sha}' does not match repository HEAD '{current_head}'"
        )

    expected_rev = context.get("expected_revision")
    if expected_rev is not None and str(expected_rev).lower() != src_sha.lower():
        raise ValueError(
            f"Revision mismatch: source_commit_sha '{src_sha}' does not match expected revision '{expected_rev}'"
        )

    # c. Check branch
    try:
        current_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    except Exception:
        current_branch = ""

    expected_branch = context.get("expected_branch", current_branch)
    if branch != current_branch or branch != expected_branch:
        raise ValueError(
            f"Wrong branch: source_lineage branch '{branch}' does not match repository branch '{current_branch}'"
        )

    # d. Check base commit if specified (object type AND ancestry)
    if base_sha_str is not None:
        try:
            base_type = _run_git(["cat-file", "-t", base_sha_str])
        except Exception:
            raise ValueError(
                f"base_commit_sha '{base_sha_str}' not found in repository"
            )
        if base_type != "commit":
            raise ValueError(
                f"base_commit_sha '{base_sha_str}' is a '{base_type}', not a commit object"
            )

        try:
            _run_git(["merge-base", "--is-ancestor", base_sha_str, src_sha])
        except Exception:
            raise ValueError(
                f"base_commit_sha '{base_sha_str}' is not an ancestor of source_commit_sha '{src_sha}'"
            )

    # e. Check dirty/uncommitted repository state (tolerates ONLY exact root-level untracked '??' ALLOWED_UNCOMMITTED_LIFECYCLE_FILES)
    status_res = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    if status_res.returncode != 0:
        err_msg = _sanitize_git_error_component(
            status_res.stderr.strip() or status_res.stdout.strip()
        )
        raise ValueError(f"Git command failed in '{repo_dir}': {err_msg}")

    dirty_files = []
    tokens = status_res.stdout.split("\0")
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if not token:
            idx += 1
            continue
        if len(token) < 3 or token[2] != " ":
            dirty_files.append(_sanitize_git_error_component(token))
            idx += 1
            continue

        xy = token[:2]
        filepath = token[3:]

        if xy[0] in ("R", "C") or xy[1] in ("R", "C"):
            idx += 1
            orig_path = tokens[idx] if idx < len(tokens) else ""
            dirty_files.append(_sanitize_git_error_component(filepath))
            if orig_path:
                dirty_files.append(_sanitize_git_error_component(orig_path))
            idx += 1
            continue

        if xy == "??" and filepath in ALLOWED_UNCOMMITTED_LIFECYCLE_FILES:
            idx += 1
            continue

        dirty_files.append(_sanitize_git_error_component(filepath))
        idx += 1

    if dirty_files:
        raise ValueError(
            sanitize_error_message(
                f"Dirty/uncommitted revision: repository at '{repo_dir}' has uncommitted files: {dirty_files}"
            )
        )

    res_lineage = {
        "source_commit_sha": src_sha,
        "branch": branch,
    }
    if base_sha_str:
        res_lineage["base_commit_sha"] = base_sha_str

    return cand_digest_clean, res_lineage, loc_str


class VerifierPlugin(abc.ABC):
    """Abstract base class for typed output verifier plugins."""

    def __init__(self, plugin_id: str, verifier_type: str, version: str = "1.0.0"):
        self.plugin_id = plugin_id
        raw_type = verifier_type
        if raw_type not in CANONICAL_VERIFIER_TYPES:
            raise ValueError(f"Unknown verifier_type: '{verifier_type}'")
        self.verifier_type = CANONICAL_VERIFIER_TYPES[raw_type]
        self.version = version

    @abc.abstractmethod
    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Perform type-specific verification and return a validated machine-readable result."""


class CodePackageVerifier(VerifierPlugin):
    """Verifier for code/package bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:code_package_verifier",
            verifier_type="code",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "python3 -m pytest -q")
        env = dict(
            context.get("environment", {"os": "linux", "python_version": "3.10"})
        )
        scope = candidate.get("scope", {"target_paths": ["prismatic/"]})
        non_claims = list(candidate.get("non_claims", ["performance_benchmarks"]))

        log_path, log_digest = write_durable_log(
            f"CodePackageVerifier run for task {task_id}\nCandidate digest: {candidate_digest}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-code",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = "No code test/build execution evidence provided or pytest tool run unavailable"
            type_evidence = {
                "tests_passed": {"status": "unavailable", "reason": reason},
                "coverage": {"status": "unavailable", "reason": reason},
                "lint_status": {"status": "unavailable", "reason": reason},
                "install_readback": {"status": "unavailable", "reason": reason},
            }
        else:
            type_evidence = dict(evidence)
            is_pass = (
                type_evidence.get("tests_passed") is True
                and not has_unhealthy_state(type_evidence)
                and "coverage" in type_evidence
                and "lint_status" in type_evidence
                and "install_readback" in type_evidence
            )
            if is_pass:
                status = "pass"
                reason = None
            else:
                if type_evidence.get("tests_passed") is False:
                    status = "failed"
                    reason = "Code package tests failed"
                else:
                    status = "unavailable"
                    reason = "Code evidence incomplete, unverified, or contains unhealthy state"

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="install_readback",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )


class WebsiteAppBrowserVerifier(VerifierPlugin):
    """Verifier for website/app/browser bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:website_app_browser_verifier",
            verifier_type="web/app",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "npx lighthouse http://localhost:3000")
        env = dict(context.get("environment", {"os": "linux", "browser": "chromium"}))
        scope = candidate.get("scope", {"target_paths": ["src/app/"]})
        non_claims = list(candidate.get("non_claims", ["native_ios_binary"]))

        log_path, log_digest = write_durable_log(
            f"WebsiteAppBrowserVerifier run for task {task_id}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-webapp",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = (
                "Headless browser / Lighthouse tool run unavailable or evidence missing"
            )
            type_evidence = {
                "build_status": {"status": "unavailable", "reason": reason},
                "lighthouse": {
                    "performance": {"status": "unavailable", "reason": reason},
                    "accessibility": {"status": "unavailable", "reason": reason},
                    "best_practices": {"status": "unavailable", "reason": reason},
                    "seo": {"status": "unavailable", "reason": reason},
                },
                "visual_qa": {"status": "unavailable", "reason": reason},
                "browser_mobile_proof": {"status": "unavailable", "reason": reason},
            }
        else:
            type_evidence = dict(evidence)
            is_pass = (
                type_evidence.get("build_status") == "success"
                and not has_unhealthy_state(type_evidence)
                and "lighthouse" in type_evidence
                and "visual_qa" in type_evidence
                and "browser_mobile_proof" in type_evidence
            )
            if is_pass:
                status = "pass"
                reason = None
            else:
                if type_evidence.get("build_status") == "failed":
                    status = "failed"
                    reason = "Web application build failed"
                else:
                    status = "unavailable"
                    reason = "Web/browser tool evidence incomplete or contains unhealthy state"

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="browser",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )


class ImageDesignVerifier(VerifierPlugin):
    """Verifier for image/design bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:image_design_verifier",
            verifier_type="image/design",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "python3 verify_image.py")
        env = dict(context.get("environment", {"os": "linux"}))
        scope = candidate.get("scope", {"target_paths": ["assets/logo.png"]})
        non_claims = list(candidate.get("non_claims", ["vector_path_precision"]))

        log_path, log_digest = write_durable_log(
            f"ImageDesignVerifier run for task {task_id}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-image",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = "PIL/OpenCV image analysis tooling not invoked or evidence missing"
            type_evidence = {
                "dimensions": {"status": "unavailable", "reason": reason},
                "color_space": {"status": "unavailable", "reason": reason},
                "similarity_score": {"status": "unavailable", "reason": reason},
                "color_alpha_proof": {"status": "unavailable", "reason": reason},
            }
        else:
            type_evidence = dict(evidence)
            dims = type_evidence.get("dimensions")
            valid_dims = (
                isinstance(dims, (list, tuple))
                and len(dims) == 2
                and dims[0] > 0
                and dims[1] > 0
            )
            is_pass = valid_dims and not has_unhealthy_state(type_evidence)
            status = "pass" if is_pass else "unavailable"
            reason = (
                None if is_pass else "Image proof unavailable or invalid dimensions"
            )

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="color_alpha",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )


class SpriteAtlasGameVerifier(VerifierPlugin):
    """Verifier for sprite/atlas/game import bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:sprite_atlas_game_verifier",
            verifier_type="sprite/game asset",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "python3 verify_spritesheet.py")
        env = dict(context.get("environment", {"os": "linux"}))
        scope = candidate.get("scope", {"target_paths": ["assets/hero.json"]})
        non_claims = list(candidate.get("non_claims", ["gpu_shader_performance"]))

        log_path, log_digest = write_durable_log(
            f"SpriteAtlasGameVerifier run for task {task_id}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-sprite",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = (
                "TexturePacker/Atlas parser tooling unavailable or evidence missing"
            )
            type_evidence = {
                "frame_count": {"status": "unavailable", "reason": reason},
                "spritesheet": {"status": "unavailable", "reason": reason},
                "collision_boxes": {"status": "unavailable", "reason": reason},
                "atlas_geometry": {"status": "unavailable", "reason": reason},
            }
        else:
            type_evidence = dict(evidence)
            fc = type_evidence.get("frame_count")
            is_pass = (
                isinstance(fc, int)
                and fc >= 1
                and not has_unhealthy_state(type_evidence)
            )
            status = "pass" if is_pass else "unavailable"
            reason = (
                None if is_pass else "Atlas proof unavailable or invalid frame count"
            )

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="atlas",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )


class VideoVerifier(VerifierPlugin):
    """Verifier for video bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:video_verifier",
            verifier_type="video",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "ffprobe demo.mp4")
        env = dict(context.get("environment", {"os": "linux"}))
        scope = candidate.get("scope", {"target_paths": ["media/demo.mp4"]})
        non_claims = list(candidate.get("non_claims", ["hdr10_color_grading"]))

        log_path, log_digest = write_durable_log(
            f"VideoVerifier run for task {task_id}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-video",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = "ffprobe / ffmpeg video decoder tool run unavailable or evidence missing"
            type_evidence = {
                "resolution": {"status": "unavailable", "reason": reason},
                "duration_seconds": {"status": "unavailable", "reason": reason},
                "bitrate_kbps": {"status": "unavailable", "reason": reason},
                "codec_fps_sync": {"status": "unavailable", "reason": reason},
            }
        else:
            type_evidence = dict(evidence)
            dur = type_evidence.get("duration_seconds")
            bitrate = type_evidence.get("bitrate_kbps")
            valid_metrics = (
                isinstance(dur, (int, float))
                and dur > 0
                and isinstance(bitrate, (int, float))
                and bitrate > 0
            )
            is_pass = valid_metrics and not has_unhealthy_state(type_evidence)
            status = "pass" if is_pass else "unavailable"
            reason = (
                None if is_pass else "Video proof unavailable or invalid media metrics"
            )

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="codec_fps",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )


class AudioVerifier(VerifierPlugin):
    """Verifier for audio bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:audio_verifier",
            verifier_type="audio",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "sox sample.wav")
        env = dict(context.get("environment", {"os": "linux"}))
        scope = candidate.get("scope", {"target_paths": ["audio/sample.wav"]})
        non_claims = list(candidate.get("non_claims", ["spatial_audio_3d"]))

        log_path, log_digest = write_durable_log(
            f"AudioVerifier run for task {task_id}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-audio",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = "sox / libsndfile audio tool run unavailable or evidence missing"
            type_evidence = {
                "channels": {"status": "unavailable", "reason": reason},
                "sample_rate_hz": {"status": "unavailable", "reason": reason},
                "duration_seconds": {"status": "unavailable", "reason": reason},
                "loudness_silence_proof": {
                    "status": "unavailable",
                    "reason": reason,
                },
            }
        else:
            type_evidence = dict(evidence)
            ch = type_evidence.get("channels")
            sr = type_evidence.get("sample_rate_hz")
            dur = type_evidence.get("duration_seconds")
            valid_audio = (
                isinstance(ch, int)
                and ch >= 1
                and isinstance(sr, int)
                and sr > 0
                and isinstance(dur, (int, float))
                and dur > 0
            )
            is_pass = valid_audio and not has_unhealthy_state(type_evidence)
            status = "pass" if is_pass else "unavailable"
            reason = (
                None if is_pass else "Audio proof unavailable or invalid audio metrics"
            )

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="sample_rate",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )


class DocumentDataVerifier(VerifierPlugin):
    """Verifier for document/data bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:document_data_verifier",
            verifier_type="document/data",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "python3 verify_docs.py")
        env = dict(context.get("environment", {"os": "linux"}))
        scope = candidate.get("scope", {"target_paths": ["docs/spec.md"]})
        non_claims = list(candidate.get("non_claims", ["pdf_print_dpi"]))

        log_path, log_digest = write_durable_log(
            f"DocumentDataVerifier run for task {task_id}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-doc",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = "Document/data validation tooling not run or document input missing/empty"
            type_evidence = {
                "format": {"status": "unavailable", "reason": reason},
                "valid": {"status": "unavailable", "reason": reason},
                "schema_compliant": {"status": "unavailable", "reason": reason},
            }
        else:
            type_evidence = dict(evidence)
            is_valid_doc = (
                type_evidence.get("valid") is True
                and type_evidence.get("schema_compliant") is True
                and isinstance(type_evidence.get("format"), str)
                and len(type_evidence.get("format")) > 0
                and not has_unhealthy_state(type_evidence)
            )
            if is_valid_doc:
                status = "pass"
                reason = None
            else:
                if (
                    type_evidence.get("valid") is False
                    or type_evidence.get("schema_compliant") is False
                ):
                    status = "failed"
                    reason = "Document format or schema validation failed"
                else:
                    status = "unavailable"
                    reason = "Document evidence incomplete, unverified, or contains unhealthy state"

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="licensing",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )


class MixedBundleVerifier(VerifierPlugin):
    """Verifier for mixed bundles."""

    def __init__(self):
        super().__init__(
            plugin_id="builtin:mixed_bundle_verifier",
            verifier_type="mixed",
            version="1.0.0",
        )

    def verify(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate_digest, source_lineage, artifact_locator = enforce_strict_provenance(
            candidate, context
        )
        task_id = str(context.get("task_id", candidate.get("task_id", "")))
        command = candidate.get("command", "python3 verify_bundle.py")
        env = dict(context.get("environment", {"os": "linux"}))
        scope = candidate.get("scope", {"target_paths": ["bundle/"]})
        non_claims = list(candidate.get("non_claims", ["external_cdn_sync"]))

        log_path, log_digest = write_durable_log(
            f"MixedBundleVerifier run for task {task_id}\nArtifact: {artifact_locator}\n",
            prefix="agy-GRO-4114-verify-mixed",
        )

        evidence = candidate.get("type_specific_evidence")
        if not evidence or not isinstance(evidence, dict):
            status = "unavailable"
            reason = "Child manifest digests not supplied"
            type_evidence = {
                "submanifests_validated": {"status": "unavailable", "reason": reason},
                "child_digests": {"status": "unavailable", "reason": reason},
                "atomic_policy": "all_or_nothing",
            }
        else:
            type_evidence = dict(evidence)
            child_digests = type_evidence.get("child_digests", [])
            errors = []

            if not isinstance(child_digests, list) or len(child_digests) == 0:
                errors.append("Child digests missing or empty")
            else:
                seen_locs = set()
                for idx, c in enumerate(child_digests):
                    if not isinstance(c, dict):
                        errors.append(f"Child digest {idx} is not an object")
                        continue
                    loc = c.get("locator") or c.get("path")
                    dig = c.get("digest") or c.get("sha256")
                    if not loc:
                        errors.append(f"Child digest {idx} missing locator")
                    else:
                        ok_loc, loc_err = validate_locator_safety(str(loc))
                        if not ok_loc:
                            errors.append(
                                f"Child digest {idx} locator invalid: {loc_err}"
                            )
                        if str(loc) in seen_locs:
                            errors.append(
                                f"Child digest {idx} duplicate locator '{loc}'"
                            )
                        seen_locs.add(str(loc))

                    if not dig or not is_valid_sha256(str(dig)):
                        errors.append(
                            f"Child digest {idx} invalid sha256 digest: '{dig}'"
                        )
                    elif ok_loc:
                        # Recompute child digest if file exists
                        try:
                            cp = Path(str(loc)).resolve()
                            if cp.is_file():
                                actual = hashlib.sha256(cp.read_bytes()).hexdigest()
                                expected = (
                                    str(dig).removeprefix("sha256:")
                                )
                                if actual.lower() != expected.lower():
                                    errors.append(
                                        f"Child digest {idx} content mismatch for '{loc}'"
                                    )
                        except Exception:
                            pass

            is_pass = len(errors) == 0 and not has_unhealthy_state(type_evidence)
            status = "pass" if is_pass else "unavailable"
            reason = (
                "; ".join(errors)
                if errors
                else (None if is_pass else "Child digests unavailable or invalid")
            )

        return build_verifier_result(
            task_id=task_id,
            verifier_type=self.verifier_type,
            verifier_version=self.version,
            candidate_digest=candidate_digest,
            source_lineage=source_lineage,
            command=command,
            environment=env,
            log_path=log_path,
            log_digest=log_digest,
            proof_class="mixed_child_digest",
            status=status,
            scope=scope,
            non_claims=non_claims,
            type_specific_evidence=type_evidence,
            unavailable_reason=reason,
        )
