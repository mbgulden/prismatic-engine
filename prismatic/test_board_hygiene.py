from datetime import UTC, datetime, timedelta

from prismatic.board_hygiene import (
    BoardHygieneConfig,
    BoardHygieneWorker,
    IssueSnapshot,
    analyze_board,
    classify_noise,
)


NOW = datetime(2026, 7, 7, tzinfo=UTC)
CONFIG = BoardHygieneConfig(now=NOW, stale_after_days=7)


def issue(identifier, **overrides):
    base = {
        "identifier": identifier,
        "title": f"Task {identifier}",
        "state": {"name": "Todo", "type": "unstarted"},
        "labels": {"nodes": [{"name": "agent:ned"}, {"name": "dispatch:ready"}]},
        "updatedAt": NOW.isoformat(),
    }
    base.update(overrides)
    return base


def test_suppresses_completed_duplicate_umbrella_stale_and_unassigned_noise():
    old = (NOW - timedelta(days=30)).isoformat()
    snapshots = [
        IssueSnapshot.from_issue(issue("GRO-1", state={"name": "Done", "type": "completed"})),
        IssueSnapshot.from_issue(issue("GRO-2", labels=["agent:ned", "duplicate"])),
        IssueSnapshot.from_issue(issue("GRO-3", title="Epic: Umbrella tracking", labels=["agent:ned"])),
        IssueSnapshot.from_issue(issue("GRO-4", updatedAt=old)),
        IssueSnapshot.from_issue(issue("GRO-5", labels=["prismatic-engine"])),
    ]

    assert [classify_noise(snapshot, CONFIG) for snapshot in snapshots] == [
        "completed",
        "duplicate",
        "umbrella",
        "stale",
        "unassigned",
    ]


def test_first_actionable_snapshot_emits_only_actionable_items():
    result = analyze_board(
        [
            IssueSnapshot.from_issue(issue("GRO-10")),
            IssueSnapshot.from_issue(issue("GRO-11", labels=["duplicate"])),
            IssueSnapshot.from_issue(issue("GRO-12", state={"name": "Done", "type": "completed"})),
        ],
        config=CONFIG,
    )

    assert result.should_emit is True
    assert result.delta.as_dict() == {
        "should_emit": True,
        "new_actionable": ["GRO-10"],
        "changed_actionable": [],
        "resolved_actionable": [],
    }
    assert result.as_dict()["suppressed"] == {
        "completed": ["GRO-12"],
        "duplicate": ["GRO-11"],
    }


def test_noise_only_changes_do_not_emit_when_actionable_fingerprints_are_stable():
    first = analyze_board(
        [IssueSnapshot.from_issue(issue("GRO-20"))],
        config=CONFIG,
    )
    second = analyze_board(
        [
            IssueSnapshot.from_issue(issue("GRO-20")),
            IssueSnapshot.from_issue(issue("GRO-21", title="Duplicate: old work")),
            IssueSnapshot.from_issue(issue("GRO-22", labels=["epic"])),
        ],
        previous_fingerprints=first.fingerprints,
        config=CONFIG,
    )

    assert second.should_emit is False
    assert second.delta.as_dict() == {
        "should_emit": False,
        "new_actionable": [],
        "changed_actionable": [],
        "resolved_actionable": [],
    }
    assert second.as_dict()["suppressed"] == {
        "duplicate": ["GRO-21"],
        "umbrella": ["GRO-22"],
    }


def test_actionable_changes_and_resolution_emit_deltas():
    first = analyze_board(
        [
            IssueSnapshot.from_issue(issue("GRO-30")),
            IssueSnapshot.from_issue(issue("GRO-31")),
        ],
        config=CONFIG,
    )
    second = analyze_board(
        [
            IssueSnapshot.from_issue(issue("GRO-30", title="Task GRO-30 - updated")),
            IssueSnapshot.from_issue(issue("GRO-32")),
        ],
        previous_fingerprints=first.fingerprints,
        config=CONFIG,
    )

    assert second.should_emit is True
    assert second.delta.as_dict() == {
        "should_emit": True,
        "new_actionable": ["GRO-32"],
        "changed_actionable": ["GRO-30"],
        "resolved_actionable": ["GRO-31"],
    }


def test_worker_persists_state_between_runs(tmp_path):
    state_path = tmp_path / "board-hygiene.json"
    worker = BoardHygieneWorker(state_path, CONFIG)

    first = worker.run([issue("GRO-40")])
    second = worker.run([issue("GRO-40"), issue("GRO-41", labels=["duplicate"])])
    third = worker.run([issue("GRO-40", labels=["agent:ned", "dispatch:ready", "dispatch:priority"])])

    assert first.should_emit is True
    assert second.should_emit is False
    assert third.should_emit is True
    assert third.delta.as_dict()["changed_actionable"] == ["GRO-40"]
    assert state_path.exists()
