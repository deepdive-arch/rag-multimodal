from types import SimpleNamespace

import pytest

from api.server import health
from core.config import Settings
from services import pinecone_service


def make_settings(tmp_path, *, google_api_key="", pinecone_api_key="pinecone"):
    return Settings(database_path=tmp_path / "rag.db", google_api_key=google_api_key, pinecone_api_key=pinecone_api_key, pinecone_index_name="rag")


@pytest.mark.parametrize(
    ("google_api_key", "pinecone_state", "database_ready", "expected_status"),
    [
        ("google", "ready", True, "ok"),
        ("", "ready", True, "degraded"),
        ("google", "missing_key", True, "degraded"),
        ("google", "index_missing", True, "degraded"),
        ("google", "unavailable", True, "degraded"),
        ("google", "ready", False, "offline"),
    ],
)
@pytest.mark.asyncio
async def test_health_global_state(monkeypatch, tmp_path, google_api_key, pinecone_state, database_ready, expected_status):
    settings = make_settings(tmp_path, google_api_key=google_api_key, pinecone_api_key="" if pinecone_state == "missing_key" else "pinecone")

    async def fake_is_ready(_catalog):
        return database_ready

    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog.is_ready", fake_is_ready)
    monkeypatch.setattr("api.server.index_health", lambda _settings: pinecone_state)

    result = await health()

    assert result.status == expected_status
    assert result.services.database == ("ok" if database_ready else "unavailable")
    assert result.services.google == ("configured" if google_api_key else "missing_key")
    assert result.services.pinecone == pinecone_state


@pytest.mark.asyncio
async def test_health_runs_pinecone_probe_in_worker(monkeypatch, tmp_path):
    settings = make_settings(tmp_path, google_api_key="google")
    calls = []

    async def fake_to_thread(function, received_settings):
        calls.append((function, received_settings))
        return "ready"

    async def fake_is_ready(_catalog):
        return True

    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog.is_ready", fake_is_ready)
    monkeypatch.setattr("api.server.asyncio.to_thread", fake_to_thread)

    result = await health()

    assert result.status == "ok"
    assert calls == [(pinecone_service.index_health, settings)]


@pytest.mark.asyncio
async def test_health_hides_database_error(monkeypatch, tmp_path):
    settings = make_settings(tmp_path, google_api_key="secret-google")

    async def fail_is_ready(_catalog):
        raise RuntimeError("C:/private/database-secret.db")

    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog.is_ready", fail_is_ready)
    monkeypatch.setattr("api.server.index_health", lambda _settings: "ready")

    result = await health()
    payload = result.model_dump_json()

    assert result.status == "offline"
    assert "database-secret.db" not in payload
    assert "secret-google" not in payload


class FakeIndexManager:
    def __init__(self, indexes, description=None, error=None):
        self.indexes = indexes
        self.description = description
        self.error = error

    def list(self):
        if self.error:
            raise self.error
        return self.indexes

    def describe(self, _name):
        return self.description


@pytest.mark.parametrize(
    ("indexes", "description", "error", "expected"),
    [
        ([], None, None, "index_missing"),
        ([SimpleNamespace(name="rag")], SimpleNamespace(dimension=512, metric="cosine", status=SimpleNamespace(ready=True)), None, "invalid_configuration"),
        ([SimpleNamespace(name="rag")], None, RuntimeError("unauthorized"), "unavailable"),
        ([SimpleNamespace(name="rag")], SimpleNamespace(dimension=1536, metric="cosine", status=SimpleNamespace(ready=True)), None, "ready"),
    ],
)
def test_index_health_classifies_pinecone_states(monkeypatch, tmp_path, indexes, description, error, expected):
    settings = make_settings(tmp_path)
    client = SimpleNamespace(indexes=FakeIndexManager(indexes, description, error))
    monkeypatch.setattr(pinecone_service, "get_pinecone_client", lambda: client)

    assert pinecone_service.index_health(settings) == expected


def test_index_health_reports_missing_key_without_client(monkeypatch, tmp_path):
    settings = make_settings(tmp_path, pinecone_api_key="")
    monkeypatch.setattr(pinecone_service, "get_pinecone_client", lambda: pytest.fail("client must not be created"))

    assert pinecone_service.index_health(settings) == "missing_key"
