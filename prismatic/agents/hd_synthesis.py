"""
Prismatic Engine — HD Synthesis Agent
======================================

Agent responsible for synthesizing raw Human Design data into narrative reports.
Uses Gemini Flash/Lite for synthesis and references the human-design-computation
portable skill for logic and templates.
"""
from __future__ import annotations

import logging
from typing import Any

from prismatic.providers.tasks.base import Issue
from .base import BaseAgent, AgentConfig, AGENT_TYPES
from prismatic.providers.hd_synthesis.gemini_client import create_gemini_client
from prismatic.providers.hd_synthesis.report_templates import (
    INDIVIDUAL_REPORT_TEMPLATE,
    RELATIONSHIP_REPORT_TEMPLATE,
    TRANSIT_REPORT_TEMPLATE
)

logger = logging.getLogger(__name__)

class HDSynthesisAgent(BaseAgent):
    """Agent that performs Human Design narrative synthesis."""

    def __init__(self, config: AgentConfig, agent_config: dict[str, Any] | None = None):
        super().__init__(config, agent_config)
        self._gemini = create_gemini_client(self._agent_config.get("gemini", {}))

    def execute(self, issue: Issue) -> bool:
        """Execute HD synthesis based on the issue metadata."""
        request_type = issue.metadata.get("request_type", "individual")
        params = issue.metadata.get("params", {})

        logger.info(f"Executing {request_type} synthesis for issue {issue.identifier}")

        try:
            if request_type == "individual":
                report = self._synthesize_individual(params)
            elif request_type == "relationship":
                report = self._synthesize_relationship(params)
            elif request_type == "transit":
                report = self._synthesize_transit(params)
            else:
                logger.error(f"Unknown request type: {request_type}")
                return False

            # Log the synthesized report
            logger.info(f"Successfully synthesized {request_type} report:\n{report}")

            # If a callback_url was provided, we'd send the report back
            callback_url = issue.metadata.get("callback_url")
            if callback_url:
                logger.info(f"Sending report to callback URL: {callback_url}")
                # Mock callback request

            return True

        except Exception as e:
            logger.exception(f"Failed to synthesize {request_type} report: {e}")
            return False

    def get_id(self) -> str:
        return "hd_synthesis"

    def _synthesize_individual(self, params: dict[str, Any]) -> str:
        # 1. Fetch raw HD data (normally via human-design-computation skill/engine)
        # 2. Format into prompt using the template
        prompt = INDIVIDUAL_REPORT_TEMPLATE.format(
            name=params.get("name", "User"),
            type_summary=params.get("type"),
            authority_summary=params.get("authority"),
            profile_summary=params.get("profile"),
            inner_self_narrative="Computed narrative for inner self...",
            outer_self_narrative="Computed narrative for outer self...",
            scenarios="Real-world scenarios based on data..."
        )
        # 3. Call Gemini
        return self._gemini.generate_report(
            prompt,
            system_instruction="You are an expert HD analyst. Synthesize the following data into a narrative report."
        )

    def _synthesize_relationship(self, params: dict[str, Any]) -> str:
        prompt = RELATIONSHIP_REPORT_TEMPLATE.format(
            person_a=params.get("person_a"),
            person_b=params.get("person_b"),
            dynamics_summary="Dynamics analysis...",
            person_a_perspective="A's view of B...",
            person_b_perspective="B's view of A...",
            synthesis_framework="Synergy and collaboration...",
            transit_forecast="Composite transit forecast..."
        )
        return self._gemini.generate_report(prompt)

    def _synthesize_transit(self, params: dict[str, Any]) -> str:
        prompt = TRANSIT_REPORT_TEMPLATE.format(
            name=params.get("name"),
            current_weather="Current transit conditions...",
            phantom_gates_summary="Phantom gate activations...",
            monthly_forecast="12-month outlook..."
        )
        return self._gemini.generate_report(prompt)

# Register the agent
AGENT_TYPES["hd_synthesis"] = HDSynthesisAgent

def create_hd_synthesis_agent(config: dict[str, Any]) -> HDSynthesisAgent:
    """Factory for HDSynthesisAgent."""
    agent_config = AgentConfig(
        executable="hd_synthesis",
        mode=config.get("mode", "signal"),
        timeout=config.get("timeout", 600),
        next_label=config.get("next_label"),
    )
    return HDSynthesisAgent(config=agent_config, agent_config=config)
