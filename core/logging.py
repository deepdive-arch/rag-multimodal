"""Small structured logging helpers that avoid sensitive values."""

import logging
from typing import Any

from core.config import get_settings


def configure_logging() -> None:
    """Configure the application logger once."""
    logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO), format="%(message)s")


def log_event(logger: logging.Logger, operation: str, **fields: Any) -> None:
    """Log allow-listed operational fields without document contents or secrets."""
    safe = {key: value for key, value in fields.items() if key not in {"content", "history", "vector", "path", "token", "api_key"}}
    logger.info("%s %s", operation, safe)
