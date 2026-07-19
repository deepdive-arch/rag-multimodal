"""FastAPI dependency helpers."""

import hmac

from fastapi import HTTPException, Request

from core.config import Settings, get_settings
from core.visitor import parse_visitor_id


def settings_dependency() -> Settings:
    """Return centralized settings."""
    return get_settings()


def visitor_id_for_request(request: Request) -> str:
    """Return the server-resolved visitor identity attached by middleware."""
    value = parse_visitor_id(getattr(request.state, "visitor_id", None))
    if value is None:
        raise HTTPException(status_code=500, detail="Sessão de visitante indisponível")
    return value


def require_admin(request: Request) -> None:
    """Require the configured admin token for destructive actions."""
    settings = get_settings()
    token = request.headers.get("X-Admin-Token", "")
    if not settings.admin_token or not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=403, detail="Token administrativo inválido")
