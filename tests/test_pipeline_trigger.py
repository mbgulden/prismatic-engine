"""
Tests for cross-project pipeline trigger events (GRO-1571-C).

Covers:
- PipelineTriggerEvent creation, serialisation, and helpers
- Affected target calculation (stub matching logic)
- Dispatcher trigger event interface
"""

import unittest
from datetime import datetime, timedelta, timezone
from prismatic.core.events import PipelineTriggerEvent, EventType
from prismatic.core.targets import (
    AffectedTarget,
    calculate_affected_targets,
    _pipeline_matches_event,
    _find_downstream_targets,
)


# ═══════════════════════════════════════════════════════════════
# PipelineTriggerEvent tests
# ═══════════════════════════════════════════════════════════════


class TestPipelineTriggerEvent(unittest.TestCase):
    """Event schema creation, serialisation, and helpers."""

    def test_create_minimal_event(self) -> None:
        """Minimal creation should auto-generate event_id and timestamp."""
        event = PipelineTriggerEvent(
            source_repo="hd-engine",
            source_branch="staging-hd",
            event_type=EventType.MERGE,
        )
        self.assertEqual(event.source_repo, "hd-engine")
        self.assertEqual(event.source_branch, "staging-hd")
        self.assertEqual(event.event_type, EventType.MERGE)
        self.assertIsNotNone(event.event_id)
        self.assertIsNotNone(event.timestamp)
        self.assertIsInstance(event.event_id, str)
        self.assertEqual(len(event.event_id), 36)  # UUID4 hex

    def test_create_with_explicit_id_and_timestamp(self) -> None:
        """All fields can be provided explicitly."""
        ts = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
        event = PipelineTriggerEvent(
            source_repo="beyondsaas",
            source_branch="main",
            event_type=EventType.PUSH,
            timestamp=ts,
            event_id="test-001",
            metadata={"commit_sha": "abc123"},
        )
        self.assertEqual(event.event_id, "test-001")
        self.assertEqual(event.timestamp, ts)
        self.assertEqual(event.metadata["commit_sha"], "abc123")

    def test_age_seconds_positive(self) -> None:
        """age_seconds should be positive for events created in the past."""
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        event = PipelineTriggerEvent(
            source_repo="r1",
            source_branch="main",
            event_type=EventType.WEBHOOK,
            timestamp=past,
        )
        self.assertGreater(event.age_seconds, 25)  # allow a few seconds drift
        self.assertLess(event.age_seconds, 60)

    def test_to_dict_round_trip(self) -> None:
        """to_dict() → from_dict() should preserve all fields."""
        original = PipelineTriggerEvent(
            source_repo="darius-star",
            source_branch="staging-star",
            event_type=EventType.MERGE,
            metadata={"pr_number": 42},
        )
        data = original.to_dict()
        restored = PipelineTriggerEvent.from_dict(data)

        self.assertEqual(restored.event_id, original.event_id)
        self.assertEqual(restored.source_repo, original.source_repo)
        self.assertEqual(restored.source_branch, original.source_branch)
        self.assertEqual(restored.event_type, original.event_type)
        self.assertEqual(restored.timestamp, original.timestamp)
        self.assertEqual(restored.metadata, original.metadata)

    def test_to_dict_structure(self) -> None:
        """to_dict() should contain all expected keys."""
        event = PipelineTriggerEvent(
            source_repo="re1",
            source_branch="branch1",
            event_type=EventType.MANUAL,
        )
        data = event.to_dict()
        self.assertIn("event_id", data)
        self.assertIn("source_repo", data)
        self.assertIn("source_branch", data)
        self.assertIn("event_type", data)
        self.assertIn("timestamp", data)
        self.assertIn("metadata", data)
        self.assertEqual(data["event_type"], "manual")

    def test_from_dict_missing_event_id_generates_new(self) -> None:
        """from_dict() should create a new UUID4 when event_id is missing."""
        data = {
            "source_repo": "r1",
            "source_branch": "b1",
            "event_type": "push",
            "timestamp": "2026-06-16T12:00:00+00:00",
            "metadata": {},
        }
        event = PipelineTriggerEvent.from_dict(data)
        self.assertIsNotNone(event.event_id)
        self.assertEqual(len(event.event_id), 36)  # UUID4

    def test_event_type_enum_values(self) -> None:
        """All EventType enum values should be valid strings."""
        self.assertEqual(EventType.PUSH.value, "push")
        self.assertEqual(EventType.MERGE.value, "merge")
        self.assertEqual(EventType.PR_CLOSED.value, "pr_closed")
        self.assertEqual(EventType.WEBHOOK.value, "webhook")
        self.assertEqual(EventType.MANUAL.value, "manual")


