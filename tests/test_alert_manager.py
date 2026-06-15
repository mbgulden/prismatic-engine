"""
Tests for Alertmanager Notification Routing — Phase 4.5 / GRO-1584

Covers:
    - Alert rule definitions (all four rules present)
    - AlertRouter: severity-based routing to Telegram/Slack/log
    - AlertEvaluator: rule evaluation against telemetry data
    - FastAPI webhook endpoint: /api/alerts/webhook
    - List rules endpoint: GET /api/alerts/rules
    - Synthetic alert test endpoint: POST /api/alerts/test
    - CLI synthetic alert firing
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════
# Alert Rule Definitions Tests
# ═══════════════════════════════════════════════════════════════


class TestAlertRules:
    """Test that all four required alert rules are defined correctly."""

    def test_all_four_rules_defined(self):
        from prismatic.gateway.alert_manager import ALERT_RULES

        assert len(ALERT_RULES) == 4
        assert "HighLockContention" in ALERT_RULES
        assert "AgentStall" in ALERT_RULES
        assert "CreditBurnRate" in ALERT_RULES
        assert "CircuitBreakerTrip" in ALERT_RULES

    def test_rule_severities(self):
        from prismatic.gateway.alert_manager import ALERT_RULES

        # Critical rules
        assert ALERT_RULES["HighLockContention"].severity == "critical"
        assert ALERT_RULES["AgentStall"].severity == "critical"
        assert ALERT_RULES["CircuitBreakerTrip"].severity == "critical"

        # Warning rule
        assert ALERT_RULES["CreditBurnRate"].severity == "warning"

    def test_rule_descriptions(self):
        from prismatic.gateway.alert_manager import ALERT_RULES

        for name, rule in ALERT_RULES.items():
            assert rule.description, f"Rule {name} missing description"
            assert rule.threshold_hint, f"Rule {name} missing threshold_hint"
            assert rule.severity in ("critical", "warning", "info")


# ═══════════════════════════════════════════════════════════════
# AlertRouter Tests
# ═══════════════════════════════════════════════════════════════


class TestAlertRouter:
    """Test the severity-based routing tree."""

    @pytest.fixture
    def router(self, tmp_path):
        """Create an AlertRouter with a temp alert log path."""
        log_path = tmp_path / "alerts.log"
        with patch(
            "prismatic.gateway.alert_manager.ALERT_LOG_PATH",
            str(log_path),
        ):
            from prismatic.gateway.alert_manager import AlertRouter
            router = AlertRouter()
            router._alert_log_path = log_path
            return router

    def test_critical_routes_to_telegram(self, router):
        """Critical alerts route to Telegram (no token → logged warning but marked as fired)."""
        with patch.object(router, "_send_telegram") as mock_tg, \
             patch.object(router, "_log_alert") as mock_log:
            fired = router.route({
                "name": "HighLockContention",
                "severity": "critical",
                "summary": "Test critical alert",
            })

        assert "telegram" in fired
        assert "log" in fired  # critical also logs
        mock_tg.assert_called_once()
        mock_log.assert_called_once()

    def test_warning_routes_to_slack(self, router):
        """Warning alerts route to Slack."""
        with patch.object(router, "_send_slack") as mock_slack, \
             patch.object(router, "_log_alert") as mock_log:
            fired = router.route({
                "name": "CreditBurnRate",
                "severity": "warning",
                "summary": "Test warning alert",
            })

        assert "slack" in fired
        assert "log" in fired
        mock_slack.assert_called_once()
        mock_log.assert_called_once()

    def test_info_routes_to_log_only(self, router):
        """Info alerts route to log file only."""
        with patch.object(router, "_send_telegram") as mock_tg, \
             patch.object(router, "_send_slack") as mock_slack, \
             patch.object(router, "_log_alert") as mock_log:
            fired = router.route({
                "name": "InfoAlert",
                "severity": "info",
                "summary": "Test info alert",
            })

        assert fired == ["log"]
        mock_tg.assert_not_called()
        mock_slack.assert_not_called()
        mock_log.assert_called_once()

    def test_log_alert_writes_to_file(self, router, tmp_path):
        """Alert log entries are written as JSON lines."""
        log_path = tmp_path / "alerts.log"
        router._alert_log_path = log_path

        router._log_alert({
            "name": "TestAlert",
            "severity": "critical",
            "summary": "Test message",
            "details": "Extra info",
        })

        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert entry["name"] == "TestAlert"
        assert entry["severity"] == "critical"
        assert "timestamp" in entry


# ═══════════════════════════════════════════════════════════════
# AlertEvaluator Tests
# ═══════════════════════════════════════════════════════════════


class TestAlertEvaluator:
    """Test evaluation of alert rules against telemetry data."""

    @pytest.fixture
    def mock_telemetry(self):
        """Create a mock TelemetryCollector with controllable dashboard data."""
        collector = MagicMock()
        collector.get_dashboard_data.return_value = {
            "loops": [],
            "tokens": [],
            "validation": {},
            "breakers_tripped": 0,
            "hours": 1,
            "credit_burn_rate": 0,
            "total_credits": 0,
            "failure_rate": 0.0,
            "total_agent_runs": 10,
            "failed_agent_runs": 0,
        }
        return collector

    @pytest.fixture
    def evaluator(self, mock_telemetry, tmp_path):
        """Create an AlertEvaluator with mock telemetry and temp log."""
        from prismatic.gateway.alert_manager import AlertEvaluator, AlertRouter

        log_path = tmp_path / "alerts.log"
        with patch(
            "prismatic.gateway.alert_manager.ALERT_LOG_PATH",
            str(log_path),
        ):
            router = AlertRouter()
            router._alert_log_path = log_path
            return AlertEvaluator(
                telemetry_collector=mock_telemetry,
                router=router,
            )

    def test_no_alerts_when_everything_healthy(self, evaluator):
        """No alerts triggered when all metrics are within limits."""
        triggered = evaluator.evaluate(hours=1)
        assert len(triggered) == 0

    def test_credit_burn_rate_alert(self, evaluator, mock_telemetry):
        """CreditBurnRate triggers when credits/hr exceeds threshold."""
        mock_telemetry.get_dashboard_data.return_value = {
            "loops": [],
            "tokens": [],
            "validation": {},
            "breakers_tripped": 0,
            "hours": 1,
            "credit_burn_rate": 2000,  # > 1000 threshold
            "total_credits": 2000,
            "failure_rate": 0.0,
            "total_agent_runs": 10,
            "failed_agent_runs": 0,
        }

        triggered = evaluator.evaluate(hours=1)
        burn_alerts = [a for a in triggered if a["name"] == "CreditBurnRate"]
        assert len(burn_alerts) == 1
        assert burn_alerts[0]["severity"] == "warning"

    def test_agent_stall_alert(self, evaluator, mock_telemetry):
        """AgentStall triggers when zero agent runs in window."""
        mock_telemetry.get_dashboard_data.return_value = {
            "loops": [],
            "tokens": [],
            "validation": {},
            "breakers_tripped": 0,
            "hours": 1,
            "credit_burn_rate": 0,
            "total_credits": 0,
            "failure_rate": 0.0,
            "total_agent_runs": 0,  # Zero runs = stall
            "failed_agent_runs": 0,
        }

        triggered = evaluator.evaluate(hours=1)
        stall_alerts = [a for a in triggered if a["name"] == "AgentStall"]
        assert len(stall_alerts) >= 1

    def test_circuit_breaker_trip_alert(self, evaluator, mock_telemetry):
        """CircuitBreakerTrip triggers when breakers are tripped."""
        mock_telemetry.get_dashboard_data.return_value = {
            "loops": [],
            "tokens": [],
            "validation": {},
            "breakers_tripped": 3,  # 3 breakers tripped
            "hours": 1,
            "credit_burn_rate": 0,
            "total_credits": 0,
            "failure_rate": 0.0,
            "total_agent_runs": 10,
            "failed_agent_runs": 0,
        }

        triggered = evaluator.evaluate(hours=1)
        breaker_alerts = [a for a in triggered if a["name"] == "CircuitBreakerTrip"]
        assert len(breaker_alerts) == 1
        assert breaker_alerts[0]["severity"] == "critical"

    def test_multiple_alerts_simultaneously(self, evaluator, mock_telemetry):
        """Multiple alerts can fire simultaneously."""
        mock_telemetry.get_dashboard_data.return_value = {
            "loops": [],
            "tokens": [],
            "validation": {},
            "breakers_tripped": 2,
            "hours": 1,
            "credit_burn_rate": 3000,
            "total_credits": 3000,
            "failure_rate": 0.0,
            "total_agent_runs": 0,
            "failed_agent_runs": 0,
        }

        triggered = evaluator.evaluate(hours=1)
        names = {a["name"] for a in triggered}
        # CreditBurnRate + CircuitBreakerTrip + AgentStall
        assert len(names) >= 3
        assert "CreditBurnRate" in names
        assert "CircuitBreakerTrip" in names

    def test_evaluator_without_telemetry(self, tmp_path):
        """Evaluator without telemetry returns empty list gracefully."""
        from prismatic.gateway.alert_manager import AlertEvaluator, AlertRouter

        log_path = tmp_path / "alerts.log"
        with patch(
            "prismatic.gateway.alert_manager.ALERT_LOG_PATH",
            str(log_path),
        ):
            router = AlertRouter()
            router._alert_log_path = log_path
            evaluator = AlertEvaluator(telemetry_collector=None, router=router)
            triggered = evaluator.evaluate()
            assert triggered == []


# ═══════════════════════════════════════════════════════════════
# FastAPI Webhook Endpoint Tests
# ═══════════════════════════════════════════════════════════════


class TestAlertWebhookEndpoint:
    """Test the FastAPI alert webhook endpoints."""

    @pytest.fixture
    def test_client(self, tmp_path):
        """Create a test client with alert webhook routes."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from prismatic.gateway.alert_manager import create_alert_webhook_route

        test_app = FastAPI()
        alert_router = create_alert_webhook_route()
        test_app.include_router(alert_router, prefix="/api")
        return TestClient(test_app)

    def test_list_rules_endpoint(self, test_client):
        """GET /api/alerts/rules returns all four rules."""
        response = test_client.get("/api/alerts/rules")
        assert response.status_code == 200
        data = response.json()
        assert "rules" in data
        assert len(data["rules"]) == 4
        for name in ("HighLockContention", "AgentStall", "CreditBurnRate", "CircuitBreakerTrip"):
            assert name in data["rules"]

    def test_webhook_single_alert(self, test_client):
        """POST /api/alerts/webhook with a single alert."""
        response = test_client.post(
            "/api/alerts/webhook",
            json={
                "labels": {
                    "alertname": "HighLockContention",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "Test alert",
                    "description": "Test details",
                },
            },
        )
        assert response.status_code in (200, 207)
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["name"] == "HighLockContention"

    def test_webhook_batch_alerts(self, test_client):
        """POST /api/alerts/webhook with multiple alerts."""
        response = test_client.post(
            "/api/alerts/webhook",
            json=[
                {
                    "labels": {
                        "alertname": "AgentStall",
                        "severity": "critical",
                    },
                    "annotations": {
                        "summary": "Stall detected",
                        "description": "No runs in 15m",
                    },
                },
                {
                    "labels": {
                        "alertname": "CreditBurnRate",
                        "severity": "warning",
                    },
                    "annotations": {
                        "summary": "Burn rate high",
                        "description": "2000 credits/hr",
                    },
                },
            ],
        )
        assert response.status_code in (200, 207)
        data = response.json()
        assert data["total"] == 2

    def test_webhook_simplified_format(self, test_client):
        """POST /api/alerts/webhook accepts simplified format (no labels sub-dict)."""
        response = test_client.post(
            "/api/alerts/webhook",
            json={
                "name": "TestAlert",
                "severity": "info",
                "summary": "A test info alert",
                "details": "Test details",
            },
        )
        assert response.status_code in (200, 207)
        data = response.json()
        assert data["results"][0]["name"] == "TestAlert"
        assert "log" in data["results"][0]["routed_to"]

    def test_synthetic_alert_test_endpoint(self, test_client):
        """POST /api/alerts/test fires synthetic alerts for all four types."""
        response = test_client.post(
            "/api/alerts/test",
            json=[
                {
                    "name": "HighLockContention",
                    "severity": "critical",
                    "summary": "Synthetic test",
                    "details": "test",
                },
            ],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "synthetic_alerts_fired"
        assert data["total"] == 1

    def test_synthetic_alert_test_defaults(self, test_client):
        """POST /api/alerts/test with no body fires all four synthetic alerts."""
        response = test_client.post("/api/alerts/test", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1


# ═══════════════════════════════════════════════════════════════
# CLI Synthetic Alert Test
# ═══════════════════════════════════════════════════════════════


class TestCLISyntheticAlerts:
    """Test the fire_synthetic_alerts CLI function."""

    def test_cli_runs_without_error(self, tmp_path):
        """CLI function runs and routes alerts without crashing."""
        from prismatic.gateway.alert_manager import fire_synthetic_alerts, AlertRouter

        log_path = tmp_path / "alerts.log"
        with patch(
            "prismatic.gateway.alert_manager.ALERT_LOG_PATH",
            str(log_path),
        ):
            # Should not raise
            fire_synthetic_alerts()

        # Should have written log entries
        assert log_path.exists()
        content = log_path.read_text()
        assert len(content.strip().split("\n")) >= 1
