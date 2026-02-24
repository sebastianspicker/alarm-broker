"""Logging configuration for the alarm broker.

This module provides structured logging configuration with support for:
- JSON formatted logs for production
- Human-readable logs for development
- Correlation ID propagation
- Request context enrichment
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter.

    Outputs log records as JSON objects suitable for log aggregation systems.
    """

    def __init__(self, *, include_timestamp: bool = True, include_level: bool = True) -> None:
        """Initialize the formatter.

        Args:
            include_timestamp: Whether to include timestamp in output
            include_level: Whether to include log level in output
        """
        super().__init__()
        self.include_timestamp = include_timestamp
        self.include_level = include_level

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON.

        Args:
            record: The log record to format

        Returns:
            JSON formatted log string
        """
        log_data: dict[str, Any] = {
            "message": record.getMessage(),
        }

        if self.include_timestamp:
            log_data["timestamp"] = datetime.now(UTC).isoformat()

        if self.include_level:
            log_data["level"] = record.levelname

        # Add logger name
        log_data["logger"] = record.name

        # Add extra fields
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id

        if hasattr(record, "alarm_id"):
            log_data["alarm_id"] = record.alarm_id

        if hasattr(record, "extra"):
            extra = record.extra
            if isinstance(extra, dict):
                log_data.update(extra)

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add location info
        log_data["location"] = f"{record.filename}:{record.lineno}"

        return json.dumps(log_data)


class HumanReadableFormatter(logging.Formatter):
    """Human-readable log formatter for development.

    Uses colors and indentation for easier reading in terminals.
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record for human reading.

        Args:
            record: The log record to format

        Returns:
            Human readable log string
        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        color = self.COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:8}{self.RESET}"

        # Base message
        parts = [f"[{timestamp}] {level} {record.name}: {record.getMessage()}"]

        # Add extra fields
        extra_parts = []
        if hasattr(record, "request_id"):
            extra_parts.append(f"request_id={record.request_id}")
        if hasattr(record, "alarm_id"):
            extra_parts.append(f"alarm_id={record.alarm_id}")
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            for key, value in record.extra.items():
                extra_parts.append(f"{key}={value}")

        if extra_parts:
            parts.append("  " + " ".join(extra_parts))

        # Add exception if present
        if record.exc_info:
            parts.append(self.formatException(record.exc_info))

        return "\n".join(parts)


def configure_logging(
    level: str = "INFO",
    json_format: bool = False,
    loggers: list[str] | None = None,
) -> None:
    """Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Use JSON structured logging
        loggers: Additional loggers to configure
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    # Select formatter
    if json_format:
        formatter = StructuredFormatter()
    else:
        formatter = HumanReadableFormatter()

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Configure specific loggers
    loggers_to_configure = ["alarm_broker", "uvicorn", "sqlalchemy"]
    if loggers:
        loggers_to_configure.extend(loggers)

    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        # Don't propagate to root to avoid duplicate logs
        logger.propagate = False
        logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
