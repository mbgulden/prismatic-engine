"""
Prismatic Engine — HD Synthesis Backend Agent
==============================================

An isolated worker agent for the Human Design platform that:

1. Accepts birth data via webhook or direct API call
2. Routes to Gemini Flash/Lite on Vertex AI
3. Synthesizes plain-English narrative reports
4. Follows the BaseAgent interface pattern

Usage:
    agent = HDSynthesisAgent(config)
    agent.execute(issue)  # where issue contains HD birth data
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prismatic.providers.tasks.base import Issue
from prismatic.providers.hd_synthesis.gemini_client import GeminiClient, create_gemini_client
from prismatic.providers.hd_synthesis.report_templates import (
    INDIVIDUAL_REPORT_TEMPLATE,
    RELATIONSHIP_REPORT_TEMPLATE,
    TRANSIT_REPORT_TEMPLATE,
)
from .base import BaseAgent, AgentConfig


# ── Agent type registration ───────────────────────────────────
from .base import AGENT_TYPES


@dataclass
class SynthesisRequest:
    """Structured request for HD synthesis."""

    report_type: str  # "individual" | "relationship" | "transit"
    birth_data: dict[str, Any]
    secondary_birth_data: dict[str, Any] | None = None  # for relationship reports
    transit_date: str | None = None  # for transit reports
    name: str = "Client"
    language: str = "en"
    tone: str = "conversational"  # conversational, professional, concise
    options: dict[str, Any] = field(default_factory=dict)


class HDSynthesisAgent(BaseAgent):
    """Agent that synthesizes Human Design narrative reports.

    Accepts birth data, routes to Gemini models via Vertex AI,
    and produces structured plain-English reports covering:

    - Individual deep dives (type, authority, profile, channels, centers)
    - Relationship compatibility reports (two-chart overlay)
    - Transit briefings (current transit effects on natal chart)

    The agent intentionally avoids re-computing charts — it delegates
    chart calculation to the existing HD engine (OpenHumanDesignMCP)
    and focuses purely on narrative synthesis from raw chart data.
    """

    # ── Supported report types ────────────────────────────────
    REPORT_TYPES = {"individual", "relationship", "transit"}

    def __init__(
        self,
        config: AgentConfig,
        agent_config: dict[str, Any] | None = None,
        gemini_client: GeminiClient | None = None,
    ):
        super().__init__(config, agent_config)
        merged = {**(agent_config or {}), **config.options}
        self._gemini_client = gemini_client or create_gemini_client({
            "project_id": merged.get("project_id", os.environ.get("GCP_PROJECT_ID")),
            "location": merged.get("location", "us-central1"),
            "model_name": merged.get("model_name", "gemini-1.5-flash-001"),
        })
        self._fallback_model = merged.get("fallback_model", "gemini-1.5-flash-002")
        self._max_retries = int(merged.get("max_retries", 2))

    def get_id(self) -> str:
        return "hd_synthesis"

    def execute(self, issue: Issue) -> bool:
        """Execute HD synthesis from an issue.

        The issue's description should contain JSON-encoded
        ``SynthesisRequest`` data.  Returns True on success.
        """
        request = self._parse_request(issue)
        if request is None:
            print(f"[HDSynthesisAgent] Could not parse request from {issue.identifier}")
            return False

        try:
            report = self._synthesize(request)
            self._write_output(issue, report)
            return True
        except Exception as exc:
            print(f"[HDSynthesisAgent] Synthesis failed for {issue.identifier}: {exc}")
            return False

    # ── Request parsing ───────────────────────────────────────

    @staticmethod
    def _parse_request(issue: Issue) -> SynthesisRequest | None:
        """Extract SynthesisRequest from the issue description or metadata."""
        if not issue.description:
            return None

        try:
            data = json.loads(issue.description)
            return SynthesisRequest(
                report_type=data.get("report_type", "individual"),
                birth_data=data.get("birth_data", {}),
                secondary_birth_data=data.get("secondary_birth_data"),
                transit_date=data.get("transit_date"),
                name=data.get("name", "Client"),
                language=data.get("language", "en"),
                tone=data.get("tone", "conversational"),
                options=data.get("options", {}),
            )
        except (json.JSONDecodeError, TypeError):
            # Description isn't JSON — treat as a prompt
            return SynthesisRequest(
                report_type="individual",
                birth_data={"prompt": issue.description},
                name=issue.title,
            )

    # ── Synthesis pipeline ────────────────────────────────────

    def _synthesize(self, request: SynthesisRequest) -> str:
        """Run the full synthesis pipeline for a given request.

        1. Validate the report type
        2. Build the prompt from birth data and templates
        3. Send to Gemini (primary model, with fallback)
        4. Return the narrative report
        """
        if request.report_type not in self.REPORT_TYPES:
            raise ValueError(
                f"Unsupported report type '{request.report_type}'. "
                f"Supported: {', '.join(sorted(self.REPORT_TYPES))}"
            )

        # Build the full prompt
        prompt = self._build_prompt(request)

        # System instruction
        system_instruction = self._build_system_instruction(request)

        # Try primary model first, fall back on failure
        report = self._generate_with_fallback(prompt, system_instruction)

        return report

    def _build_prompt(self, request: SynthesisRequest) -> str:
        """Build a structured prompt from birth data."""
        data = request.birth_data
        name = request.name
        report_type = request.report_type

        prompt_parts = [
            f"Generate a {report_type} Human Design report for {name}.",
            "",
            f"Language: {request.language}",
            f"Tone: {request.tone}",
            "",
            "--- Birth Data ---",
            json.dumps(data, indent=2),
        ]

        if request.secondary_birth_data:
            prompt_parts.extend([
                "",
                "--- Secondary Birth Data (Relationship Partner) ---",
                json.dumps(request.secondary_birth_data, indent=2),
            ])

        if request.transit_date:
            prompt_parts.extend([
                "",
                f"--- Transit Date: {request.transit_date} ---",
            ])

        return "\n".join(prompt_parts)

    @staticmethod
    def _build_system_instruction(request: SynthesisRequest) -> str:
        """Build the system-level instruction for the LLM."""
        base = (
            "You are a Human Design synthesis expert. You produce plain-English, "
            "jargon-free narrative reports that make Human Design accessible to "
            "everyone. Avoid astrological jargon unless you explain it in context. "
        )

        if request.tone == "conversational":
            base += (
                "Write in a warm, conversational tone as if explaining to a friend. "
                "Use metaphors and real-world examples. Keep paragraphs short."
            )
        elif request.tone == "professional":
            base += (
                "Write in a professional, coaching-style tone. Be precise but "
                "accessible. Structure the report with clear sections."
            )
        elif request.tone == "concise":
            base += (
                "Be direct and concise. Use bullet points where appropriate. "
                "Focus on actionable insights."
            )

        return base

    def _generate_with_fallback(self, prompt: str, system_instruction: str) -> str:
        """Generate report with primary model, retry on fallback if needed."""
        last_error = None

        for attempt in range(self._max_retries + 1):
            model = self._gemini_client.model_name if attempt == 0 else self._fallback_model
            try:
                client = self._gemini_client
                if model != client.model_name:
                    client = GeminiClient(
                        project_id=client.project_id,
                        location=client.location,
                        model_name=model,
                    )
                result = client.generate_report(prompt, system_instruction)
                if result and len(result) > 20:
                    return result
                last_error = f"Empty or too-short response ({len(result) if result else 0} chars)"
            except Exception as exc:
                last_error = str(exc)

            if attempt < self._max_retries:
                print(f"[HDSynthesisAgent] Retry {attempt + 1}/{self._max_retries} "
                      f"with fallback model {model}")

        raise RuntimeError(
            f"Synthesis failed after {self._max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )

    # ── Output handling ───────────────────────────────────────

    def _write_output(self, issue: Issue, report: str) -> None:
        """Write the synthesized report to disk."""
        base = os.environ.get("PRISMATIC_HOME")
        if not base:
            base = str(Path.home())
        output_dir = Path(base) / "work" / "hd-synthesis" / "output"

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{issue.identifier}_{issue.title.replace(' ', '_')[:40]}.md"

        output_path.write_text(report, encoding="utf-8")
        print(f"[HDSynthesisAgent] Report written to {output_path}")

    # ── Webhook-compatible entry point ────────────────────────

    def synthesize_from_data(self, request: SynthesisRequest) -> str:
        """Direct synthesis entry point, compatible with webhook flow.

        This method is the bridge between the webhook handler
        and the agent's core synthesis pipeline.  It bypasses
        the Issue object for direct data flow.
        """
        return self._synthesize(request)


# ── Factory ────────────────────────────────────────────────────

def create_hd_synthesis_agent(config: dict[str, Any]) -> HDSynthesisAgent:
    """Shorthand factory for HDSynthesisAgent."""
    agent_config = AgentConfig(
        executable="hd_synthesis",
        mode=config.get("mode", "direct"),
        timeout=config.get("timeout", 300),
        next_label=config.get("next_label", "agent:done"),
    )
    return HDSynthesisAgent(config=agent_config, agent_config=config)


# Register with the agent type registry
AGENT_TYPES["hd_synthesis"] = HDSynthesisAgent
