"""Structured logging configuration using structlog.

Provides JSON formatting for both structlog and standard library logging.
"""

from __future__ import annotations

import builtins
import logging
import sys
import structlog
from typing import Any

# Global flag to track if logging has been initialized
_initialized = False
_original_print = builtins.print
_original_stdout = sys.stdout
_original_stderr = sys.stderr


def init_logging(level: int | str = logging.INFO, intercept_print: bool = True) -> None:
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

    if intercept_print:
        _setup_print_interception()


def _setup_print_interception() -> None:
    """Redirect standard prints to the structlog 'print' logger."""
    import threading
    _local = threading.local()
    logger = structlog.get_logger("print")

    def intercepted_print(*args: Any, **kwargs: Any) -> None:
        if getattr(_local, "in_print", False):
            _original_print(*args, **kwargs)
            return

        file = kwargs.get("file", sys.stdout)
        
        is_stdout = (file is sys.stdout or (hasattr(file, "name") and file.name == "<stdout>"))
        is_stderr = (file is sys.stderr or (hasattr(file, "name") and file.name == "<stderr>"))

        # Fall back to original print for custom targets or if standard streams are redirected
        if not is_stdout and not is_stderr:
            _original_print(*args, **kwargs)
            return

        if (is_stdout and sys.stdout is not _original_stdout) or \
           (is_stderr and sys.stderr is not _original_stderr):
            _original_print(*args, **kwargs)
            return

        sep = kwargs.get("sep", " ")
        message = sep.join(str(arg) for arg in args)

        _local.in_print = True
        try:
            if file is sys.stderr or (hasattr(file, "name") and file.name == "<stderr>"):
                logger.error(message)
            else:
                logger.info(message)
        except Exception:
            _original_print(*args, **kwargs)
        finally:
            _local.in_print = False

    builtins.print = intercepted_print


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    # Ensure logging is initialized if not done already
    if not _initialized:
        init_logging()
    return structlog.get_logger(name)