# ═══════════════════════════════════════════════════════════════
# Affected target calculation tests
# ═══════════════════════════════════════════════════════════════


class TestCalculateAffectedTargets(unittest.TestCase):
    """Stub target calculation logic."""

    def setUp(self) -> None:
        self.sample_pipelines = {
            "pipelines": {
                "test-pipeline": {
                    "name": "Test Pipeline",
                    "chain": [
                        {"agent": "hd-engine", "label": "agent:agy", "step": "Research"},
                        {"agent": "beyondsaas", "label": "agent:jules", "step": "Build"},
                        {"agent": "fred", "label": "agent:fred", "step": "Deploy"},
                    ],
                    "triggers": ["hd-engine", "merge"],
                },
                "content-pipeline": {
                    "name": "Content",
                    "chain": [
                        {"agent": "beyondsaas", "label": "agent:agy", "step": "Draft"},
                        {"agent": "fred", "label": "agent:fred", "step": "Publish"},
                    ],
                    "triggers": ["beyondsaas"],
                },
            }
        }

    def test_matching_repo_returns_downstream_targets(self) -> None:
        """A MERGE event on hd-engine should return subsequent chain steps."""
        event = PipelineTriggerEvent(
            source_repo="hd-engine",
            source_branch="staging-hd",
            event_type=EventType.MERGE,
        )
        targets = calculate_affected_targets(event, self.sample_pipelines)
        self.assertEqual(len(targets), 2)  # beyondsaas + fred follow hd-engine
        self.assertEqual(targets[0].target_repo, "beyondsaas")
        self.assertEqual(targets[1].target_repo, "fred")
        self.assertEqual(targets[0].pipeline_name, "test-pipeline")
        self.assertIn("hd-engine", targets[0].reason)

    def test_no_match_returns_empty(self) -> None:
        """An event on an unmatched repo should return no targets."""
        event = PipelineTriggerEvent(
            source_repo="unknown-repo",
            source_branch="main",
            event_type=EventType.PUSH,
        )
        targets = calculate_affected_targets(event, self.sample_pipelines)
        self.assertEqual(len(targets), 0)

    def test_match_by_event_type(self) -> None:
        """An event whose type matches a trigger keyword should still match."""
        event = PipelineTriggerEvent(
            source_repo="beyondsaas",
            source_branch="main",
            event_type=EventType.MERGE,  # "merge" is in test-pipeline triggers
        )
        targets = calculate_affected_targets(event, self.sample_pipelines)
        # Matches test-pipeline by event-type "merge", and also content-pipeline
        # by repo "beyondsaas".  But "beyondsaas" is the first step in the
        # content-pipeline chain, so no downstream from it there.
        # From test-pipeline, "beyondsaas" is the second chain step after
        # "hd-engine", so the source step match fails.
        # Expect at least one from the event-type match.
        self.assertGreater(len(targets), 0)

    def test_empty_config_returns_empty(self) -> None:
        """No pipelines defined → no targets."""
        event = PipelineTriggerEvent(
            source_repo="r1",
            source_branch="main",
            event_type=EventType.PUSH,
        )
        targets = calculate_affected_targets(event, {})
        self.assertEqual(len(targets), 0)

    def test_pipeline_without_triggers_never_matches(self) -> None:
        """A pipeline with no triggers list should never produce targets."""
        config = {
            "pipelines": {
                "no-triggers": {
                    "chain": [
                        {"agent": "a1", "label": "l1"},
                    ],
                },
            }
        }
        event = PipelineTriggerEvent(
            source_repo="anything",
            source_branch="main",
            event_type=EventType.PUSH,
        )
        targets = calculate_affected_targets(event, config)
        self.assertEqual(len(targets), 0)

    def test_last_chain_step_no_downstream(self) -> None:
        """If the source step is the last in the chain, no targets returned."""
        config = {
            "pipelines": {
                "single-step": {
                    "chain": [
                        {"agent": "hd-engine", "label": "agent:agy"},
                    ],
                    "triggers": ["hd-engine"],
                }
            }
        }
        event = PipelineTriggerEvent(
            source_repo="hd-engine",
            source_branch="main",
            event_type=EventType.PUSH,
        )
        targets = calculate_affected_targets(event, config)
        self.assertEqual(len(targets), 0)

    def test_multiple_pipelines_matching(self) -> None:
        """An event matching multiple pipelines should return all downstream targets."""
        config = {
            "pipelines": {
                "p1": {
                    "chain": [
                        {"agent": "hd-engine", "label": "l1"},
                        {"agent": "beyondsaas", "label": "l2"},
                    ],
                    "triggers": ["hd-engine"],
                },
                "p2": {
                    "chain": [
                        {"agent": "hd-engine", "label": "l1"},
                        {"agent": "active-oahu", "label": "l2"},
                    ],
                    "triggers": ["hd-engine"],
                },
            }
        }
        event = PipelineTriggerEvent(
            source_repo="hd-engine",
            source_branch="staging",
            event_type=EventType.PUSH,
        )
        targets = calculate_affected_targets(event, config)
        self.assertEqual(len(targets), 2)  # one from each pipeline


