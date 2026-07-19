"""Privacy-preserving public-demo identity and quota helpers."""

from datetime import UTC, date, datetime, timedelta
import hashlib
import hmac
from typing import Any

from core.config import Settings
from core.visitor import parse_visitor_id


def utc_usage_date(now: datetime | None = None) -> date:
    """Return the quota period in UTC, never in the host locale."""
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def hash_client_address(address: str, secret: str) -> str:
    """Derive a non-reversible per-secret client identifier."""
    return hmac.new(secret.encode("utf-8"), address.encode("utf-8"), hashlib.sha256).hexdigest()


def client_hash_for_request(request: Any, settings: Settings, visitor_id: str | None = None) -> str:
    """Hash only the backend-resolved visitor for persistent quotas."""
    visitor = parse_visitor_id(visitor_id or getattr(getattr(request, "state", None), "visitor_id", None))
    if not visitor:
        raise ValueError("visitor_id must be resolved before quota identity is calculated")
    return hash_client_address(visitor, settings.rate_limit_secret)


def retry_after_until_next_utc_day(now: datetime | None = None) -> int:
    """Return a safe Retry-After duration for a daily quota response."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    tomorrow = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return max(1, int((tomorrow - current).total_seconds()))
