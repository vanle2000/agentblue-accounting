"""Regression tests for structlog integration in health endpoints."""

from __future__ import annotations

import pytest
import structlog

pytestmark = pytest.mark.unit


async def test_structlog_error_with_extra_kwargs() -> None:
    """Regression: structlog logger.error must accept keyword arguments
    without colliding with the positional event parameter.

    Prior to the fix, calling:
        logger.error("event_name", event="value")
    raised TypeError because 'event' was passed as both a positional
    and keyword argument.
    """
    logger = structlog.get_logger("test")

    # This must not raise TypeError.
    logger.error("test_event", reason="test_reason", key="value")
