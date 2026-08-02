"""Group D production-path merge authorization, concurrency, CI, and rollback tests."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

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
from prismatic.review_factory.models import (
    MergeAuthorization,
    ReviewJob,
    ReviewJobState,
)
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
    assert "could not be atomically claimed" in loser.error
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


@pytest.mark.parametrize(
    ("binding", "tampered_value"),
    [
        ("issue_id", "OTHER-TASK"),
        ("task_id", "OTHER-TASK"),
        ("changed_paths", ("docs/other.md",)),
        ("risk_tier", RiskTier.B),
    ],
)
def test_group_d_manifest_identity_mismatch_fails_before_claim(
    tmp_path, binding, tampered_value
):
    root = tmp_path / binding
    root.mkdir()
    git_ids = _make_repo(root)
    receipt_db = root / "receipts.sqlite3"
    task_label = binding.replace("changed_paths", "paths")
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), f"TASK-D-BIND-{task_label}"
    )
    manifest = _manifest(git_ids, f"TASK-D-BIND-{task_label}", stored)
    bad_manifest = copy.deepcopy(manifest)
    object.__setattr__(bad_manifest, binding, tampered_value)
    queue, job_id, auth_id = _authorized_job(
        root / "review.sqlite3", git_ids, f"TASK-D-BIND-{task_label}"
    )
    executor = _executor(queue, Path(git_ids["repo"]), root / "mf.sqlite3", receipt_db)

    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run"
    ) as integration:
        result = executor.execute(job_id, manifest=bad_manifest)

    assert result.success is False
    assert f"manifest {binding} mismatch" in result.error.lower()
    integration.assert_not_called()
    consumed = queue.db.conn.execute(
        "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
        (auth_id,),
    ).fetchone()[0]
    assert consumed == ""


def test_group_d_tampered_durable_manifest_fails_before_claim(tmp_path):
    git_ids = _make_repo(tmp_path)
    receipt_db = tmp_path / "receipts.sqlite3"
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), "TASK-D-PACKET-DIGEST"
    )
    manifest = _manifest(git_ids, "TASK-D-PACKET-DIGEST", stored)
    packet_path = tmp_path / "merge_candidate.json"
    manifest.write(packet_path)
    packet_digest = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    queue, job_id, auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-PACKET-DIGEST"
    )
    queue.db.conn.execute(
        """UPDATE review_jobs
           SET result_packet_path = ?, result_packet_sha256 = ?
           WHERE review_job_id = ?""",
        (str(packet_path), packet_digest, job_id),
    )
    packet_path.write_text(
        packet_path.read_text(encoding="utf-8") + " ", encoding="utf-8"
    )
    executor = _executor(
        queue, Path(git_ids["repo"]), tmp_path / "mf.sqlite3", receipt_db
    )

    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run"
    ) as integration:
        result = executor.execute(job_id, manifest=manifest)

    assert result.success is False
    assert "durable result packet digest mismatch" in result.error.lower()
    integration.assert_not_called()
    consumed = queue.db.conn.execute(
        "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
        (auth_id,),
    ).fetchone()[0]
    assert consumed == ""


def test_group_d_unsafe_single_parent_mutation_refuses_rollback(tmp_path):
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
    assert "rollback was refused or failed" in result.error.lower()
    unsafe_head = _git(repo, "rev-parse", "release")
    assert unsafe_head != git_ids["base_commit"]
    assert (repo / "docs" / "readme.md").read_text() == "mutated-by-integration\n"
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
        _git(
            repo,
            "merge",
            git_ids["candidate_commit"],
            "--no-ff",
            "-m",
            "returned integration failure",
        )
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


def test_group_d_stale_target_head_is_rejected_without_git_mutation(tmp_path):
    git_ids = _make_repo(tmp_path)
    repo = Path(git_ids["repo"])
    receipt_db = tmp_path / "receipts.sqlite3"
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), "TASK-D-STALE-TARGET"
    )
    manifest = _manifest(git_ids, "TASK-D-STALE-TARGET", stored)
    queue, job_id, _auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-STALE-TARGET"
    )
    advanced = repo / "advanced.txt"
    advanced.write_text("unrelated target advance\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "advance target after authorization")
    advanced_head = _git(repo, "rev-parse", "release")
    executor = _executor(queue, repo, tmp_path / "mf.sqlite3", receipt_db)

    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run"
    ) as integration:
        result = executor.execute(job_id, manifest=manifest)

    assert result.success is False
    assert "target release advanced" in result.error.lower()
    assert _git(repo, "rev-parse", "release") == advanced_head
    integration.assert_not_called()


def test_group_d_concurrent_advance_after_failed_merge_refuses_cas_rollback(tmp_path):
    git_ids = _make_repo(tmp_path)
    repo = Path(git_ids["repo"])
    receipt_db = tmp_path / "receipts.sqlite3"
    stored = _provider_receipt(
        git_ids, VerificationReceiptStore(receipt_db), "TASK-D-CAS-ADVANCE"
    )
    manifest = _manifest(git_ids, "TASK-D-CAS-ADVANCE", stored)
    queue, job_id, _auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-CAS-ADVANCE"
    )
    executor = _executor(queue, repo, tmp_path / "mf.sqlite3", receipt_db)
    observed: dict[str, str] = {}

    def merge_then_advance(**_kwargs):
        _git(
            repo,
            "merge",
            git_ids["candidate_commit"],
            "--no-ff",
            "-m",
            "authorized merge before concurrent advance",
        )
        observed["merge_sha"] = _git(repo, "rev-parse", "HEAD")
        (repo / "concurrent.txt").write_text("advance\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "concurrent target advance")
        observed["advanced_head"] = _git(repo, "rev-parse", "HEAD")
        return IntegrationManifest(
            issue_id="TASK-D-CAS-ADVANCE",
            branch=git_ids["candidate_commit"],
            target_branch="release",
            status=IntegrationStatus.FAILED,
            merge_sha=observed["merge_sha"],
            error_message="failure after concurrent target advance",
        )

    with patch(
        "prismatic.review_factory.merge_executor.integrate_pipeline_run",
        side_effect=merge_then_advance,
    ):
        result = executor.execute(job_id, manifest=manifest)

    assert result.success is False
    assert "rollback was refused or failed" in result.error.lower()
    assert _git(repo, "rev-parse", "release") == observed["advanced_head"]


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


def test_group_d_atomic_authorization_creation_has_one_winner(tmp_path, monkeypatch):
    git_ids = _make_repo(tmp_path)
    db_path = tmp_path / "review.sqlite3"
    seed = ReviewQueue(db=ReviewFactoryDB(db_path))
    job = ReviewJob(
        completed_work_id="cw-auth-race",
        task_id="TASK-D-AUTH-RACE",
        repository="local-repo",
        base_commit=git_ids["base_commit"],
        base_tree=git_ids["base_tree"],
        candidate_commit=git_ids["candidate_commit"],
        candidate_tree=git_ids["candidate_tree"],
        changed_paths_json="[]",
        risk_tier=0,
        state=ReviewJobState.MERGE_READY.value,
    )
    seed.db.insert_review_job(job)

    queues = [
        ReviewQueue(db=ReviewFactoryDB(db_path)),
        ReviewQueue(db=ReviewFactoryDB(db_path)),
    ]
    barrier = threading.Barrier(2)
    for queue in queues:
        original = queue.db.get_review_job

        def synchronized_get(review_job_id, original=original):
            observed = original(review_job_id)
            barrier.wait(timeout=10)
            return observed

        monkeypatch.setattr(queue.db, "get_review_job", synchronized_get)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda queue: queue.authorize_merge(
                    job.review_job_id,
                    actor="standing-policy: tier-0",
                    expected_merge_tree=git_ids["candidate_tree"],
                ),
                queues,
            )
        )

    assert sum(result is not None for result in results) == 1
    persisted = seed.db.conn.execute(
        "SELECT * FROM merge_authorizations WHERE review_job_id = ?",
        (job.review_job_id,),
    ).fetchall()
    assert len(persisted) == 1
    current = seed.db.get_review_job(job.review_job_id)
    assert current is not None
    assert current.state == ReviewJobState.MERGE_AUTHORIZED.value


def test_group_d_authorization_ttl_bounds_fail_without_mutation(tmp_path):
    git_ids = _make_repo(tmp_path)
    queue = ReviewQueue(db=ReviewFactoryDB(tmp_path / "review.sqlite3"))
    job = ReviewJob(
        completed_work_id="cw-auth-ttl",
        task_id="TASK-D-AUTH-TTL",
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

    for invalid_ttl in (0, -1, 1441, True):
        assert (
            queue.authorize_merge(
                job.review_job_id,
                actor="standing-policy: tier-0",
                expires_minutes=invalid_ttl,
            )
            is None
        )
    assert queue.db.get_authorization_for_job(job.review_job_id) is None
    current = queue.db.get_review_job(job.review_job_id)
    assert current is not None
    assert current.state == ReviewJobState.MERGE_READY.value


def test_group_d_expired_or_wrong_binding_claim_changes_neither_row(tmp_path):
    git_ids = _make_repo(tmp_path)
    queue, job_id, auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-EXPIRED-CLAIM"
    )
    auth = queue.db.get_authorization_for_job(job_id)
    assert auth is not None

    wrong_binding = queue.db.claim_authorization_for_merge(
        auth_id,
        review_job_id=job_id,
        repository="wrong-repository",
        pr_head_commit=git_ids["candidate_commit"],
        pr_base_commit=git_ids["base_commit"],
        candidate_tree=git_ids["candidate_tree"],
        expected_merge_tree=git_ids["candidate_tree"],
        policy_version="v1",
    )
    assert wrong_binding is None

    queue.db.conn.execute(
        "UPDATE merge_authorizations SET expires_at = ? WHERE authorization_id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), auth_id),
    )
    expired = queue.db.claim_authorization_for_merge(
        auth_id,
        review_job_id=job_id,
        repository="local-repo",
        pr_head_commit=git_ids["candidate_commit"],
        pr_base_commit=git_ids["base_commit"],
        candidate_tree=git_ids["candidate_tree"],
        expected_merge_tree=git_ids["candidate_tree"],
        policy_version="v1",
    )
    assert expired is None
    row = queue.db.conn.execute(
        "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
        (auth_id,),
    ).fetchone()
    assert row["consumed_at"] == ""
    current = queue.db.get_review_job(job_id)
    assert current is not None
    assert current.state == ReviewJobState.MERGE_AUTHORIZED.value


def test_group_d_claim_state_failure_rolls_back_authorization_consumption(tmp_path):
    git_ids = _make_repo(tmp_path)
    queue, job_id, auth_id = _authorized_job(
        tmp_path / "review.sqlite3", git_ids, "TASK-D-CLAIM-ROLLBACK"
    )
    queue.db.conn.executescript(
        """CREATE TRIGGER reject_merging_transition
           BEFORE UPDATE OF state ON review_jobs
           WHEN NEW.state = 'merging'
           BEGIN
               SELECT RAISE(ABORT, 'forced merging transition failure');
           END;"""
    )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced merging transition failure"
    ):
        queue.db.claim_authorization_for_merge(
            auth_id,
            review_job_id=job_id,
            repository="local-repo",
            pr_head_commit=git_ids["candidate_commit"],
            pr_base_commit=git_ids["base_commit"],
            candidate_tree=git_ids["candidate_tree"],
            expected_merge_tree=git_ids["candidate_tree"],
            policy_version="v1",
        )

    row = queue.db.conn.execute(
        "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
        (auth_id,),
    ).fetchone()
    assert row["consumed_at"] == ""
    current = queue.db.get_review_job(job_id)
    assert current is not None
    assert current.state == ReviewJobState.MERGE_AUTHORIZED.value


def test_group_d_authorization_creation_state_failure_rolls_back_insert(tmp_path):
    git_ids = _make_repo(tmp_path)
    queue = ReviewQueue(db=ReviewFactoryDB(tmp_path / "review.sqlite3"))
    job = ReviewJob(
        completed_work_id="cw-create-rollback",
        task_id="TASK-D-CREATE-ROLLBACK",
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
    queue.db.conn.executescript(
        """CREATE TRIGGER reject_merge_authorized_transition
           BEFORE UPDATE OF state ON review_jobs
           WHEN NEW.state = 'merge_authorized'
           BEGIN
               SELECT RAISE(ABORT, 'forced authorization transition failure');
           END;"""
    )
    now = datetime.now(timezone.utc)
    auth = MergeAuthorization(
        review_job_id=job.review_job_id,
        repository=job.repository,
        pr_head_commit=job.candidate_commit,
        pr_base_commit=job.base_commit,
        candidate_tree=job.candidate_tree,
        expected_merge_tree=job.candidate_tree,
        policy_version=job.policy_version,
        actor="standing-policy: tier-0",
        scope="tier-0-auto",
        expires_at=(now + timedelta(minutes=5)).isoformat(),
    )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced authorization transition failure"
    ):
        queue.db.create_authorization_and_transition(auth, now=now)

    count = queue.db.conn.execute(
        "SELECT COUNT(*) FROM merge_authorizations WHERE review_job_id = ?",
        (job.review_job_id,),
    ).fetchone()[0]
    assert count == 0
    current = queue.db.get_review_job(job.review_job_id)
    assert current is not None
    assert current.state == ReviewJobState.MERGE_READY.value


def test_group_d_malformed_or_non_utc_expiry_fails_closed():
    now = datetime.now(timezone.utc)
    for expires_at in (
        "",
        "not-a-time",
        now.replace(tzinfo=None).isoformat(),
        "2030-01-01T00:00:00+05:00",
    ):
        assert MergeAuthorization(expires_at=expires_at).is_expired is True
