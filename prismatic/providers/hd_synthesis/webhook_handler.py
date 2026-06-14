"""
HD Synthesis Backend — Webhook Handler
=======================================

FastAPI-style webhook receiver for the HD Synthesis Agent.
Accepts birth data via POST, returns synthesized reports.

Endpoints:

- POST /api/v1/synthesize/individual — Individual deep dive
- POST /api/v1/synthesize/relationship — Relationship compatibility
- POST /api/v1/synthesize/transit — Transit briefing

Each endpoint validates the payload, delegates to the HDSynthesisAgent,
and returns the report as structured JSON.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from prismatic.agents.hd_synthesis import HDSynthesisAgent, SynthesisRequest

logger = logging.getLogger(__name__)


# ── Webhook request/response models ──────────────────────────

@dataclass
class WebhookRequest:
    """Incoming webhook payload from the frontend."""

    report_type: str
    name: str = "Client"
    birth_date: str = ""
    birth_time: str = ""
    birth_location: str = ""
    timezone: str = "UTC"
    secondary_name: str | None = None
    secondary_birth_date: str | None = None
    secondary_birth_time: str | None = None
    secondary_birth_location: str | None = None
    secondary_timezone: str | None = None
    transit_date: str | None = None
    language: str = "en"
    tone: str = "conversational"
    raw_chart_data: dict[str, Any] | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookResponse:
    """Response returned after synthesis."""

    success: bool
    report_type: str
    name: str
    report: str | None = None
    error: str | None = None
    generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    request_id: str | None = None


# ── Validators ────────────────────────────────────────────────

def validate_individual_request(req: WebhookRequest) -> list[str]:
    """Validate fields for an individual report request."""
    errors = []
    if not req.name:
        errors.append("'name' is required")
    if not req.birth_date and not req.raw_chart_data:
        errors.append("Either 'birth_date' or 'raw_chart_data' is required")
    return errors


def validate_relationship_request(req: WebhookRequest) -> list[str]:
    """Validate fields for a relationship report request."""
    errors = validate_individual_request(req)
    if not req.secondary_name:
        errors.append("'secondary_name' is required for relationship reports")
    if not req.secondary_birth_date and not req.raw_chart_data:
        errors.append("Either 'secondary_birth_date' or 'raw_chart_data' is required")
    return errors


def validate_transit_request(req: WebhookRequest) -> list[str]:
    """Validate fields for a transit briefing request."""
    errors = validate_individual_request(req)
    if not req.transit_date:
        errors.append("'transit_date' is required for transit reports")
    return errors


# ── Request builders ────────────────────────────────────────

VALIDATOR_MAP: dict[str, Callable[[WebhookRequest], list[str]]] = {
    "individual": validate_individual_request,
    "relationship": validate_relationship_request,
    "transit": validate_transit_request,
}


def build_birth_data(req: WebhookRequest) -> dict[str, Any]:
    """Build structured birth data from a webhook request."""
    if req.raw_chart_data:
        return req.raw_chart_data

    data = {
        "date": req.birth_date,
        "time": req.birth_time,
        "location": req.birth_location,
        "timezone": req.timezone,
    }
    return {k: v for k, v in data.items() if v}


def build_synthesis_request(req: WebhookRequest) -> SynthesisRequest | None:
    """Convert a webhook request into a SynthesisRequest.

    Returns None if validation fails.
    """
    validator = VALIDATOR_MAP.get(req.report_type, validate_individual_request)
    errors = validator(req)
    if errors:
        logger.warning(f"Validation errors for {req.report_type}: {errors}")
        return None

    birth_data = build_birth_data(req)
    secondary_birth_data = None

    if req.report_type == "relationship":
        secondary_birth_data = build_birth_data(
            WebhookRequest(
                report_type="relationship",
                name=req.secondary_name or "",
                birth_date=req.secondary_birth_date or "",
                birth_time=req.secondary_birth_time or "",
                birth_location=req.secondary_birth_location or "",
                timezone=req.secondary_timezone or req.timezone,
                raw_chart_data=req.raw_chart_data,
            )
        )

    return SynthesisRequest(
        report_type=req.report_type,
        birth_data=birth_data,
        secondary_birth_data=secondary_birth_data,
        transit_date=req.transit_date,
        name=req.name,
        language=req.language,
        tone=req.tone,
        options=req.options,
    )


# ── Webhook handler class ─────────────────────────────────────

class WebhookHandler:
    """Handles incoming webhook requests for HD synthesis.

    This class provides the integration layer between the frontend
    (FastAPI endpoints, serverless functions) and the HDSynthesisAgent.
    """

    def __init__(self, agent: HDSynthesisAgent | None = None):
        self._agent = agent

    @property
    def agent(self) -> HDSynthesisAgent:
        if self._agent is None:
            from prismatic.agents.hd_synthesis import create_hd_synthesis_agent
            self._agent = create_hd_synthesis_agent({})
        return self._agent

    def handle_synthesis_request(
        self,
        payload: dict[str, Any] | WebhookRequest,
        request_id: str | None = None,
    ) -> WebhookResponse:
        """Process an incoming synthesis request.

        Args:
            payload: Raw dict from the HTTP POST body, or a WebhookRequest.
            request_id: Optional request ID for traceability.

        Returns:
            WebhookResponse with the synthesized report or error details.
        """
        req = payload if isinstance(payload, WebhookRequest) else self._dict_to_request(payload)
        synth_request = build_synthesis_request(req)

        if synth_request is None:
            errors = self._validate_raw(req)
            return WebhookResponse(
                success=False,
                report_type=req.report_type,
                name=req.name,
                error=f"Validation failed: {'; '.join(errors)}",
                request_id=request_id,
            )

        try:
            report = self.agent.synthesize_from_data(synth_request)
            return WebhookResponse(
                success=True,
                report_type=synth_request.report_type,
                name=synth_request.name,
                report=report,
                request_id=request_id,
            )
        except Exception as exc:
            logger.exception("Synthesis failed")
            return WebhookResponse(
                success=False,
                report_type=synth_request.report_type,
                name=synth_request.name,
                error=str(exc),
                request_id=request_id,
            )

    # ── FastAPI route handlers ───────────────────────────────

    async def individual_deep_dive(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/synthesize/individual"""
        result = self.handle_synthesis_request(
            {**payload, "report_type": "individual"},
            request_id=payload.get("request_id"),
        )
        return self._response_to_dict(result)

    async def relationship_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/synthesize/relationship"""
        result = self.handle_synthesis_request(
            {**payload, "report_type": "relationship"},
            request_id=payload.get("request_id"),
        )
        return self._response_to_dict(result)

    async def transit_briefing(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/synthesize/transit"""
        result = self.handle_synthesis_request(
            {**payload, "report_type": "transit"},
            request_id=payload.get("request_id"),
        )
        return self._response_to_dict(result)

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _dict_to_request(d: dict[str, Any]) -> WebhookRequest:
        """Convert a raw dict to a WebhookRequest."""
        return WebhookRequest(
            report_type=d.get("report_type", "individual"),
            name=d.get("name", "Client"),
            birth_date=d.get("birth_date", ""),
            birth_time=d.get("birth_time", ""),
            birth_location=d.get("birth_location", ""),
            timezone=d.get("timezone", "UTC"),
            secondary_name=d.get("secondary_name"),
            secondary_birth_date=d.get("secondary_birth_date"),
            secondary_birth_time=d.get("secondary_birth_time"),
            secondary_birth_location=d.get("secondary_birth_location"),
            secondary_timezone=d.get("secondary_timezone"),
            transit_date=d.get("transit_date"),
            language=d.get("language", "en"),
            tone=d.get("tone", "conversational"),
            raw_chart_data=d.get("raw_chart_data"),
            options=d.get("options", {}),
        )

    @staticmethod
    def _validate_raw(req: WebhookRequest) -> list[str]:
        """Re-validate and return errors."""
        validator = VALIDATOR_MAP.get(req.report_type, validate_individual_request)
        return validator(req)

    @staticmethod
    def _response_to_dict(resp: WebhookResponse) -> dict[str, Any]:
        """Convert a WebhookResponse to a plain dict for JSON serialization."""
        return {
            "success": resp.success,
            "report_type": resp.report_type,
            "name": resp.name,
            "report": resp.report,
            "error": resp.error,
            "generated_at": resp.generated_at,
            "request_id": resp.request_id,
        }


# ── Convenience factory ──────────────────────────────────────

def create_webhook_handler(agent_config: dict[str, Any] | None = None) -> WebhookHandler:
    """Create a WebhookHandler with an optional agent config."""
    if agent_config:
        from prismatic.agents.hd_synthesis import create_hd_synthesis_agent
        agent = create_hd_synthesis_agent(agent_config)
        return WebhookHandler(agent=agent)
    return WebhookHandler()
