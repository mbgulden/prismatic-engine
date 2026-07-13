# Task GRO-2895: Write 15+ Integration Tests for Peer Review Pipeline

We have pulled, applied, and verified the peer review pipeline changes and the integration test suite produced by session `8206235204008637994`. 

A total of **19 integration tests** were successfully verified, satisfying all test coverage requirements for Phase 2 / Gap 8.

---

## 1. Summary of Test Coverage

The integration test suite is located in the `agentic-swarm-ops` repository under [tests/test_peer_review_pipeline.py](file:///home/ubuntu/work/agentic-swarm-ops/tests/test_peer_review_pipeline.py) and [tests/test_peer_review_wiring.py](file:///home/ubuntu/work/agentic-swarm-ops/tests/test_peer_review_wiring.py).

The 19 tests map to the Gap 8 requirement categories as follows:

### A. High Impact & Risk Classification (`is_high_impact` - 7 tests)
* **`test_process_prs_bypass_high_risk`**: Verifies that any PR associated with an issue containing high-risk labels (e.g., `requires:human-approval`) bypasses the auto-merger.
* **`test_risk_bypass_labels`**: Parametric test covering 6 distinct high-risk labels:
  - `requires:human-approval`
  - `impact:high`
  - `impact:critical`
  - `output:requires-attention`
  - `external-write`
  - `secrets`

### B. Peer Review Request (`request_peer_review` - 5 tests)
* **`test_reviewer_mapping` & `test_agent_reviewer_mapping`**: Asserts the correct cyclic mapping for peer reviewers (Agy $\rightarrow$ Jules $\rightarrow$ Codex $\rightarrow$ Agy).
* **`test_trigger_peer_review` & `test_process_prs_trigger_review`**: Ensures peer reviews correctly post comments on Linear and dispatch signals to the reviewer.
* **`test_duplicate_review_prevention`**: Ensures we do not trigger multiple reviews for the same issue/PR.

### C. Verdict Transitions & Fallbacks (3 tests)
* **`test_process_prs_ci_failure_bypass`**: Checks that a PR with failing CI does not get auto-merged.
* **`test_process_prs_no_ci_fallback_local_success`**: Verifies fallback to local script execution of unit tests when GitHub Status checks are missing.
* **`test_process_prs_no_ci_fallback_local_failure`**: Verifies merge block if local fallback test fails.

### E. Rework Loops (3 tests)
* **`test_route_rework_conflict` & `test_process_prs_route_conflict`**: Verifies routing merge conflicts back to the submitting worker via Linear comment and signal.
* **`test_route_rework_feedback` & `test_process_prs_route_feedback`**: Verifies routing requested changes (`CHANGES_REQUESTED` review state) back to the submitting worker.
* **`test_duplicate_rework_prevention`**: Prevents commenting duplicate rework messages on Linear.

### F. End-to-End Flow (2 tests)
* **`test_process_prs_auto_merge_success`**: Verifies end-to-end integration: approved PR with successful CI results in a clean auto-merge.
* **`test_review_signal_delivery` & `test_rework_signal_delivery`**: Verifies integration with the Prismatic file signal provider to ensure correct communication to the runner.

---

## 2. Test Execution Output

All 19 tests pass successfully:

```text
$ python3 -m unittest discover -s tests -p "test_peer_review*.py"
Peer review already triggered for GRO-101
.Routing conflict on GRO-101 back to agent:jules
Rework for conflict already routed for GRO-101
..
Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
Merging PR #123...
Successfully merged PR #123
.
Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
PR #123 has high-risk labels. Skipping autonomous actions.
.
Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: failure
.
Checking mbgulden/repo...
No remote status checks found for PR #123. Running local tests as fallback...
PR #123: 'Update docs' | Mergeable: True | CI: failure
.
Checking mbgulden/repo...
No remote status checks found for PR #123. Running local tests as fallback...
PR #123: 'Update docs' | Mergeable: True | CI: success
Merging PR #123...
Successfully merged PR #123
.
Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: False | CI: success
Routing conflict on GRO-101 back to agent:jules
.
Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
Routing feedback on GRO-101 back to agent:jules
.
Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
Triggering peer review for GRO-101 -> agent:codex
..
Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
PR #123 has high-risk labels. Skipping autonomous actions.

Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
PR #123 has high-risk labels. Skipping autonomous actions.

Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
PR #123 has high-risk labels. Skipping autonomous actions.

Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
PR #123 has high-risk labels. Skipping autonomous actions.

Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
PR #123 has high-risk labels. Skipping autonomous actions.

Checking mbgulden/repo...
PR #123: 'Update docs' | Mergeable: True | CI: success
PR #123 has high-risk labels. Skipping autonomous actions.
.Routing conflict on GRO-101 back to agent:jules
.Routing feedback on GRO-101 back to agent:jules
.Triggering peer review for GRO-101 -> agent:codex
..Triggering peer review for GRO-101 -> agent:jules
.Routing feedback on GRO-101 back to agent:codex
.
----------------------------------------------------------------------
Ran 19 tests in 0.019s

OK
```