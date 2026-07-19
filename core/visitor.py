"""Anonymous visitor identity signing, validation and deterministic scoping."""

import hashlib
import hmac
from uuid import UUID, uuid4


VISITOR_COOKIE_NAME = "rag_visitor_id"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def new_visitor_id() -> str:
    """Generate one cryptographically secure UUIDv4 visitor identifier."""
    return str(uuid4())


def parse_visitor_id(value: object) -> str | None:
    """Accept only canonicalizable UUIDv4 values from the cookie boundary."""
    try:
        parsed = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None
    return str(parsed) if parsed.version == 4 else None


def sign_visitor_cookie(visitor_id: object, secret: str) -> str:
    """Sign one server-generated visitor UUID for use as an untrusted cookie."""
    owner = parse_visitor_id(visitor_id)
    if owner is None or not secret:
        raise ValueError("visitor cookie signing requires a UUIDv4 and secret")
    return f"{owner}.{_visitor_signature(owner, secret)}"


def verify_visitor_cookie(value: object, secret: str) -> str | None:
    """Return the visitor UUID only when the cookie signature is authentic."""
    raw = str(value or "")
    owner, separator, signature = raw.rpartition(".")
    parsed = parse_visitor_id(owner)
    if not separator or parsed is None or not secret:
        return None
    return parsed if hmac.compare_digest(signature, _visitor_signature(parsed, secret)) else None


def visitor_scope(value: object) -> str:
    """Return a stable safe scope label for external storage metadata."""
    parsed = parse_visitor_id(value)
    if parsed is None:
        raise ValueError("visitor_id must be a UUIDv4")
    return f"visitor_{parsed.replace('-', '')}"


def _visitor_signature(visitor_id: str, secret: str) -> str:
    """Derive a purpose-separated cookie signature."""
    return hmac.new(secret.encode("utf-8"), f"rag-visitor-v1:{visitor_id}".encode(), hashlib.sha256).hexdigest()
