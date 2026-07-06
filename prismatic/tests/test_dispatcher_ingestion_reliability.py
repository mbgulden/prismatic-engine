from types import SimpleNamespace

from prismatic import dispatcher


class _Collector:
    def record_credit(self, **kwargs):
        pass

    def record_agent_run(self, **kwargs):
        pass


def test_dispatch_event_ledger_suppresses_replay_after_success(tmp_path):
    dedup = dispatcher.EventRouterDedup(str(tmp_path / "events.db"))

    assert dedup.begin_dispatch_event("GRO-1", "agent:ned", "cycle-a") is True
    dedup.mark_dispatch_processed("GRO-1", "agent:ned", "cycle-a")

    assert dedup.begin_dispatch_event("GRO-1", "agent:ned", "cycle-b") is False
    assert dedup.get_failed_dispatches() == []
    dedup.close()


def test_failed_dispatch_is_visible_and_retryable(tmp_path):
    dedup = dispatcher.EventRouterDedup(str(tmp_path / "events.db"))

    assert dedup.begin_dispatch_event("GRO-2", "agent:kai", "cycle-a") is True
    dedup.mark_dispatch_failed("GRO-2", "agent:kai", "cycle-a", "transport down")

    failed = dedup.get_failed_dispatches()
    assert len(failed) == 1
    assert failed[0]["issue_id"] == "GRO-2"
    assert failed[0]["status"] == "failed"
    assert failed[0]["last_error"] == "transport down"

    assert dedup.begin_dispatch_event("GRO-2", "agent:kai", "cycle-b") is True
    dedup.mark_dispatch_processed("GRO-2", "agent:kai", "cycle-b")
    assert dedup.get_failed_dispatches() == []
    dedup.close()


def test_dispatch_once_does_not_duplicate_side_effects_across_cycles(
    monkeypatch, tmp_path
):
    dedup = dispatcher.EventRouterDedup(str(tmp_path / "events.db"))
    launches = []

    monkeypatch.setattr(dispatcher, "AGENT_CONFIG", {"fred": {}})
    monkeypatch.setattr(dispatcher, "setup_pipeline_issues", lambda: [])
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", lambda max_age_minutes=5: 0)
    monkeypatch.setattr(dispatcher, "recover_stalled_agy", lambda max_retries=3: None)
    monkeypatch.setattr(
        dispatcher, "detect_origin_completions", lambda dedup, cycle_id: 0
    )
    monkeypatch.setattr(dispatcher, "add_comment", lambda issue_id, body: True)
    monkeypatch.setattr(dispatcher, "get_collector", lambda: _Collector())
    monkeypatch.setattr(
        dispatcher,
        "evaluate_agent_launch",
        lambda *args, **kwargs: SimpleNamespace(
            action=dispatcher.PolicyAction.ALLOW,
            reason="ok",
            estimated_cost=0,
        ),
    )
    monkeypatch.setattr(
        dispatcher,
        "get_issues_with_label",
        lambda label: [
            {"id": "linear-uuid-1", "identifier": "GRO-1", "title": "Reliable event"}
        ],
    )

    def launch(issue_id, title=""):
        launches.append((issue_id, title))
        return True

    monkeypatch.setattr(dispatcher, "AGENT_LAUNCHERS", {"fred": launch})

    first = dispatcher.dispatch_once(dedup, pipelines={"pipelines": {}})
    second = dispatcher.dispatch_once(dedup, pipelines={"pipelines": {}})

    assert first["dispatched"] == 1
    assert second["dispatched"] == 0
    assert launches == [("linear-uuid-1", "Reliable event")]
    dedup.close()
