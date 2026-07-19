from pinecone import NotFoundError
import pytest

from core.config import Settings
from core.exceptions import ConfigurationError
from core.visitor import new_visitor_id
from services import pinecone_service


class FakeIndex:
    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure

    def delete(self, **_kwargs):
        self.calls += 1
        if self.failure:
            raise self.failure

    def describe_index_stats(self):
        return {"namespaces": {"audit-test": {"vector_count": 1}}}


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test", database_url="", pinecone_namespace="audit-test")


def test_delete_all_vectors_is_idempotent_when_namespace_does_not_exist(monkeypatch):
    index = FakeIndex(NotFoundError("Namespace not found"))
    monkeypatch.setattr(pinecone_service, "get_index", lambda _settings: index)

    pinecone_service.delete_all_vectors(_settings())

    assert index.calls == 1


def test_pinecone_retry_recovers_from_temporary_failure(monkeypatch):
    attempts = iter([OSError("temporary"), OSError("temporary"), "ok"])
    monkeypatch.setattr(pinecone_service.time, "sleep", lambda _seconds: None)

    result = pinecone_service._with_retry(lambda: _next_attempt(attempts))

    assert result == "ok"


class RecordingIndex:
    def __init__(self):
        self.calls = []

    def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return {"matches": []}

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))

    def fetch(self, **kwargs):
        self.calls.append(("fetch", kwargs))
        return {"vectors": {}}

    def describe_index_stats(self):
        return {"namespaces": {"audit-test": {"vector_count": 1}, "audit-test--visitor_deadbeef": {"vector_count": 1}, "other-app": {"vector_count": 1}}}


def test_vector_operations_use_distinct_server_derived_visitor_namespaces(monkeypatch):
    settings = _settings()
    first, second = new_visitor_id(), new_visitor_id()
    index = RecordingIndex()
    monkeypatch.setattr(pinecone_service, "get_index", lambda _settings: index)
    pinecone_service.upsert_vectors([{"id": "a", "values": [0.0], "metadata": {"visitor_id": second}}], settings, first)
    pinecone_service.query_vectors([0.0], 1, {"visitor_id": {"$eq": second}}, settings, second)
    namespaces = [call[1]["namespace"] for call in index.calls]
    assert namespaces == [pinecone_service.visitor_namespace(settings, first), pinecone_service.visitor_namespace(settings, second)]
    assert namespaces[0] != namespaces[1]
    assert index.calls[0][1]["vectors"][0]["metadata"]["visitor_id"] == first


def test_query_rejects_unscoped_or_cross_visitor_metadata_filter(monkeypatch):
    settings = _settings()
    first, second = new_visitor_id(), new_visitor_id()
    monkeypatch.setattr(pinecone_service, "get_index", lambda _settings: RecordingIndex())
    with pytest.raises(ConfigurationError):
        pinecone_service.query_vectors([0.0], 1, {"file_type": {"$eq": "text"}}, settings, first)
    with pytest.raises(ConfigurationError):
        pinecone_service.query_vectors([0.0], 1, {"visitor_id": {"$eq": second}}, settings, first)


def test_admin_cleanup_ignores_unrelated_namespaces(monkeypatch):
    settings = _settings()
    index = RecordingIndex()
    monkeypatch.setattr(pinecone_service, "get_index", lambda _settings: index)
    pinecone_service.delete_all_vectors(settings)
    deleted = [kwargs["namespace"] for operation, kwargs in index.calls if operation == "delete"]
    assert deleted == ["audit-test", "audit-test--visitor_deadbeef"]


def _next_attempt(attempts):
    outcome = next(attempts)
    if isinstance(outcome, Exception):
        raise outcome
    return outcome
