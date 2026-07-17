"""FastAPI dependency helpers."""

from fastapi import HTTPException, Request

from core.config import Settings, get_settings


def settings_dependency() -> Settings:
    """Return centralized settings."""
    return get_settings()


def require_admin(request: Request) -> None:
    """Require the configured admin token for destructive actions."""
    settings = get_settings()
    if settings.admin_token and request.headers.get("X-Admin-Token") != settings.admin_token:
        raise HTTPException(status_code=403, detail="Token administrativo inválido")
