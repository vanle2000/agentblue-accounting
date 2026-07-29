"""Structured logging configuration with redaction.

Provides JSON-structured logs for production-shadow environments
with automatic redaction of sensitive fields.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

# Sensitive field names that must be redacted
_SENSITIVE_FIELDS = frozenset({
    "password",
    "secret",
    "token",
    "authorization",
    "access_token",
    "refresh_token",
    "api_key",
    "client_secret",
    "db_password",
    "jwt_secret_key",
    "jwt",
    "bearer",
    "credit_card",
    "ssn",
    "bank_account",
    "account_number",
})

# Patterns for sensitive values
_SENSITIVE_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"postgresql(\+asyncpg)?://[^\s]+", re.IGNORECASE),
    re.compile(r"ey[A-Za-z0-9\-._~+/]+=*\.[A-Za-z0-9\-._~+/]+=*\.?[A-Za-z0-9\-._~+/]*"),
]

_REDACTED = "[REDACTED]"


def redact_value(key: str, value: Any) -> Any:
    """Redact a value if the key or value pattern is sensitive.

    Args:
        key: The field name.
        value: The field value.

    Returns:
        The value or [REDACTED] if sensitive.
    """
    if isinstance(value, str):
        # Check field name
        if key.lower() in _SENSITIVE_FIELDS:
            return _REDACTED
        # Check value patterns
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(value):
                return _REDACTED
    return value


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive fields in a dictionary.

    Args:
        data: The dictionary to redact.

    Returns:
        A new dictionary with sensitive values redacted.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = redact_dict(value)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(v) if isinstance(v, dict) else redact_value(key, v)
                for v in value
            ]
        else:
            result[key] = redact_value(key, value)
    return result


def redact_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that redacts sensitive fields.

    Args:
        logger: The structlog logger.
        method_name: The method name.
        event_dict: The event dictionary.

    Returns:
        The event dictionary with sensitive values redacted.
    """
    return redact_dict(event_dict)


def configure_structured_logging(
    *,
    level: str = "INFO",
    environment: str = "development",
    json_format: bool = False,
) -> None:
    """Configure structlog for the given environment.

    Args:
        level: Log level.
        environment: Environment name.
        json_format: Whether to use JSON format (production modes).
    """
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        redact_processor,
    ]

    if json_format:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
