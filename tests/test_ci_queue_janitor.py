"""Unit tests for scripts/ci_queue_janitor.py — the pure cancellation decision.

No network. Covers the safety invariants: never cancel in_progress runs, never
cancel deploy runs, never cancel fresh runs, only cancel stale queued runs.
"""

import datetime
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_queue_janitor.py"
_spec = importlib.util.spec_from_file_location("ci_queue_janitor", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

should_cancel = _mod.should_cancel

NOW = datetime.datetime(2026, 9, 29, 3, 30, tzinfo=datetime.timezone.utc)
MAIN = "6f35b85c1b959e29caf2d0fcce17e902c7de77c5"
DEPLOY_FRESH = "abc12345def6789012345678901234567890abcd"
PR_HEAD = "deadbeef1234567890abcdef1234567890abcdef"
MERGED_HEAD = "6f35098411111111111111111111111111111111"


def run(status, head, path=".github/workflows/test.yml", created_at=None):
    return {
        "id": 1,
        "name": "test",
        "status": status,
        "head_sha": head,
        "path": path,
        "created_at": created_at,
    }


def old_ts():
    return (NOW - datetime.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_ts():
    return (NOW - datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def decide(r):
    return should_cancel(r, [MAIN, DEPLOY_FRESH], [PR_HEAD], NOW)


def test_stale_queued_run_cancelled():
    ok, reason = decide(run("queued", MERGED_HEAD, created_at=old_ts()))
    assert (ok, reason) == (True, "stale-head")


def test_stale_waiting_requested_pending_cancelled():
    for status in ("waiting", "requested", "pending"):
        ok, reason = decide(run(status, MERGED_HEAD, created_at=old_ts()))
        assert ok, status


def test_in_progress_never_cancelled_even_on_merged_branch():
    ok, reason = decide(run("in_progress", MERGED_HEAD, created_at=old_ts()))
    assert (ok, reason) == (False, "not-cancellable-status")


def test_completed_run_never_cancelled():
    ok, reason = decide(run("completed", MERGED_HEAD, created_at=old_ts()))
    assert ok is False


def test_deploy_workflow_never_cancelled():
    ok, reason = decide(
        run(
            "queued",
            MERGED_HEAD,
            path=".github/workflows/post-merge-deploy.yml",
            created_at=old_ts(),
        )
    )
    assert (ok, reason) == (False, "deploy-protected")


def test_janitor_own_workflow_never_cancelled():
    ok, reason = decide(
        run(
            "queued",
            MAIN,
            path=".github/workflows/ci-queue-janitor.yml",
            created_at=old_ts(),
        )
    )
    assert ok is False  # protected: current-main head AND self path


def test_current_main_head_kept():
    ok, reason = decide(run("queued", MAIN, created_at=old_ts()))
    assert (ok, reason) == (False, "protected-branch-head")


def test_deploy_fresh_head_kept():
    ok, reason = decide(run("queued", DEPLOY_FRESH, created_at=old_ts()))
    assert (ok, reason) == (False, "protected-branch-head")


def test_open_pr_head_kept():
    ok, reason = decide(run("queued", PR_HEAD, created_at=old_ts()))
    assert (ok, reason) == (False, "open-pr-head")


def test_too_new_run_kept():
    ok, reason = decide(run("queued", MERGED_HEAD, created_at=new_ts()))
    assert (ok, reason) == (False, "too-new")


def test_malformed_timestamp_not_treated_as_new():
    ok, reason = decide(run("queued", MERGED_HEAD, created_at="not-a-time"))
    assert (ok, reason) == (True, "stale-head")


def test_missing_created_at_not_treated_as_new():
    ok, reason = decide(run("queued", MERGED_HEAD))
    assert (ok, reason) == (True, "stale-head")


def test_empty_protected_and_open_lists_still_safe():
    # Degenerate inputs must not flip a keep into a cancel on the invariants.
    ok, _ = should_cancel(
        run(
            "queued",
            MERGED_HEAD,
            path=".github/workflows/post-merge-deploy.yml",
            created_at=old_ts(),
        ),
        [],
        [],
        NOW,
    )
    assert ok is False
    ok, _ = should_cancel(
        run("in_progress", MERGED_HEAD, created_at=old_ts()), [], [], NOW
    )
    assert ok is False


def test_fixture_replay_of_2026_09_28_2125_event():
    """Replay the real 21:25 MDT janitor event as a fixture.

    Merged branch muse/deploy-hardening-sha (head 6f350984) left 2 stale queued
    runs; everything else must be kept. Expected: exactly those 2 cancel.
    """
    old_main = "10198e2a6037000000000000000000000000000000"  # pre-#629 main
    fixture = [
        (
            "36516866230",
            "Shadow Event Feed",
            "queued",
            MERGED_HEAD,
            ".github/workflows/shadow-event-feed.yml",
            old_ts(),
            True,
        ),
        (
            "36516610320",
            "test",
            "queued",
            MERGED_HEAD,
            ".github/workflows/test.yml",
            old_ts(),
            True,
        ),
        (
            "36516866001",
            "test",
            "queued",
            MAIN,
            ".github/workflows/test.yml",
            old_ts(),
            False,
        ),  # current main
        (
            "36516866002",
            "test",
            "queued",
            PR_HEAD,
            ".github/workflows/test.yml",
            old_ts(),
            False,
        ),  # live PR
        (
            "36516866003",
            "post-merge-deploy",
            "queued",
            MAIN,
            ".github/workflows/post-merge-deploy.yml",
            old_ts(),
            False,
        ),  # deploy
        (
            "36516866004",
            "signal",
            "in_progress",
            MERGED_HEAD,
            ".github/workflows/test.yml",
            old_ts(),
            False,
        ),  # never touch
        (
            "36516866005",
            "test",
            "queued",
            old_main,
            ".github/workflows/test.yml",
            new_ts(),
            False,
        ),  # too new (race)
    ]
    cancelled = []
    for rid, name, status, head, path, created_at, _want in fixture:
        ok, reason = should_cancel(
            {
                "id": rid,
                "name": name,
                "status": status,
                "head_sha": head,
                "path": path,
                "created_at": created_at,
            },
            [MAIN, DEPLOY_FRESH],
            [PR_HEAD],
            NOW,
        )
        if ok:
            cancelled.append((rid, reason))
    assert [c[0] for c in cancelled] == ["36516866230", "36516610320"]
    assert all(r == "stale-head" for _, r in cancelled)
    for rid, _n, _s, _h, _p, _c, want in fixture:
        assert (rid in [c[0] for c in cancelled]) == want, rid
