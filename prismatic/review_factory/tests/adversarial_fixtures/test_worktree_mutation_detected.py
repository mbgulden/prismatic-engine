from prismatic.review_factory.verifier import VerificationWorker


def test_worktree_mutation_detected(tmp_path):
    worker = VerificationWorker(repo_path=tmp_path)

    original_paths = ["a.py", "b.py"]
    proof1 = worker._compute_invariance_proof(original_paths)

    mutated_paths = ["a.py", "b.py", "c.py"]
    proof2 = worker._compute_invariance_proof(mutated_paths)

    assert proof1 != proof2
