"""
HD Synthesis Backend — Provider Package
========================================

Provides the Gemini client, report templates, and webhook handler
for the Human Design Synthesis Agent in Prismatic Engine.

Usage:
    from prismatic.providers.hd_synthesis import (
        GeminiClient, create_gemini_client,
        WebhookHandler, create_webhook_handler,
        WebhookRequest, WebhookResponse,
        INDIVIDUAL_REPORT_TEMPLATE, RELATIONSHIP_REPORT_TEMPLATE,
        TRANSIT_REPORT_TEMPLATE,
    )
"""

from prismatic.providers.hd_synthesis.gemini_client import GeminiClient, create_gemini_client
from prismatic.providers.hd_synthesis.webhook_handler import (
    WebhookHandler,
    WebhookRequest,
    WebhookResponse,
    create_webhook_handler,
)
from prismatic.providers.hd_synthesis.report_templates import (
    INDIVIDUAL_REPORT_TEMPLATE,
    RELATIONSHIP_REPORT_TEMPLATE,
    TRANSIT_REPORT_TEMPLATE,
)

__all__ = [
    "GeminiClient",
    "create_gemini_client",
    "WebhookHandler",
    "WebhookRequest",
    "WebhookResponse",
    "create_webhook_handler",
    "INDIVIDUAL_REPORT_TEMPLATE",
    "RELATIONSHIP_REPORT_TEMPLATE",
    "TRANSIT_REPORT_TEMPLATE",
]
