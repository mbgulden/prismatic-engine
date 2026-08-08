"""Production-path tests for exact immutable verification materialization."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from prismatic.merge_candidate_manifest import MergeCandidateManifest, RiskTier
from prismatic.review_factory.models import ReviewJob
from prismatic.review_factory.verifier import CheckResult, VerificationWorker


def _git(repo: Path, *args: str, text: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=text,
    ).stdout


def _repo_with_commit(
    tmp_path: Path, content: str = "committed\n"
) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "RF Test")
    (repo / "proof.txt").write_text(content)
    _git(repo, "add", "proof.txt")
    _git(repo, "commit", "-m", "candidate")
    commit = _git(repo, "rev-parse", "HEAD").strip()
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    return repo, commit, tree


def _manifest(repo: Path, commit: str) -> MergeCandidateManifest:
    return MergeCandidateManifest.create(
        issue_id="PR421-GROUP-C",
        task_id="PR421-GROUP-C",
        task_file_sha256="a" * 64,
        repository="local/repo",
        target="main",
        base_sha=commit,
        candidate_sha=commit,
        changed_paths=["proof.txt"],
        producer="george",
        preserved_candidate_location=str(repo),
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )


def test_verify_invokes_materializer_once_and_uses_committed_bytes(
    tmp_path, monkeypatch
):
    repo, commit, tree = _repo_with_commit(tmp_path)
    # Mutable checkout diverges after the candidate commit.
    (repo / "proof.txt").write_text("mutable-uncommitted\n")

    worker = VerificationWorker(repo_path=repo, log_dir=tmp_path / "logs")
    real_materializer = worker._materialize_immutable_archive
    materializer_spy = Mock(wraps=real_materializer)
    monkeypatch.setattr(worker, "_materialize_immutable_archive", materializer_spy)

    observed: list[str] = []

    def proof(_proof_class, _job, _manifest):
        observed.append((worker.repo_path / "proof.txt").read_text())
        return CheckResult(
            name="committed-bytes",
            proof_class="focused",
            command="read proof.txt",
            exit_code=0,
            passed=True,
        )

    monkeypatch.setattr(worker, "_run_proof_class", proof)
    job = ReviewJob(
        review_job_id="job-c",
        completed_work_id="cw-c",
        task_id="PR421-GROUP-C",
        repository="local/repo",
        base_commit=commit,
        base_tree=tree,
        candidate_commit=commit,
        candidate_tree=tree,
    )

    archive_bytes = _git(repo, "archive", "--format=tar", commit, text=False)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    archive_glob = f"rf-archive-{digest[:12]}-*"
    archives_before = set(Path("/tmp").glob(archive_glob))

    receipt, _ = worker.verify(job, _manifest(repo, commit))

    assert materializer_spy.call_count == 1
    assert observed == ["committed\n"]
    assert receipt.immutable_archive_id == f"sha256:{digest}"
    expected_receipt_id = receipt.recompute_provenance_hash(
        repository=job.repository,
        policy_version=job.policy_version if hasattr(job, "policy_version") else "v1"
    )
    assert receipt.receipt_id == expected_receipt_id
    assert receipt.candidate_commit == commit
    assert receipt.candidate_tree == tree
    # verify() restores the caller's path and removes the ephemeral extraction.
    assert worker.repo_path == repo
    assert set(Path("/tmp").glob(archive_glob)) == archives_before


def test_materialized_archive_is_read_only_and_collision_isolated(tmp_path):
    repo, commit1, tree1 = _repo_with_commit(tmp_path, "one\n")
    worker = VerificationWorker(repo_path=repo, log_dir=tmp_path / "logs")
    archive1 = worker._materialize_immutable_archive(commit1, tree1)
    try:
        assert (archive1.path / "proof.txt").read_text() == "one\n"
        assert os.stat(archive1.path / "proof.txt").st_mode & 0o222 == 0
        assert os.stat(archive1.path).st_mode & 0o222 == 0

        (repo / "proof.txt").write_text("two\n")
        _git(repo, "add", "proof.txt")
        _git(repo, "commit", "-m", "second")
        commit2 = _git(repo, "rev-parse", "HEAD").strip()
        tree2 = _git(repo, "rev-parse", "HEAD^{tree}").strip()
        archive2 = worker._materialize_immutable_archive(commit2, tree2)
        try:
            assert archive2.artifact_sha256 != archive1.artifact_sha256
            assert archive2.path != archive1.path
            assert (archive2.path / "proof.txt").read_text() == "two\n"
        finally:
            worker._cleanup_materialized_archive(archive2.path)
    finally:
        worker._cleanup_materialized_archive(archive1.path)


@pytest.mark.parametrize(
    ("commit", "tree", "message"),
    [
        ("deadbee", "b" * 40, "Candidate commit"),
        ("A" * 40, "b" * 40, "Candidate commit"),
        ("a" * 40, "", "Candidate tree"),
    ],
)
def test_materializer_rejects_non_exact_identities(tmp_path, commit, tree, message):
    repo, _, _ = _repo_with_commit(tmp_path)
    worker = VerificationWorker(repo_path=repo)
    with pytest.raises(ValueError, match=message):
        worker._materialize_immutable_archive(commit, tree)


def test_materializer_rejects_commit_tree_mismatch(tmp_path):
    repo, commit, _ = _repo_with_commit(tmp_path)
    worker = VerificationWorker(repo_path=repo)
    with pytest.raises(ValueError, match="tree mismatch"):
        worker._materialize_immutable_archive(commit, "f" * 40)