# ═══════════════════════════════════════════════════════════════
# Pipeline match helper tests
# ═══════════════════════════════════════════════════════════════


class TestPipelineMatchesEvent(unittest.TestCase):
    """Direct tests for the internal _pipeline_matches_event helper."""

    def test_match_by_repo_name(self) -> None:
        event = PipelineTriggerEvent("hd-engine", "main", EventType.PUSH)
        pipeline = {"triggers": ["hd-engine"]}
        self.assertTrue(_pipeline_matches_event(event, pipeline))

    def test_match_by_event_type(self) -> None:
        event = PipelineTriggerEvent("re1", "main", EventType.WEBHOOK)
        pipeline = {"triggers": ["webhook"]}
        self.assertTrue(_pipeline_matches_event(event, pipeline))

    def test_match_case_insensitive(self) -> None:
        event = PipelineTriggerEvent("HD-ENGINE", "main", EventType.PUSH)
        pipeline = {"triggers": ["hd-engine"]}
        self.assertTrue(_pipeline_matches_event(event, pipeline))

    def test_no_triggers_no_match(self) -> None:
        event = PipelineTriggerEvent("r1", "main", EventType.PUSH)
        pipeline = {"triggers": []}
        self.assertFalse(_pipeline_matches_event(event, pipeline))

    def test_no_triggers_key_defaults(self) -> None:
        event = PipelineTriggerEvent("r1", "main", EventType.PUSH)
        pipeline = {}  # no triggers key at all
        self.assertFalse(_pipeline_matches_event(event, pipeline))


# ═══════════════════════════════════════════════════════════════
# Downstream target helper tests
# ═══════════════════════════════════════════════════════════════


