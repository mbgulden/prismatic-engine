"""Group D production-path merge authorization, concurrency, CI, and rollback tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from prismatic.core.merge_factory import MergeFactoryStore
from prismatic.integrate import (
    IntegrationManifest,
    IntegrationStatus,
    integrate_pipeline_run as real_integrate_pipeline_run,
)
from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    MergeCandidateManifest,
    RiskTier,
    VerificationEvidence,
)
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.merge_executor import MergeExecutor
from prismatic.review_factory.models import ReviewJob, ReviewJobState
from prismatic.review_factory.queue import ReviewQueue
from prismatic.verification.receipt_store import VerificationReceiptStore
from tests.test_receipt_validator import resign_receipt, valid_policy, valid_receipt


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_repo(root: Path) -> dict[str, str]:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "release")
    _git(repo, "config", "user.name", "RF Group D")
    _git(repo, "config", "user.email", "rf-group-d@example.invalid")
    tracked = repo / "docs" / "readme.md"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base_commit = _git(repo, "rev-parse", "HEAD")
    base_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    _git(repo, "checkout", "-q", "-b", "candidate")
    tracked.write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "candidate")
    candidate_commit = _git(repo, "rev-parse", "HEAD")
    candidate_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    _git(repo, "checkout", "-q", "release")
    return {
        "repo": str(repo.resolve()),
        "base_commit": base_commit,
        "base_tree": base_tree,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
    }


def _provider_receipt(
    git_ids: dict[str, str], store: VerificationReceiptStore, task_id: str
):
    receipt = valid_receipt()
    integration_repo = Path(git_ids["repo"])
    verification_repo = integration_repo.parent / f"verify-{task_id.lower()}"
    subprocess.run(
        ["git", "clone", "-q", str(integration_repo), str(verification_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(verification_repo, "checkout", "-q", git_ids["candidate_commit"])
    changed_paths = ["docs/readme.md"]
    changed_paths_digest = hashlib.sha256(
        json.dumps(
            changed_paths,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt.update(
        task_id=task_id,
        repository_id="local-repo",
        base_sha=git_ids["base_commit"],
        base_tree_sha=git_ids["base_tree"],
        candidate_sha=git_ids["candidate_commit"],
        tree_sha=git_ids["candidate_tree"],
        canonical_repository_root=str(verification_repo.resolve()),
        source_locator=str(verification_repo.resolve()),
        clean_checkout_id="group-d-clean-room",
        changed_paths=changed_paths,
        checkout_clean_state={
            "status": "clean",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "porcelain_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
        },
        changed_path_containment={
            "allowed_roots": ["docs"],
            "contained": True,
            "changed_paths_sha256": f"sha256:{changed_paths_digest}",
        },
        verifier_isolation={
            "independent": True,
            "network_isolated": True,
            "filesystem_isolated": True,
            "clean_room_id": "group-d-clean-room",
        },
        proof_scope_status={
            "focused": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "canonical": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "clean_room": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "package": {"status": "pass", "evidence_ids": ["focused-tests"]},
            "production": {
                "status": "unavailable",
                "evidence_ids": [],
                "reason": "not claimed before deployment",
            },
            "browser": {
                "status": "unavailable",
                "evidence_ids": [],
                "reason": "not claimed by this fixture",
            },
        },
    )
    resign_receipt(receipt)
    policy = valid_policy()
    policy["repository"]["repository_id"] = "local-repo"
    policy["bindings"].update(
        expected_base_sha=git_ids["base_commit"],
        expected_candidate_sha=git_ids["candidate_commit"],
        expected_tree_sha=git_ids["candidate_tree"],
    )
    return store.persist(receipt, policy)


def _manifest(
    git_ids: dict[str, str], task_id: str, stored_receipt, *, provenance: bool = True
) -> MergeCandidateManifest:
    manifest = MergeCandidateManifest.create(
        issue_id=task_id,
        task_id=task_id,
        task_file_sha256="a" * 64,
        repository="local-repo",
        target="release",
        base_sha=git_ids["base_commit"],
        candidate_sha=git_ids["candidate_commit"],
        changed_paths=["docs/readme.md"],
        producer="agy",
        preserved_candidate_location=git_ids["repo"],
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )
    manifest = manifest.request_review(
        [
            VerificationEvidence(
                proof_class="focused",
                command="pytest -q",
                summary="focused pass",
                result="PASS",
                log_path="/tmp/group-d.log",
                log_sha256="f" * 64,
            )
        ]
    )
    manifest = manifest.record_review(
        IndependentReview(
            reviewer="independent-group-d",
            review_id=f"review-{task_id}",
            verdict="CLEAN",
            reviewed_sha=git_ids["candidate_commit"],
            reviewed_manifest_digest=manifest._digest_without_review(),
            scope_clean=True,
            conflict_free=True,
        )
    )
    receipt_fields = (
        {
            "provider_receipt_id": stored_receipt.receipt_id,
            "provider_receipt_sha256": stored_receipt.receipt_sha256,
            "provider_policy_sha256": stored_receipt.policy_sha256,
        }
        if provenance
        else {}
    )
    manifest = manifest.record_ci(
        [
            CICheck(
                name="rf-v1-verification",
                run_id=1000,
                conclusion="SUCCESS",
                head_sha=git_ids["candidate_commit"],
                details_url="https://github.com/local-repo/actions/runs/1000",
                **receipt_fields,
            )
        ]
    )
    return manifest.mark_merge_eligible()


def _authorized_job(
    db_path: Path, git_ids: dict[str, str], task_id: str
) -> tuple[ReviewQueue, str, str]:
    queue = ReviewQueue(db=ReviewFactoryDB(db_path))
    job = ReviewJob(
        completed_work_id=f"cw-{task_id}",
        task_id=task_id,
        repository="local-repo",
        base_commit=git_ids["base_commit"],
        base_tree=git_ids["base_tree"],
        candidate_commit=git_ids["candidate_commit"],
        candidate_tree=git_ids["candidate_tree"],
        changed_paths_json=json.dumps(["docs/readme.md"]),
        risk_tier=0,
        state=ReviewJobState.MERGE_READY.value,
    )
    queue.db.insert_review_job(job)
    auth_id = queue.authorize_merge(
        job.review_job_id,
        actor="standing-policy: tier-0",
        expected_merge_tree=git_ids["candidate_tree"],
    )
    assert auth_id is not None
    return queue, job.review_job_id, auth_id


def _executor(
    queue: ReviewQueue,
    repo: Path,
    mf_db: Path,
    receipt_db: Path,
) -> MergeExecutor:
    return MergeExecutor(
        queue=queue,
        repo_path=repo,
        mf_store=MergeFactoryStore(db_path=mf_db),
        verification_receipt_store=VerificationReceiptStore(receipt_db),
    )


def test_group_d_real_concurrent_executors_have_one_winner(tmp_path, monkeypatch):
    git_ids = _make_repo(tmp_path)
    repo = Path(git_ids["repo"])
    receipt_db = tmp_path / "receipts.sqlite3"
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), "TASK-D-CONCURRENT"
    )
    manifest = _manifest(git_ids, "TASK-D-CONCURRENT", stored)
    queue0, job_id, auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-CONCURRENT"
    )

    queue1 = ReviewQueue(db=ReviewFactoryDB(tmp_path / "review.sqlite3"))
    queue2 = ReviewQueue(db=ReviewFactoryDB(tmp_path / "review.sqlite3"))
    barrier = threading.Barrier(2)
    for queue in (queue1, queue2):
        original = queue.db.get_authorization_for_job

        def synchronized_get(review_job_id, original=original):
            authorization = original(review_job_id)
            barrier.wait(timeout=10)
            return authorization

        monkeypatch.setattr(queue.db, "get_authorization_for_job", synchronized_get)

    integration_calls = 0
    integration_lock = threading.Lock()

    def real_integration(**kwargs):
        nonlocal integration_calls
        with integration_lock:
            integration_calls += 1
        return real_integrate_pipeline_run(**kwargs, skip_tests=True)

    executor1 = _executor(queue1, repo, tmp_path / "mf.sqlite3", receipt_db)
    executor2 = _executor(queue2, repo, tmp_path / "mf.sqlite3", receipt_db)
    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run",
        side_effect=real_integration,
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda executor: executor.execute(job_id, manifest=manifest),
                    (executor1, executor2),
                )
            )

    assert sum(result.success for result in results) == 1
    assert integration_calls == 1
    loser = next(result for result in results if not result.success)
    assert "could not be claimed" in loser.error
    winner = next(result for result in results if result.success)
    assert _git(repo, "rev-parse", "release") == winner.merge_sha
    assert _git(repo, "rev-parse", "release^{tree}") == git_ids["candidate_tree"]
    assert _git(repo, "branch", "--show-current") == "release"
    final_job = queue0.db.get_review_job(job_id)
    assert final_job is not None
    assert final_job.state == ReviewJobState.MERGED.value
    consumed = queue0.db.conn.execute(
        "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
        (auth_id,),
    ).fetchone()[0]
    assert consumed


def test_group_d_synthetic_ci_rejected_before_claim(tmp_path):
    git_ids = _make_repo(tmp_path)
    receipt_db = tmp_path / "receipts.sqlite3"
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), "TASK-D-SYNTHETIC"
    )
    manifest = _manifest(git_ids, "TASK-D-SYNTHETIC", stored, provenance=False)
    queue, job_id, auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-SYNTHETIC"
    )
    executor = _executor(
        queue,
        Path(git_ids["repo"]),
        tmp_path / "mf.sqlite3",
        receipt_db,
    )

    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run"
    ) as integration:
        result = executor.execute(job_id, manifest=manifest)

    assert result.success is False
    assert "lacks provider-neutral receipt provenance" in result.error
    integration.assert_not_called()
    job = queue.db.get_review_job(job_id)
    assert job is not None
    assert job.state == ReviewJobState.MERGE_AUTHORIZED.value
    authorization = queue.db.conn.execute(
        "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
        (auth_id,),
    ).fetchone()[0]
    assert authorization == ""


def test_group_d_integration_failure_rolls_back_real_git(tmp_path):
    git_ids = _make_repo(tmp_path)
    repo = Path(git_ids["repo"])
    receipt_db = tmp_path / "receipts.sqlite3"
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), "TASK-D-ROLLBACK"
    )
    manifest = _manifest(git_ids, "TASK-D-ROLLBACK", stored)
    queue, job_id, auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-ROLLBACK"
    )
    executor = _executor(queue, repo, tmp_path / "mf.sqlite3", receipt_db)

    def mutate_then_fail(**_kwargs):
        (repo / "docs" / "readme.md").write_text("mutated-by-integration\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "failing integration side effect")
        raise RuntimeError("forced integration failure")

    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run",
        side_effect=mutate_then_fail,
    ):
        result = executor.execute(job_id, manifest=manifest)

    assert result.success is False
    assert "forced integration failure" in result.error
    assert _git(repo, "rev-parse", "release") == git_ids["base_commit"]
    assert (repo / "docs" / "readme.md").read_text() == "base\n"
    job = queue.db.get_review_job(job_id)
    assert job is not None
    assert job.state == ReviewJobState.MERGE_VERIFICATION_FAILED.value
    consumed = queue.db.conn.execute(
        "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
        (auth_id,),
    ).fetchone()[0]
    assert consumed


def test_group_d_returned_integration_failure_rolls_back_real_git(tmp_path):
    git_ids = _make_repo(tmp_path)
    repo = Path(git_ids["repo"])
    receipt_db = tmp_path / "receipts.sqlite3"
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), "TASK-D-RETURNED-FAILURE"
    )
    manifest = _manifest(git_ids, "TASK-D-RETURNED-FAILURE", stored)
    queue, job_id, _auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-RETURNED-FAILURE"
    )
    executor = _executor(queue, repo, tmp_path / "mf.sqlite3", receipt_db)

    def mutate_then_return_failure(**_kwargs):
        (repo / "docs" / "readme.md").write_text("returned-failure\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "returned integration failure")
        return IntegrationManifest(
            issue_id="TASK-D-RETURNED-FAILURE",
            branch=git_ids["candidate_commit"],
            target_branch="release",
            status=IntegrationStatus.FAILED,
            merge_sha=_git(repo, "rev-parse", "HEAD"),
            error_message="integration returned failure",
        )

    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run",
        side_effect=mutate_then_return_failure,
    ):
        result = executor.execute(job_id, manifest=manifest)

    assert result.success is False
    assert "integration returned failure" in result.error
    assert _git(repo, "rev-parse", "release") == git_ids["base_commit"]
    assert (repo / "docs" / "readme.md").read_text() == "base\n"
    job = queue.db.get_review_job(job_id)
    assert job is not None
    assert job.state == ReviewJobState.MERGE_VERIFICATION_FAILED.value


def test_group_d_authorization_rejects_arbitrary_and_whitespace_actor(tmp_path):
    git_ids = _make_repo(tmp_path)
    queue = ReviewQueue(db=ReviewFactoryDB(tmp_path / "review.sqlite3"))
    job = ReviewJob(
        completed_work_id="cw-authority",
        task_id="TASK-D-AUTHORITY",
        repository="local-repo",
        base_commit=git_ids["base_commit"],
        base_tree=git_ids["base_tree"],
        candidate_commit=git_ids["candidate_commit"],
        candidate_tree=git_ids["candidate_tree"],
        changed_paths_json="[]",
        risk_tier=0,
        state=ReviewJobState.MERGE_READY.value,
    )
    queue.db.insert_review_job(job)
    for actor in ("", "   ", "actor", "michael", "human:michael"):
        assert queue.authorize_merge(job.review_job_id, actor=actor) is None
    assert (
        queue.authorize_merge(
            job.review_job_id,
            actor="standing-policy: tier-0",
            expected_merge_tree=git_ids["candidate_tree"],
        )
        is not None
    )
