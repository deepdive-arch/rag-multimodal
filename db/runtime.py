"""Process-level database ownership for the FastAPI lifespan."""

from __future__ import annotations

from core.config import Settings, get_settings
from db.database import Database, database_from_settings


_database: Database | None = None


def configure_database(settings: Settings) -> Database:
    """Create and register the process database."""
    global _database
    if _database is None:
        _database = database_from_settings(settings)
    return _database


def get_database(settings: Settings | None = None) -> Database:
    """Return the registered database, creating a lazy runtime instance when needed."""
    global _database
    if _database is None:
        _database = database_from_settings(settings or get_settings())
    return _database


async def close_database() -> None:
    """Dispose the registered database during application shutdown."""
    global _database
    database, _database = _database, None
    if database is not None:
        await database.close()
