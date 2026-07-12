"""Structured logging configuration using structlog.

Provides JSON formatting for both structlog and standard library logging.
"""

from __future__ import annotations

import logging
import sys
import structlog
from typing import Any

# Global flag to track if logging has been initialized
_initialized = False


def init_logging(level: int | str = logging.INFO) -> None:
    """Initialize structured logging (structlog) with JSON output.

    Clears any existing root handlers and sets up a standard output handler
    formatted with structlog's JSONRenderer.
    """
    global _initialized
    if _initialized:
        return

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Clear existing handlers to avoid duplicate output
    root_logger.handlers = []
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    _initialized = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    # Ensure logging is initialized if not done already
    if not _initialized:
        init_logging()
    return structlog.get_logger(name)
