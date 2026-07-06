from prismatic.label_debt import analyze_issues


def issue(identifier, title, labels, state="Todo", state_type="unstarted", description="", **extra):
    return {
        "identifier": identifier,
        "title": title,
        "description": description,
        "state": {"name": state, "type": state_type},
        "labels": {"nodes": [{"name": label} for label in labels]},
        **extra,
    }


def actions_by_id(report):
    return {action["identifier"]: action for action in report["actions"]}


def test_scorecard_separates_labeled_from_launchable_work():
    report = analyze_issues(
        [
            issue("GRO-1", "Ready work", ["agent:ned", "dispatch:ready"]),
            issue("GRO-2", "Needs backfill", ["agent:fred"]),
            issue("GRO-3", "Duplicate of GRO-1", ["agent:ned", "dispatch:ready"]),
            issue("GRO-4", "Unlabeled backlog", []),
        ]
    )

    assert report["summary"]["total_issues"] == 4
    assert report["summary"]["labeled_work"] == 3
    assert report["summary"]["launchable_work"] == 1
    assert report["summary"]["label_to_launchable_gap"] == 2
    assert report["summary"]["launchable_ratio"] == 0.3333
    assert report["launchable_identifiers"] == ["GRO-1"]


def test_backfills_legitimate_active_work_missing_dispatch_ready():
    report = analyze_issues([issue("GRO-10", "Implement queue probe", ["agent:ned"])])

    action = actions_by_id(report)["GRO-10"]
    assert action["action"] == "backfill_dispatch_ready"
    assert action["add_labels"] == ["dispatch:ready"]
    assert action["remove_labels"] == []


def test_cancels_duplicates_and_removes_dispatch_labels():
    report = analyze_issues(
        [issue("GRO-11", "Stale clone of GRO-7", ["agent:ned", "dispatch:ready", "dispatch:priority"])]
    )

    action = actions_by_id(report)["GRO-11"]
    assert action["action"] == "cancel_duplicate_or_stale_clone"
    assert action["target_state"] == "Canceled"
    assert action["remove_labels"] == ["dispatch:priority", "dispatch:ready"]


def test_quarantines_review_noise_outside_production_queue():
    report = analyze_issues(
        [
            issue(
                "GRO-12",
                "Post-publish audit finding already reviewed",
                ["agent:ned-review", "dispatch:ready"],
                state="In Review",
                state_type="started",
            )
        ]
    )

    action = actions_by_id(report)["GRO-12"]
    assert action["action"] == "quarantine_noise"
    assert action["add_labels"] == ["queue:quarantine"]
    assert action["remove_labels"] == ["dispatch:ready"]


def test_parks_done_or_archived_leftovers():
    report = analyze_issues(
        [issue("GRO-13", "Completed thing", ["agent:ned", "dispatch:ready"], state="Done", state_type="completed")]
    )

    action = actions_by_id(report)["GRO-13"]
    assert action["action"] == "park_archived_leftover"
    assert action["add_labels"] == ["queue:quarantine"]
    assert action["remove_labels"] == ["dispatch:ready"]


def test_blocked_work_gets_triage_instead_of_launchable_capacity():
    report = analyze_issues(
        [issue("GRO-14", "Needs credential from Michael", ["agent:ned", "dispatch:ready"])]
    )

    action = actions_by_id(report)["GRO-14"]
    assert action["action"] == "mark_requires_triage"
    assert action["add_labels"] == ["requires:triage"]
    assert action["remove_labels"] == ["dispatch:ready"]
    assert report["summary"]["launchable_work"] == 0
