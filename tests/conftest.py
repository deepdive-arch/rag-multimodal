import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from db.database import Database


os.environ["APP_ENV"] = "test"
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
_TEST_DATABASE_SCHEMA = os.environ.get("TEST_DATABASE_SCHEMA", "")
_RUNTIME_DATABASE_URL = (
    make_url(_TEST_DATABASE_URL).update_query_dict({"search_path": _TEST_DATABASE_SCHEMA}).render_as_string(hide_password=False)
    if _TEST_DATABASE_URL and _TEST_DATABASE_SCHEMA
    else _TEST_DATABASE_URL or "postgresql+asyncpg://test:test@127.0.0.1:1/test"
)
os.environ["DATABASE_URL"] = _RUNTIME_DATABASE_URL
if _TEST_DATABASE_URL:
    os.environ["TEST_DATABASE_URL"] = _RUNTIME_DATABASE_URL


def pytest_configure(config):
    """Keep tests independent from a developer's local .env paths."""
    config.addinivalue_line("markers", "external: requires real Gemini/Pinecone credentials")
    config.addinivalue_line("markers", "integration: requires TEST_DATABASE_URL and a disposable Postgres database")


def pytest_collection_modifyitems(config, items):
    """Skip database integration tests when no disposable database was supplied."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="set TEST_DATABASE_URL to run Postgres integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def test_schema():
    """Apply and remove the Alembic schema around the integration suite."""
    url = os.environ.get("DATABASE_URL") if _TEST_DATABASE_URL else None
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run Postgres integration tests")
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    if _TEST_DATABASE_SCHEMA:
        asyncio.run(_set_test_schema(_TEST_DATABASE_URL, _TEST_DATABASE_SCHEMA, create=True))
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=environment, check=True)
        yield
    finally:
        try:
            subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], cwd=root, env=environment, check=True)
        finally:
            if _TEST_DATABASE_SCHEMA:
                asyncio.run(_set_test_schema(_TEST_DATABASE_URL, _TEST_DATABASE_SCHEMA, create=False))


async def _set_test_schema(database_url: str, schema: str, *, create: bool) -> None:
    """Create or remove one explicitly isolated integration-test schema."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError("TEST_DATABASE_SCHEMA must be one lowercase PostgreSQL identifier")
    database = Database(database_url)
    statement = f'CREATE SCHEMA "{schema}"' if create else f'DROP SCHEMA "{schema}" CASCADE'
    try:
        async with database.session_factory.begin() as session:
            await session.execute(text(statement))
    finally:
        await database.close()
