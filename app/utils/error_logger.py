"""
Error Logger - Scraper metrics and event logging utilities.

For error logging, use logger.error() or logger.exception() directly with
structured extra fields. This module provides scraper-specific utilities.
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.core.logging import get_logger

SCRAPER_METRICS: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


# Scraper event logging


def log_scraper_event(
    *,
    service: str,
    event: str,
    level: int = logging.INFO,
    metric: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured scraper event log and optionally increment metrics.

    Args:
        service: Name of the scraper service.
        event: Event type/name.
        level: Log level (default INFO).
        metric: Metric name to increment (optional).
        **fields: Additional fields to include in the log.
    """
    logger = get_logger(f"scraper.{service}")

    payload = {
        "timestamp": datetime.now().isoformat(),
        "service": service,
        "event": event,
    }
    payload.update({k: v for k, v in fields.items() if v is not None})

    logger.log(
        level,
        "Scraper event %s",
        event,
        extra={
            "component": "scraper",
            "operation": event,
            "context_data": payload,
        },
    )

    if metric:
        SCRAPER_METRICS[service][metric] += 1


def get_scraper_metrics() -> dict[str, dict[str, int]]:
    """Return current scraper metric counters (primarily for tests).

    Returns:
        Dictionary mapping service names to metric dictionaries.
    """
    return {service: dict(metrics) for service, metrics in SCRAPER_METRICS.items()}


def reset_scraper_metrics() -> None:
    """Clear scraper metrics. Useful in tests to avoid cross pollution."""
    SCRAPER_METRICS.clear()