class TestFindDownstreamTargets(unittest.TestCase):
    """Direct tests for the internal _find_downstream_targets helper."""

    def test_returns_following_steps(self) -> None:
        event = PipelineTriggerEvent("agy", "main", EventType.PUSH)
        chain = [
            {"agent": "agy", "label": "l1"},
            {"agent": "jules", "label": "l2"},
            {"agent": "fred", "label": "l3"},
        ]
        targets = _find_downstream_targets(event, "p1", chain)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].target_repo, "jules")
        self.assertEqual(targets[1].target_repo, "fred")

    def test_no_matching_source_returns_empty(self) -> None:
        event = PipelineTriggerEvent("unknown", "main", EventType.PUSH)
        chain = [
            {"agent": "agy", "label": "l1"},
            {"agent": "fred", "label": "l2"},
        ]
        targets = _find_downstream_targets(event, "p1", chain)
        self.assertEqual(len(targets), 0)

    def test_target_priority_descending(self) -> None:
        """Earlier downstream steps should have higher priority."""
        event = PipelineTriggerEvent("agy", "main", EventType.PUSH)
        chain = [
            {"agent": "agy", "label": "l1"},
            {"agent": "step1", "label": "l2"},
            {"agent": "step2", "label": "l3"},
        ]
        targets = _find_downstream_targets(event, "p1", chain)
        self.assertGreater(targets[0].priority, targets[1].priority)


# ═══════════════════════════════════════════════════════════════
# Dispatcher trigger interface tests
# ═══════════════════════════════════════════════════════════════


class TestDispatcherTriggerInterface(unittest.TestCase):
    """Dispatcher.handle_trigger_event integration."""

    def setUp(self) -> None:
        from unittest.mock import MagicMock
        from prismatic.core.dispatcher import Dispatcher

        self.dispatcher = Dispatcher(plugin_loader=MagicMock())

    def test_handle_without_config_returns_empty(self) -> None:
        """Before load_pipelines_config is called, no targets should match."""
        event = PipelineTriggerEvent("hd-engine", "main", EventType.MERGE)
        targets = self.dispatcher.handle_trigger_event(event)
        self.assertEqual(len(targets), 0)

    def test_handle_with_config_returns_downstream(self) -> None:
        """After loading config, matching events should return targets."""
        config = {
            "pipelines": {
                "test": {
                    "chain": [
                        {"agent": "hd-engine", "label": "l1"},
                        {"agent": "beyondsaas", "label": "l2"},
                    ],
                    "triggers": ["hd-engine"],
                }
            }
        }
        self.dispatcher.load_pipelines_config(config)

        event = PipelineTriggerEvent("hd-engine", "main", EventType.PUSH)
        targets = self.dispatcher.handle_trigger_event(event)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].target_repo, "beyondsaas")

    def test_handle_no_match_returns_empty(self) -> None:
        """Non-matching events should return empty even with config loaded."""
        config = {
            "pipelines": {
                "ingest": {
                    "chain": [{"agent": "agy", "label": "l1"}],
                    "triggers": ["docs-repo"],
                }
            }
        }
        self.dispatcher.load_pipelines_config(config)

        event = PipelineTriggerEvent("unknown", "main", EventType.PUSH)
        targets = self.dispatcher.handle_trigger_event(event)
        self.assertEqual(len(targets), 0)

    def test_reload_config_updates_pipelines(self) -> None:
        """Calling load_pipelines_config again should replace the old config."""
        config_a = {
            "pipelines": {
                "a": {
                    "chain": [{"agent": "r1", "label": "l1"}, {"agent": "r2", "label": "l2"}],
                    "triggers": ["r1"],
                }
            }
        }
        config_b = {
            "pipelines": {}  # empty — overrides config_a
        }

        self.dispatcher.load_pipelines_config(config_a)
        self.dispatcher.load_pipelines_config(config_b)

        event = PipelineTriggerEvent("r1", "main", EventType.PUSH)
        targets = self.dispatcher.handle_trigger_event(event)
        self.assertEqual(len(targets), 0, "Empty config should override previous one")


if __name__ == "__main__":
    unittest.main()
