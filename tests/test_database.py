import os

import pytest
from sqlalchemy import text

from db.database import _asyncpg_url, _connection_options
from db.database import Database


def test_connection_string_normalizes_driver_and_ssl():
    url, options = _connection_options(_asyncpg_url("postgres://user:password@host:5432/db?sslmode=require"))
    assert url.drivername == "postgresql+asyncpg"
    assert url.query == {}
    assert options == {"ssl": "require"}


def test_connection_string_moves_safe_search_path_to_server_settings():
    url, options = _connection_options(
        _asyncpg_url("postgres://user:password@host:5432/db?sslmode=require&search_path=codex_test_123")
    )
    assert url.query == {}
    assert options == {"ssl": "require", "server_settings": {"search_path": "codex_test_123"}}


def test_connection_string_rejects_unsafe_search_path():
    with pytest.raises(ValueError, match="search_path"):
        _connection_options(_asyncpg_url("postgres://user:password@host:5432/db?search_path=public,private"))


def test_runtime_rejects_sqlite_connection_strings():
    with pytest.raises(ValueError, match="PostgreSQL"):
        _asyncpg_url("sqlite+aiosqlite:///./rag.db")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_catalog_is_not_exposed_to_supabase_data_api(test_schema):
    database = Database(os.environ["DATABASE_URL"])
    try:
        async with database.session_factory() as session:
            result = await session.execute(text("SELECT grantee, table_name FROM information_schema.role_table_grants WHERE table_schema = current_schema() AND grantee IN ('anon', 'authenticated')"))
            assert result.all() == []
    finally:
        await database.close()
