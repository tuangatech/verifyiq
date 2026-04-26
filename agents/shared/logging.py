# agents/shared/logging.py
"""Shared structlog configuration for all VerifyIQ services."""

import logging

import structlog


def configure_logging(agent_name: str) -> None:
    """Configure structlog with JSON output and bound agent name.

    Call once at module level, before the FastAPI app is created.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    structlog.contextvars.bind_contextvars(agent=agent_name)
