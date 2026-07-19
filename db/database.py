"""Async SQLAlchemy engine and session lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import re
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from core.config import Settings


def _asyncpg_url(database_url: str) -> URL:
    """Normalize a Postgres URL for SQLAlchemy's asyncpg dialect."""
    normalized = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    parsed = make_url(normalized)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+asyncpg")
    if parsed.drivername != "postgresql+asyncpg":
        raise ValueError("DATABASE_URL must use PostgreSQL with asyncpg")
    return parsed


def _connection_options(url: URL) -> tuple[URL, dict[str, object]]:
    """Move SSL URL options into asyncpg connection arguments."""
    query = dict(url.query)
    ssl = query.pop("ssl", query.pop("sslmode", None))
    search_path = query.pop("search_path", None)
    if search_path and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", search_path):
        raise ValueError("search_path must be one lowercase PostgreSQL identifier")
    options: dict[str, object] = {"ssl": ssl} if ssl else {}
    if search_path:
        options["server_settings"] = {"search_path": search_path}
    return url.set(query=query), options


class Database:
    """Owned async engine and session factory."""

    def __init__(self, settings: Settings | str) -> None:
        url, options = _connection_options(_asyncpg_url(settings if isinstance(settings, str) else settings.database_url))
        pool_size = 5 if isinstance(settings, str) else settings.database_pool_size
        max_overflow = 10 if isinstance(settings, str) else settings.database_max_overflow
        pool_timeout = 30 if isinstance(settings, str) else settings.database_pool_timeout_seconds
        if not isinstance(settings, str):
            options["timeout"] = settings.database_connect_timeout_seconds
        self.engine: AsyncEngine = create_async_engine(url, connect_args=options, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield one session for a caller-managed transaction."""
        async with self.session_factory() as session:
            yield session

    async def check(self, timeout_seconds: float = 2.0) -> None:
        """Verify the database with a bounded SELECT 1."""
        await asyncio.wait_for(self._select_one(), timeout=timeout_seconds)

    async def _select_one(self) -> None:
        """Run the health query."""
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        """Release all pooled connections."""
        await self.engine.dispose()


def database_from_settings(settings: Settings) -> Database:
    """Create a database from validated settings."""
    if not settings.database_url.strip():
        raise ValueError("DATABASE_URL is required for the Postgres catalog")
    return Database(settings)
