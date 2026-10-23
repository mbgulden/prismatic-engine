"""RF-2: Deterministic Verification Worker — Deep PE Integration.

Produces ``VerificationEvidence`` objects (from ``merge_candidate_manifest``)
and advances the manifest from ``CANDIDATE`` → ``REVIEW_REQUIRED`` via
``manifest.request_review(evidence_list)``.

Three integrity invariants (baked in, not bolt-on):
    1. **Integration Import Invariant** — grep for expected PE imports.
       If the module is supposed to integrate with a PE surface, the
       import MUST be present. A missing import means stub code.
    2. **Circular Proof Detection** — verify tests import external PE
       modules. If tests only import from the module under test, they
       validate the stub against itself (hollow green).
    3. **Call-site Check** — verify the imported function is actually
       called. Import-but-don't-use is a subtler hollow pattern.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tokenize
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    VerificationEvidence,
)
from prismatic.review_factory.models import (
    ReviewJob,
    VerificationReceipt,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Proof class requirements per risk tier
# ─────────────────────────────────────────────────────────────────────

_PROOF_REQUIREMENTS: dict[str, set[str]] = {
    "A": {"focused"},
    "B": {"focused", "canonical", "package"},
    "C": {"focused", "canonical", "package", "failure", "recovery", "rollback"},
}
_DASHBOARD_EXTRA = {"browser", "real_data"}


# ─────────────────────────────────────────────────────────────────────
# Integration Import Contracts (Invariant 1)
# ─────────────────────────────────────────────────────────────────────

INTEGRATION_IMPORT_CONTRACTS: dict[str, list[str]] = {
    "prismatic/review_factory/verifier.py": [
        "from prismatic.merge_candidate_manifest import",
    ],
    "prismatic/review_factory/reviewer.py": [
        "from prismatic.review import",
        "from prismatic.merge_candidate_manifest import",
    ],
    "prismatic/review_factory/merge_executor.py": [
        "from prismatic.integrate import",
        "from prismatic.merge_candidate_manifest import",
        "from prismatic.state_machine import",
    ],
    "prismatic/review_factory/backlog_importer.py": [
        "from prismatic.agy_completed_work import",
    ],
    "prismatic/review_factory/routes.py": [
        "from prismatic.review_factory.db import",
        "from prismatic.review_factory.queue import",
    ],
}

# ─────────────────────────────────────────────────────────────────────
# Integration Call-Site Contracts (Invariant 3)
# ─────────────────────────────────────────────────────────────────────

INTEGRATION_CALLSITE_CONTRACTS: dict[str, list[str]] = {
    "prismatic/review_factory/verifier.py": [
        "request_review",
    ],
    "prismatic/review_factory/reviewer.py": [
        "review_pr",
        "record_review",
    ],
    "prismatic/review_factory/merge_executor.py": [
        "integrate_pipeline_run",
        "mark_merged",
    ],
    "prismatic/review_factory/backlog_importer.py": [
        "AgyCompletedWorkStore",
    ],
}


@dataclass
class CheckResult:
    """Outcome of a single verification check."""

    name: str
    proof_class: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    log_path: str = ""
    log_sha256: str = ""
    passed: bool = True


@dataclass(frozen=True)
class MaterializedArchive:
    """Exact Git archive extracted into a read-only verification root."""

    path: Path
    artifact_sha256: str


# ─────────────────────────────────────────────────────────────────────
# Content-aware diff classification (tier A focused gate)
# ─────────────────────────────────────────────────────────────────────


def _code_tokens(source: str) -> list[str]:
    """Ordered list of code-level tokens for *source*.

    Comments and string literals (including docstrings) are replaced by
    a single placeholder token so that comment/docstring rewording is
    invisible to the comparison, while any code-token change — including
    reordering — is visible. Order matters deliberately: a value swap
    (``A = 1, B = 2`` vs ``A = 2, B = 1``) is a real behavior change
    with an identical token multiset, and must NOT be classified as
    comment-only.

    ``tokenize`` (not ``ast``) is used on purpose: docstrings are
    string-literal nodes in the AST, so an AST walk cannot distinguish
    a docstring reword from a real string-literal change. The tokenizer
    treats both uniformly — which is the conservative, fail-safe choice
    here, because a genuine code change is always still classified as
    code.
    """
    tokens: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                tokens.append("<lit>")
            elif tok.type == tokenize.NAME:
                tokens.append(tok.string)
            elif tok.type == tokenize.NUMBER:
                tokens.append(tok.string)
            elif tok.type == tokenize.ENCODING:
                continue
            elif tok.type == tokenize.ENDMARKER:
                break
            else:
                tokens.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # Unparseable source cannot be proven comment-only: the caller
        # must treat this as a code change (fail closed).
        return None  # type: ignore[return-value]
    return tokens


def _is_code_change(
    repo_path: Path,
    base_commit: str,
    diff_text: str,
    rel_path: str,
) -> bool:
    """Classify a changed ``.py`` file by CONTENT, not extension.

    Returns ``True`` when the change carries a code-token difference
    (or cannot be proven comment-only), ``False`` when the diff is
    comment/docstring-only — in which case the file carries no test
    surface and the focused gate exempts it exactly as it does for
    non-.py docs changes.

    Classification (most authoritative source wins):
      1. If the base blob is reachable in the repo
         (``git show <base>:<path>``), compare code-token lists of
         base vs worktree source. Any difference is a code change;
         unparseable either side fails closed.
      2. Otherwise, if a unified diff for the file is available,
         reconstruct the full base-side and new-side sources from the
         full-context diff and compare code-token sequences (identical
         to path 1). This is the path taken in CI, where the verifier
         materializes only the candidate tree and the base blob is not
         in the materialized root.
      3. Otherwise (no diff text at all — daemon/tmp shapes) fall back
         to extension-only classification: a changed ``.py`` is code
         (preserves the pre-existing fail-closed behavior).

    Non-``.py`` paths are never code: they are the docs/config/data
    exemption handled upstream.
    """
    if not rel_path.endswith(".py"):
        return False

    base_src = _git_show_blob(repo_path, base_commit, rel_path)
    if base_src is not None:
        new_src = _worktree_source(repo_path, rel_path)
        if new_src is None:
            # Deleted in worktree (or unreadable) -> not provable
            # comment-only.
            return True
        base_tokens = _code_tokens(base_src)
        new_tokens = _code_tokens(new_src)
        if base_tokens is None or new_tokens is None:
            return True  # unparseable -> fail closed
        return base_tokens != new_tokens

    if diff_text:
        # CI path: the verification root is a materialized plain
        # directory (no git metadata), so the base blob is unreachable
        # and the unified diff is the only content source. Reconstruct
        # the full base-side and new-side sources from the full-context
        # diff (the gate driver emits --unified=99999) and compare
        # code-token sequences — identical to the base-blob path.
        base_src, new_src = _diff_reconstruct(diff_text, rel_path)
        if base_src is None or new_src is None:
            return True  # cannot reconstruct -> fail closed
        base_tokens = _code_tokens(base_src)
        new_tokens = _code_tokens(new_src)
        if base_tokens is None or new_tokens is None:
            return True  # unparseable -> fail closed
        return base_tokens != new_tokens

    # No base blob, no diff: cannot prove comment-only. Preserve the
    # extension-only fail-closed behavior (the #619 invariant).
    return True


def _git_show_blob(repo_path: Path, base_commit: str, rel_path: str) -> Optional[str]:
    """Read the base-blob source for *rel_path*, or None if unreachable."""
    if not base_commit or base_commit in ("0" * 40,):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "show", f"{base_commit}:{rel_path}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout


def _worktree_source(repo_path: Path, rel_path: str) -> Optional[str]:
    try:
        return (repo_path / rel_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _diff_reconstruct(
    diff_text: str, rel_path: str
) -> tuple[Optional[str], Optional[str]]:
    """Reconstruct base-side and new-side full file contents from *diff_text*.

    Walks the file's unified-diff hunk(s), collecting context + removed
    lines into the base-side source and context + added lines into the
    new-side source. Requires a full-context diff (the gate driver
    emits ``--unified=99999``) that covers the ENTIRE file: the hunk
    must start at base line 1 and its base-side span must reach the
    last base line (or the file must be empty). Otherwise the
    reconstruction is incomplete and ``(None, None)`` is returned so
    the caller fails closed.

    Returns ``(base_src, new_src)``. A deleted file (no ``+`` lines)
    yields ``new_src == ""``; an added file (no ``-`` lines) yields
    ``base_src == ""``.
    """
    norm_rel = rel_path.replace(os.sep, "/")
    in_file = False
    in_hunk = False  # content is only valid inside a hunk
    base_lines: list[str] = []
    new_lines: list[str] = []
    # Hunk coverage bookkeeping (validated after the walk).
    hunk_starts: list[int] = []  # base-side start line number per hunk
    hunk_base_span = 0  # base lines consumed across all hunks
    hunk_new_span = 0  # new lines consumed across all hunks
    hunk_count = 0
    file_seen = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if in_file:
                break  # we only care about the first match; done.
            in_file = _diff_file_is_match(line, norm_rel)
            if in_file:
                file_seen = True
            in_hunk = False
            continue
        if not in_file:
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("index "):
            # Binary/metadata line between the file header and the hunks.
            continue
        if line.startswith("\\"):
            # "\ No newline at end of file" marker: git appends this when
            # a file side lacks a trailing newline. It is not content.
            continue
        if line.startswith("@@"):
            # Hunk header: "@@ -a,b +c,d @@ ...". b (and d) default to 1
            # when omitted.
            m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m is None:
                return None, None  # malformed hunk header -> fail closed
            hunk_starts.append(int(m.group(1)))
            hunk_base_span += int(m.group(2) or 1)
            hunk_new_span += int(m.group(4) or 1)
            hunk_count += 1
            in_hunk = True
            continue
        if not in_hunk:
            # Metadata lines can appear between the file header and the
            # first hunk ("new file mode", "deleted file mode", "rename
            # from/to", "old mode"/"new mode", "similarity index"). They
            # carry no content; skip them. Truncation is caught by the
            # hunk count/span validation after the walk.
            continue
        if line.startswith("-"):
            base_lines.append(line[1:])
        elif line.startswith("+"):
            new_lines.append(line[1:])
        else:
            # Context line. git emits a leading space; an empty context
            # line (blank line in the file) may arrive as the bare ""
            # after splitlines in some diffs.
            base_lines.append(line[1:] if line.startswith(" ") else line)
            new_lines.append(line[1:] if line.startswith(" ") else line)

    if not file_seen or hunk_count == 0:
        return None, None

    # A full-context diff of a changed file has exactly one hunk starting
    # at base line 1 (or, for a file that did not exist at base, an
    # addition hunk "@@ -0,0 +1,N @@" with an empty base span). Anything
    # else means the context is truncated and the reconstruction is
    # incomplete -> fail closed.
    if hunk_starts[0] != 1 and not (hunk_starts[0] == 0 and hunk_base_span == 0):
        return None, None  # truncated context: hunk doesn't start at top

    # The counted content lines must agree with the hunk headers, else
    # the diff is malformed/truncated for this file -> fail closed.
    if len(base_lines) != hunk_base_span or len(new_lines) != hunk_new_span:
        return None, None

    base_src = "\n".join(base_lines)
    new_src = "\n".join(new_lines)
    return base_src, new_src


def _diff_file_is_match(diff_line: str, norm_rel: str) -> bool:
    """Match a ``diff --git`` header to *norm_rel* (handles renames)."""
    # Forms: "diff --git a/X b/X" or "diff --git a/X b/Y" (rename)
    parts = diff_line.split()
    for i, part in enumerate(parts):
        if i == 0 or part.startswith("--git"):
            continue
        cleaned = part[2:] if part[:2] in ("a/", "b/") else part
        if cleaned == norm_rel:
            return True
    return False


class VerificationWorker:
    """RF-2: Run deterministic verification and produce VerificationEvidence."""

    def __init__(
        self,
        repo_path: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        test_mode: bool = False,
    ):
        self.repo_path = repo_path or Path(".")
        self.log_dir = log_dir or Path(tempfile.mkdtemp(prefix="rf-verify-"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.test_mode = test_mode

    def _materialize_immutable_archive(
        self,
        candidate_commit: str,
        candidate_tree: str = "",
    ) -> MaterializedArchive:
        """Materialize exact Git bytes and bind identity to the archive digest."""
        sha_pattern = re.compile(r"[0-9a-f]{40}")
        if not sha_pattern.fullmatch(candidate_commit):
            raise ValueError(
                "Candidate commit must be exactly 40 lowercase hex characters"
            )
        if not sha_pattern.fullmatch(candidate_tree):
            raise ValueError(
                "Candidate tree must be exactly 40 lowercase hex characters"
            )

        repo = self.repo_path.resolve()
        if not repo.exists():
            raise ValueError(f"Repository path does not exist: {repo}")

        def git_text(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout

        def git_bytes(*args: str) -> bytes:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                check=True,
                capture_output=True,
                timeout=60,
            ).stdout

        resolved_commit = git_text(
            "rev-parse", f"{candidate_commit}^{{commit}}"
        ).strip()
        if resolved_commit != candidate_commit:
            raise ValueError("Candidate commit did not resolve exactly")
        resolved_tree = git_text("rev-parse", f"{candidate_commit}^{{tree}}").strip()
        if resolved_tree != candidate_tree:
            raise ValueError(
                f"Candidate tree mismatch: expected {candidate_tree}, got {resolved_tree}"
            )

        archive_bytes = git_bytes("archive", "--format=tar", candidate_commit)
        artifact_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        archive_dir = Path(
            tempfile.mkdtemp(prefix=f"rf-archive-{artifact_sha256[:12]}-")
        )

        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
                for member in archive.getmembers():
                    member_path = PurePosixPath(member.name)
                    if (
                        member_path.is_absolute()
                        or ".." in member_path.parts
                        or not member_path.parts
                    ):
                        raise ValueError(f"Unsafe archive member: {member.name}")
                    # A tracked development venv contains an absolute interpreter
                    # symlink. Its bytes remain covered by artifact_sha256, but it
                    # is never extracted or used as verification input.
                    if member_path.parts[0] == ".venv_dev":
                        continue
                    if member.islnk():
                        raise ValueError(f"Hard links are not allowed: {member.name}")
                    if member.issym():
                        target = PurePosixPath(member.linkname)
                        resolved = posixpath.normpath(str(member_path.parent / target))
                        if (
                            target.is_absolute()
                            or resolved == ".."
                            or resolved.startswith("../")
                        ):
                            raise ValueError(f"Unsafe symbolic link: {member.name}")
                    elif not (member.isfile() or member.isdir()):
                        raise ValueError(f"Unsupported archive member: {member.name}")
                    archive.extract(member, path=archive_dir, filter="data")

            for path in sorted(archive_dir.rglob("*"), reverse=True):
                if path.is_symlink():
                    continue
                os.chmod(path, 0o555 if path.is_dir() else 0o444)
            os.chmod(archive_dir, 0o555)
            return MaterializedArchive(archive_dir, artifact_sha256)
        except Exception:
            os.chmod(archive_dir, 0o755)
            shutil.rmtree(archive_dir, ignore_errors=True)
            raise

    def verify(
        self,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
    ) -> tuple[VerificationReceipt, MergeCandidateManifest]:
        """Materialize once, execute only there, and bind receipt to bytes."""
        materialized = self._materialize_immutable_archive(
            job.candidate_commit, job.candidate_tree
        )
        mutable_repo_path = self.repo_path
        self.repo_path = materialized.path
        try:
            return self._verify_materialized(
                job, manifest, materialized.artifact_sha256
            )
        finally:
            self.repo_path = mutable_repo_path
            self._cleanup_materialized_archive(materialized.path)

    @staticmethod
    def _cleanup_materialized_archive(path: Path) -> None:
        for item in path.rglob("*"):
            if not item.is_symlink():
                os.chmod(item, 0o755 if item.is_dir() else 0o644)
        os.chmod(path, 0o755)
        shutil.rmtree(path)

    def compute_provenance_hash(
        self,
        repository: str,
        commit: str,
        tree: str,
        commands: list[str],
        policy_version: str,
        exit_codes: dict[str, int],
        archive_sha256: str,
        log_hashes: dict[str, str],
    ) -> str:
        """Compute content-addressed provenance record bound to repository identity, commit, tree, command manifest, environment policy, exit codes, artifact hashes, and log hashes."""
        sorted_commands = sorted(commands)
        sorted_exits = sorted(f"{k}:{v}" for k, v in exit_codes.items())
        sorted_logs = sorted(f"{k}:{v}" for k, v in log_hashes.items())

        parts = [
            repository,
            commit,
            tree,
            ",".join(sorted_commands),
            policy_version,
            ",".join(sorted_exits),
            f"sha256:{archive_sha256}",
            ",".join(sorted_logs),
        ]
        raw = "\0".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _verify_materialized(
        self,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
        artifact_sha256: str,
    ) -> tuple[VerificationReceipt, MergeCandidateManifest]:
        """Run verification and advance the manifest."""
        tier_str = (
            manifest.risk_tier.value
            if hasattr(manifest.risk_tier, "value")
            else str(manifest.risk_tier)
        )
        required = set(_PROOF_REQUIREMENTS.get(tier_str, {"focused"}))
        if manifest.dashboard_change:
            required |= _DASHBOARD_EXTRA

        results: list[CheckResult] = []
        for proof_class in sorted(required):
            result = self._run_proof_class(proof_class, job, manifest)
            results.append(result)

        integrity_results = self._run_integrity_invariants(job)
        results.extend(integrity_results)

        non_claims = self._compute_non_claims(job, results)

        by_proof_class: dict[str, list[CheckResult]] = {}
        for r in results:
            by_proof_class.setdefault(r.proof_class, []).append(r)

        evidence_list: list[VerificationEvidence] = []
        for pclass, p_results in by_proof_class.items():
            if all(r.passed for r in p_results):
                primary = p_results[0]
                summary = (
                    self._build_summary(primary)
                    if len(p_results) == 1
                    else f"{pclass}: {len(p_results)} checks passed"
                )
                evidence = VerificationEvidence(
                    proof_class=pclass,
                    command=primary.command or f"rf-verify-{pclass}",
                    summary=summary,
                    result="PASS",
                    log_path=primary.log_path or str(self.log_dir / f"{pclass}.log"),
                    log_sha256=primary.log_sha256 or self._compute_log_hash(primary),
                )
                evidence_list.append(evidence)

        # request_review is fail-closed: it raises unless every tier proof
        # class has passing evidence. A gate must report failure, not crash,
        # so only advance the manifest when every check passed. The receipt
        # below records the failure either way.
        if all(r.passed for r in results):
            updated_manifest = manifest.request_review(evidence_list)
        else:
            updated_manifest = manifest

        invariance_proof = self._compute_invariance_proof(list(manifest.changed_paths))
        archive_identity = f"sha256:{artifact_sha256}"
        exit_codes_dict = {r.name: r.exit_code for r in results}
        log_sha_dict = {r.name: r.log_sha256 for r in results}
        provenance_identity = self.compute_provenance_hash(
            repository=job.repository,
            commit=job.candidate_commit,
            tree=job.candidate_tree,
            commands=[r.command for r in results],
            policy_version=job.policy_version
            if hasattr(job, "policy_version")
            else "v1",
            exit_codes=exit_codes_dict,
            archive_sha256=artifact_sha256,
            log_hashes=log_sha_dict,
        )
        receipt = VerificationReceipt(
            receipt_id=provenance_identity,
            review_job_id=job.review_job_id,
            candidate_commit=job.candidate_commit,
            candidate_tree=job.candidate_tree or job.candidate_commit,
            immutable_archive_id=archive_identity,
            commands=json.dumps([r.command for r in results]),
            exit_codes=json.dumps({r.name: r.exit_code for r in results}),
            log_paths=json.dumps({r.name: r.log_path for r in results}),
            log_sha256=json.dumps({r.name: r.log_sha256 for r in results}),
            classification=self._classify_verification(results),
            changed_path_invariance_proof=invariance_proof,
            explicit_non_claims=json.dumps(non_claims),
            baseline_failures=json.dumps(self._compute_baseline_failures(results)),
        )

        return receipt, updated_manifest

    def _run_integrity_invariants(self, job: ReviewJob) -> list[CheckResult]:
        results: list[CheckResult] = []
        changed = json.loads(job.changed_paths_json) if job.changed_paths_json else []

        for path in changed:
            if not path.endswith(".py"):
                continue

            import_contract = INTEGRATION_IMPORT_CONTRACTS.get(path, [])
            if import_contract:
                result = self._check_integration_imports(path, import_contract)
                results.append(result)

            if "/test_" in path or path.startswith("test_"):
                result = self._check_circular_proof(path)
                results.append(result)
            else:
                test_path = self._derive_test_path(path)
                if test_path and (self.repo_path / test_path).exists():
                    result = self._check_circular_proof(test_path)
                    results.append(result)

            callsite_contract = INTEGRATION_CALLSITE_CONTRACTS.get(path, [])
            if callsite_contract:
                result = self._check_callsites(path, callsite_contract)
                results.append(result)

        return results

    def _check_integration_imports(
        self, path: str, expected_imports: list[str]
    ) -> CheckResult:
        full_path = self.repo_path / path
        if not full_path.exists():
            return CheckResult(
                name=f"integration-import:{Path(path).stem}",
                proof_class="focused",
                command=f"test -f {path}",
                exit_code=1,
                stderr=f"File {path} not found in repo_path — check failed",
                passed=False,
            )

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult(
                name=f"integration-import:{Path(path).stem}",
                proof_class="focused",
                command=f"cat {path}",
                exit_code=-2,
                stderr=str(exc),
                passed=False,
            )

        missing = [imp for imp in expected_imports if imp not in content]

        if missing:
            return CheckResult(
                name=f"integration-import:{Path(path).stem}",
                proof_class="focused",
                command=f"grep '{missing[0]}' {path}",
                exit_code=1,
                stderr=(
                    f"Integration contract violation: missing expected import '{missing[0]}' in {path}."
                ),
                passed=False,
            )

        return CheckResult(
            name=f"integration-import:{Path(path).stem}",
            proof_class="focused",
            command=f"grep '{expected_imports[0]}' {path}",
            exit_code=0,
            stdout=f"All {len(expected_imports)} expected integration imports present",
            passed=True,
        )

    def _check_circular_proof(self, test_path: str) -> CheckResult:
        full_path = self.repo_path / test_path
        if not full_path.exists():
            return CheckResult(
                name=f"circular-proof:{Path(test_path).stem}",
                proof_class="focused",
                command=f"test -f {test_path}",
                exit_code=1,
                stderr=f"Test file {test_path} not found in repo_path — check failed",
                passed=False,
            )

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult(
                name=f"circular-proof:{Path(test_path).stem}",
                proof_class="focused",
                command=f"cat {test_path}",
                exit_code=-2,
                stderr=str(exc),
                passed=False,
            )

        # Circular proof = the test imports ONLY the module under test, so it
        # validates the stub against itself (hollow green). The module under
        # test is derived from the test path: <pkg>/tests/test_<name>.py
        # tests <pkg>/<name>.py. The check fails only when every prismatic
        # import resolves to that module (or its submodules).
        # (The previous hardcoded 5-module allowlist was review-factory
        # specific and false-flagged every other subsystem's tests.)
        imported = re.findall(
            r"^\s*(?:from|import)\s+(prismatic(?:\.[A-Za-z0-9_]+)*)",
            content,
            re.M,
        )
        rel = Path(test_path)
        mut_stem = rel.stem
        if mut_stem.startswith("test_"):
            mut_stem = mut_stem[len("test_") :]
        # <pkg>/tests/test_<name>.py -> <pkg>/<name>; otherwise sibling.
        if rel.parent.name in ("tests", "test"):
            mut_pkg = rel.parent.parent.as_posix().replace("/", ".")
        else:
            mut_pkg = rel.parent.as_posix().replace("/", ".")
        mut_module = f"{mut_pkg}.{mut_stem}" if mut_pkg else mut_stem
        if not mut_module.startswith("prismatic"):
            mut_module = f"prismatic.{mut_module}"

        def _is_mut_or_sub(dotted):
            return dotted == mut_module or dotted.startswith(mut_module + ".")

        # A test with no prismatic imports cannot be circular by definition
        # (there is no module-under-test import to be self-validating).
        # Pass with a note rather than fail-closed: the previous
        # "inconclusive" failure false-flagged legitimate non-prismatic
        # tests (e.g. dashboard tests) and broke the tier-A gate on PR #639.
        if not imported:
            return CheckResult(
                name=f"circular-proof:{Path(test_path).stem}",
                proof_class="focused",
                command=f"grep PE-surface-import {test_path}",
                exit_code=0,
                stderr=(
                    f"Circular proof check passed vacuously in {test_path}: "
                    "no prismatic imports found, nothing circular to detect."
                ),
                passed=True,
            )

        if all(_is_mut_or_sub(d) for d in imported):
            return CheckResult(
                name=f"circular-proof:{Path(test_path).stem}",
                proof_class="focused",
                command=f"grep PE-surface-import {test_path}",
                exit_code=1,
                stderr=(
                    f"Circular proof detected in {test_path}: tests only import "
                    f"the module under test ({mut_module})."
                ),
                passed=False,
            )

        return CheckResult(
            name=f"circular-proof:{Path(test_path).stem}",
            proof_class="focused",
            command=f"grep PE-surface-import {test_path}",
            exit_code=0,
            stdout="Test imports modules beyond the module under test — not circular",
            passed=True,
        )

    def _check_callsites(self, path: str, expected_calls: list[str]) -> CheckResult:
        full_path = self.repo_path / path
        if not full_path.exists():
            return CheckResult(
                name=f"callsite:{Path(path).stem}",
                proof_class="focused",
                command=f"test -f {path}",
                exit_code=1,
                stderr=f"File {path} not found in repo_path — check failed",
                passed=False,
            )

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            return CheckResult(
                name=f"callsite:{Path(path).stem}",
                proof_class="focused",
                command=f"cat {path}",
                exit_code=-2,
                stderr=str(exc),
                passed=False,
            )

        missing = [fn for fn in expected_calls if fn not in content]

        if missing:
            return CheckResult(
                name=f"callsite:{Path(path).stem}",
                proof_class="focused",
                command=f"grep '{missing[0]}' {path}",
                exit_code=1,
                stderr=(
                    f"Integration contract violation: imports PE surface but never calls: {', '.join(missing)}."
                ),
                passed=False,
            )

        return CheckResult(
            name=f"callsite:{Path(path).stem}",
            proof_class="focused",
            command=f"grep '{expected_calls[0]}' {path}",
            exit_code=0,
            stdout=f"All {len(expected_calls)} expected call sites found",
            passed=True,
        )

    def _run_proof_class(
        self,
        proof_class: str,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
    ) -> CheckResult:
        if self.test_mode:
            return CheckResult(
                name=proof_class,
                proof_class=proof_class,
                command=f"test-stub-{proof_class}",
                exit_code=0,
                stdout=f"test_mode: {proof_class} passed",
                passed=True,
            )

        if proof_class == "focused":
            return self._run_focused_check(job)
        elif proof_class == "canonical":
            return self._run_canonical_check()
        elif proof_class == "package":
            return self._run_package_check()
        else:
            # Manual or unsupported proof class -> fail closed
            return CheckResult(
                name=proof_class,
                proof_class=proof_class,
                command=f"rf-verify-{proof_class}",
                exit_code=1,
                stderr=f"Proof class '{proof_class}' requires explicit automated verification runner — fail closed",
                passed=False,
            )

    def _run_focused_check(self, job: ReviewJob) -> CheckResult:
        changed = json.loads(job.changed_paths_json) if job.changed_paths_json else []
        # Only Python files map to tests. Docs/config/data changes carry no
        # test surface and must not fail the gate for having none.
        py_changed = [path for path in changed if path.endswith(".py")]
        if not py_changed:
            return CheckResult(
                name="focused",
                proof_class="focused",
                command="rf-verify-focused",
                exit_code=0,
                stdout="No Python files changed; nothing to focus on.",
                passed=True,
            )

        # Content-aware classification (#649 false-positive fix): a .py
        # change that is comment/docstring-only (no code-token change)
        # carries no test surface either, so it gets the same exemption
        # as non-.py docs changes. A real code change to a file with no
        # resolving test still fails closed below (the #619 invariant).
        diff_text = getattr(job, "diff_text", "") or ""
        base_commit = getattr(job, "base_commit", "") or ""
        code_changed = [
            path
            for path in py_changed
            if _is_code_change(self.repo_path, base_commit, diff_text, path)
        ]
        exempted = [path for path in py_changed if path not in code_changed]
        if not code_changed:
            return CheckResult(
                name="focused",
                proof_class="focused",
                command="rf-verify-focused",
                exit_code=0,
                stdout=(
                    "Changed Python files are comment/docstring-only; "
                    "no test surface (" + ", ".join(exempted[:5]) + ")."
                ),
                passed=True,
            )

        test_files = []
        for path in code_changed:
            if "/test_" in path or Path(path).name.startswith("test_"):
                # Changed test file itself - run it directly if it exists.
                if (self.repo_path / path).exists():
                    test_files.append(path)
                continue
            # Source file - resolve test_<stem>.py against the real tree.
            # Patterns were previously passed to pytest unexpanded (shell=False),
            # so they never matched anything; resolve them here instead.
            stem = Path(path).stem.replace("-", "_")
            for match in sorted(self.repo_path.rglob("test_" + stem + ".py")):
                test_files.append(str(match.relative_to(self.repo_path)))

        # Dedupe, keep the focused run bounded.
        seen = set()
        targets = [t for t in test_files if not (t in seen or seen.add(t))][:10]
        if not targets:
            # Fail closed: Python files changed but no test files resolve.
            # A verification run that executes zero tests proves nothing;
            # passing vacuously would let untested changes through the gate.
            # (Docs/config-only changes exit earlier with a pass — that path
            # is intentional and unchanged.)
            return CheckResult(
                name="focused",
                proof_class="focused",
                command="rf-verify-focused",
                exit_code=1,
                stderr=(
                    "FAIL-CLOSED: no test files resolved for changed Python "
                    "files (" + ", ".join(code_changed[:5]) + "). Focused "
                    "verification matched zero tests; add tests or adjust "
                    "the change so its test surface resolves."
                ),
                passed=False,
            )

        args = [sys.executable, "-m", "pytest"] + targets + ["-x", "-q", "--no-header"]
        return self._execute_subproc("focused", "focused", args)

    def _run_canonical_check(self) -> CheckResult:
        tests_dir = self.repo_path / "tests"
        if not tests_dir.exists():
            return CheckResult(
                name="canonical",
                proof_class="canonical",
                command="rf-verify-canonical",
                exit_code=1,
                stderr="Canonical tests directory tests/ not found in repo_path",
                passed=False,
            )

        args = [sys.executable, "-m", "pytest", "tests/", "-x", "-q", "--no-header"]
        # The full suite is far larger than the 300s default; bound it at
        # 30 minutes instead (self-hosted minutes are free).
        return self._execute_subproc("canonical", "canonical", args, timeout=1800)

    def _run_package_check(self) -> CheckResult:
        init_file = self.repo_path / "prismatic" / "__init__.py"
        if not init_file.exists():
            return CheckResult(
                name="package",
                proof_class="package",
                command="rf-verify-package",
                exit_code=1,
                stderr="Package entrypoint prismatic/__init__.py not found",
                passed=False,
            )

        # The materialized archive is read-only (0o444/0o555); py_compile
        # writes __pycache__ next to the source and would always fail there
        # with EACCES -- which made every tier-1+ verification fail this
        # check. Run the identical check against a writable copy of the
        # entrypoint instead of the archive itself.
        with tempfile.TemporaryDirectory(prefix="rf-package-check-") as tmp:
            pkg_dir = Path(tmp) / "prismatic"
            pkg_dir.mkdir(parents=True)
            shutil.copy2(init_file, pkg_dir / "__init__.py")
            args = [sys.executable, "-m", "py_compile", "prismatic/__init__.py"]
            return self._execute_subproc("package", "package", args, cwd=Path(tmp))

    def _execute_subproc(
        self,
        name: str,
        proof_class: str,
        cmd_args: list[str],
        timeout: int = 300,
        cwd: Optional[Path] = None,
    ) -> CheckResult:
        command_str = " ".join(cmd_args)
        log_path = str(self.log_dir / f"{name}.log")

        try:
            import os as _os

            proc = subprocess.run(
                cmd_args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd if cwd is not None else self.repo_path),
                # The materialized archive is read-only; never try to write
                # __pycache__ into it (py_compile etc. would fail with EACCES).
                env={**_os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
            passed = exit_code == 0
        except subprocess.TimeoutExpired:
            exit_code = -1
            stdout = ""
            stderr = f"Timeout: check exceeded {timeout}s"
            passed = False
        except Exception as exc:
            exit_code = -2
            stdout = ""
            stderr = str(exc)
            passed = False

        log_content = f"=== {name} ({proof_class}) ===\n"
        log_content += f"Command: {command_str}\n"
        log_content += f"Exit code: {exit_code}\n"
        log_content += f"--- stdout ---\n{stdout}\n"
        log_content += f"--- stderr ---\n{stderr}\n"

        try:
            Path(log_path).write_text(log_content, encoding="utf-8")
        except OSError:
            pass

        log_sha = hashlib.sha256(log_content.encode("utf-8")).hexdigest()

        return CheckResult(
            name=name,
            proof_class=proof_class,
            command=command_str,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            log_path=log_path,
            log_sha256=log_sha,
            passed=passed,
        )

    @staticmethod
    def _derive_test_path(source_path: str) -> Optional[str]:
        p = Path(source_path)
        test_dir = p.parent / "tests"
        test_file = f"test_{p.stem}.py"
        return str(test_dir / test_file)

    @staticmethod
    def _build_summary(result: CheckResult) -> str:
        if result.passed:
            return f"{result.proof_class}: passed (exit {result.exit_code})"
        return f"{result.proof_class}: FAILED (exit {result.exit_code}) — {result.stderr[:200]}"

    @staticmethod
    def _compute_log_hash(result: CheckResult) -> str:
        content = f"{result.command}\n{result.stdout}\n{result.stderr}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_invariance_proof(changed_paths: list[str]) -> str:
        canonical = json.dumps(sorted(changed_paths), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _classify_verification(results: list[CheckResult]) -> str:
        classes = {r.proof_class for r in results}
        if "canonical" in classes:
            return "canonical_suite"
        if "focused" in classes:
            return "targeted"
        return "bounded_regression"

    @staticmethod
    def _compute_non_claims(job: ReviewJob, results: list[CheckResult]) -> list[str]:
        non_claims = []
        for r in results:
            if not r.passed and r.name.startswith(
                ("integration-import:", "circular-proof:", "callsite:")
            ):
                non_claims.append(r.stderr)

        failed = [
            r
            for r in results
            if not r.passed
            and not r.name.startswith(
                ("integration-import:", "circular-proof:", "callsite:")
            )
        ]
        if failed:
            non_claims.append(
                f"The following checks failed: {', '.join(r.name for r in failed)}"
            )
        non_claims.append("Integration and end-to-end tests were not executed")
        non_claims.append("Manual UI verification was not performed")
        return non_claims

    @staticmethod
    def _compute_baseline_failures(results: list[CheckResult]) -> list[str]:
        failures = []
        for r in results:
            if not r.passed:
                failures.append(
                    f"{r.proof_class}: exit {r.exit_code} — {r.stderr[:100]}"
                )
        return failures
