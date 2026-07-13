from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from prismatic.interface.plugin import PluginContext, PrismaticPlugin
from prismatic.gateway.event_bus import get_event_bus, SwarmEvent

logger = logging.getLogger("prismatic.plugin.linear_bot")

class LinearBotPlugin(PrismaticPlugin):
    """
    Linear Bot Plugin: subscribes to 'pwp.pipeline.failed' event bus topic,
    and publishes 'linear.issue.created' with payload details.
    """

    def on_init(self, context: PluginContext) -> None:
        """Called when the core dispatcher initializes the plugin."""
        logger.info("Initializing LinearBotPlugin")
        # Subscribe to event bus
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(get_event_bus().subscribe(self.handle_event))
            logger.info("LinearBotPlugin subscribed to event bus via running event loop")
        except RuntimeError:
            # No running event loop (e.g. during test setup or CLI invocation)
            get_event_bus()._handlers.add(self.handle_event)
            logger.info("LinearBotPlugin subscribed to event bus directly")

    def register_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions (none needed for Linear Bot)."""
        return []

    async def handle_event(self, event: SwarmEvent) -> None:
        """Handle incoming swarm events."""
        if event.type == "pwp.pipeline.failed":
            logger.info("LinearBotPlugin received event pwp.pipeline.failed")
            # Build payload for the new Linear issue
            error_message = event.payload.get("error", "Unknown pipeline error")
            pipeline_id = event.payload.get("pipeline_id", "unknown-pipeline")
            
            payload = {
                "title": f"PWP Pipeline Failure: {pipeline_id}",
                "description": f"Pipeline failure detected.\nError details:\n{error_message}",
                "pipeline_id": pipeline_id,
                "error": error_message,
                "severity": "high",
                "source_event": {
                    "type": event.type,
                    "source": event.source,
                    "timestamp": event.timestamp,
                    "payload": event.payload
                }
            }

            logger.info("LinearBotPlugin publishing linear.issue.created")
            await get_event_bus().publish(
                event_type="linear.issue.created",
                source="linear-bot",
                payload=payload
            )
