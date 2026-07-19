"""Async Alembic environment for the Postgres catalog."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from db.database import _asyncpg_url, _connection_options
from db.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _database_url() -> str:
    """Read the same DATABASE_URL used by the application."""
    url = os.environ.get("DATABASE_URL", "").strip() or str(dotenv_values(".env").get("DATABASE_URL", "")).strip() or config.get_main_option("sqlalchemy.url").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required to run Alembic")
    return url


def run_migrations_offline() -> None:
    """Render migration SQL without opening a connection."""
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    """Configure Alembic on an already-open synchronous bridge."""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an asyncpg connection and run synchronous Alembic operations."""
    url, options = _connection_options(_asyncpg_url(_database_url()))
    connectable = create_async_engine(url, connect_args=options, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against Postgres."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
