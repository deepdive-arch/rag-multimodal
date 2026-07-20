from uuid import UUID, uuid1, uuid4

import pytest
from fastapi.testclient import TestClient

from api.server import app
from api.schemas import SourceResponse
from core.config import Settings
from core.exceptions import ConfigurationError
from core.visitor import VISITOR_COOKIE_NAME, new_visitor_id, parse_visitor_id, sign_visitor_cookie, verify_visitor_cookie
from services import pinecone_service
from services.retrieval import _build_filter
from services.storage import original_object_key, original_object_metadata


def _settings(**overrides):
    """Build settings without reading a developer .env file."""
    values = {"_env_file": None, "app_env": "test", "database_url": "postgresql+asyncpg://test:test@127.0.0.1:1/test", "visitor_session_secret": "test-visitor-session-secret-32-bytes"}
    return Settings(**(values | overrides))


def _set_visitor(client, visitor_id, settings):
    """Install one authentic server-signed visitor cookie in a test client."""
    client.cookies.set(VISITOR_COOKIE_NAME, sign_visitor_cookie(visitor_id, settings.visitor_cookie_secret), domain="testserver.local", path="/")


def test_visitor_ids_are_uuid4_and_reject_client_values():
    """Only canonical UUIDv4 values enter the ownership boundary."""
    visitor_id = new_visitor_id()
    assert UUID(visitor_id).version == 4
    assert parse_visitor_id(visitor_id.upper()) == visitor_id
    assert parse_visitor_id(str(uuid1())) is None
    assert parse_visitor_id("127.0.0.1") is None


def test_storage_and_r2_metadata_are_visitor_scoped():
    """Two visitors never receive the same controlled original key."""
    settings = _settings()
    first, second = new_visitor_id(), new_visitor_id()
    first_key = original_object_key("doc-1", "notes.txt", settings, first)
    second_key = original_object_key("doc-1", "notes.txt", settings, second)
    metadata = original_object_metadata("doc-1", "a" * 64, "notes.txt", "text/plain", settings, first)
    assert first_key != second_key
    assert first.replace("-", "") in first_key
    assert metadata["visitor-id"] == first.replace("-", "")


def test_retrieval_filter_always_contains_the_resolved_visitor():
    """File filters compose with a mandatory visitor equality predicate."""
    visitor_id = new_visitor_id()
    assert _build_filter("text", "doc-1", visitor_id) == {
        "$and": [
            {"visitor_id": {"$eq": visitor_id}},
            {"file_type": {"$eq": "text"}},
            {"doc_id": {"$eq": "doc-1"}},
        ]
    }


def test_pinecone_rejects_unscoped_query_and_overwrites_spoofed_metadata():
    """The vector adapter rejects global reads and signs ownership server-side."""
    settings = _settings()
    with pytest.raises(ConfigurationError):
        pinecone_service.query_vectors([0.0], 1, {"file_type": {"$eq": "text"}}, settings, new_visitor_id())
    visitor_id = new_visitor_id()
    vector = pinecone_service._vector_with_visitor({"metadata": {"visitor_id": "spoof"}}, visitor_id)
    assert vector["metadata"]["visitor_id"] == visitor_id


def test_cookie_is_stable_across_requests(monkeypatch):
    """The backend sets one persistent HttpOnly identity and reuses it."""
    settings = _settings()
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    with TestClient(app) as client:
        first = client.get("/api/session")
        cookie = client.cookies.get(VISITOR_COOKIE_NAME)
        second = client.get("/api/session")
    assert first.status_code == second.status_code == 200
    assert verify_visitor_cookie(cookie, settings.visitor_cookie_secret) is not None
    assert "httponly" in first.headers["set-cookie"].lower()
    assert "samesite=lax" in first.headers["set-cookie"].lower()
    assert "set-cookie" not in second.headers
    assert first.headers["cache-control"] == "private, no-store, max-age=0"


def test_raw_or_tampered_visitor_cookie_is_replaced(monkeypatch):
    """A client cannot select an owner by sending a raw or modified visitor UUID."""
    settings = _settings()
    injected = new_visitor_id()
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    with TestClient(app) as client:
        client.cookies.set(VISITOR_COOKIE_NAME, injected, domain="testserver.local", path="/")
        client.get("/api/session")
        replacement = client.cookies.get(VISITOR_COOKIE_NAME)
        resolved = verify_visitor_cookie(replacement, settings.visitor_cookie_secret)
        client.cookies.set(VISITOR_COOKIE_NAME, replacement[:-1] + ("0" if replacement[-1] != "0" else "1"), domain="testserver.local", path="/")
        client.get("/api/session")
        after_tamper = verify_visitor_cookie(client.cookies.get(VISITOR_COOKIE_NAME), settings.visitor_cookie_secret)
    assert resolved and resolved != injected
    assert after_tamper and after_tamper not in {injected, resolved}


class ScopedApiCatalog:
    """Small owner-aware double for HTTP IDOR and history contract tests."""

    def __init__(self, first_visitor, second_visitor):
        self.docs = {first_visitor: "doc-a", second_visitor: "doc-b"}
        self.conversations = {}
        self.messages = {}
        self.feedback = []

    async def list_files(self, visitor_id):
        return [_file_record(doc_id) for owner, doc_id in self.docs.items() if owner == visitor_id]

    async def get_file(self, doc_id, visitor_id=None):
        return _file_record(doc_id) if self.docs.get(visitor_id) == doc_id else None

    async def reserve_query_quota(self, *_args):
        return None

    async def create_conversation(self, visitor_id, conversation_id=None):
        selected = conversation_id or str(uuid4())
        if conversation_id and self.conversations.get(selected) != visitor_id:
            raise ValueError("conversation owner mismatch")
        self.conversations[selected] = visitor_id
        return selected

    async def record_message(self, visitor_id, conversation_id, question, answer, source_ids, sources, insufficient_context):
        if self.conversations.get(conversation_id) != visitor_id:
            raise ValueError("conversation owner mismatch")
        message_id = str(uuid4())
        self.messages[message_id] = {"owner": visitor_id, "message_id": message_id, "conversation_id": conversation_id, "question": question, "answer": answer, "source_ids": source_ids, "sources": sources, "insufficient_context": insufficient_context, "created_at": None}
        return message_id

    async def get_conversation(self, visitor_id, conversation_id):
        return [message for message in self.messages.values() if message["conversation_id"] == conversation_id and message["owner"] == visitor_id] if self.conversations.get(conversation_id) == visitor_id else None

    async def delete_conversation(self, visitor_id, conversation_id):
        if self.conversations.get(conversation_id) != visitor_id:
            return False
        self.conversations.pop(conversation_id)
        return True

    async def get_message(self, visitor_id, message_id):
        message = self.messages.get(message_id)
        return message if message and message["owner"] == visitor_id else None

    async def record_feedback(self, *args):
        self.feedback.append(args)
        return str(uuid4())


def _file_record(doc_id):
    """Build the public file projection expected by the API mapper."""
    return {"doc_id": doc_id, "original_name": f"{doc_id}.txt", "file_type": "text", "mime_type": "text/plain", "chunks_count": 1, "size_bytes": 1, "status": "ready", "warnings_json": "[]", "created_at": None}


def _source(doc_id):
    """Build one safe source for the query contract."""
    return SourceResponse(doc_id=doc_id, chunk_id=f"{doc_id}-chunk", file_name=f"{doc_id}.txt", stored_name="", file_type="text", mime_type="text/plain", content_modality="text", page_number=0, text_preview="evidence", media_url=None, score=1.0)


def test_api_scopes_files_queries_history_delete_and_feedback(monkeypatch):
    """Visitors A and B cannot read or mutate each other's resources."""
    settings = _settings()
    first_visitor, second_visitor = new_visitor_id(), new_visitor_id()
    catalog = ScopedApiCatalog(first_visitor, second_visitor)
    seen = []
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server._run_query", lambda body, visitor_id: seen.append(visitor_id) or {"answer": f"answer-{visitor_id}", "sources": [_source(catalog.docs[visitor_id])], "insufficient_context": False})
    with TestClient(app) as first_client, TestClient(app) as second_client:
        _set_visitor(first_client, first_visitor, settings)
        _set_visitor(second_client, second_visitor, settings)
        assert [row["doc_id"] for row in first_client.get("/api/files").json()["files"]] == ["doc-a"]
        assert first_client.get("/api/files/doc-b").status_code == 404
        first_query = first_client.post("/api/query", json={"question": "first"}).json()
        second_query = second_client.post("/api/query", json={"question": "second"}).json()
        assert first_query["sources"][0]["doc_id"] == "doc-a"
        assert second_query["sources"][0]["doc_id"] == "doc-b"
        assert seen == [first_visitor, second_visitor]
        conversation_id = first_query["conversation_id"]
        message_id = first_query["response_id"]
        assert second_client.get(f"/api/conversations/{conversation_id}").status_code == 404
        assert second_client.delete(f"/api/conversations/{conversation_id}").status_code == 404
        assert second_client.post("/api/feedback", json={"response_id": message_id, "useful": False}).status_code == 404
        assert first_client.post("/api/feedback", json={"response_id": message_id, "useful": True}).status_code == 200
        assert first_client.delete(f"/api/conversations/{conversation_id}").status_code == 200


def test_client_identity_namespace_and_r2_key_injection_are_rejected(monkeypatch):
    """Public request schemas reject ownership and external-storage control fields."""
    settings = _settings()
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    with TestClient(app) as client:
        visitor = client.post("/api/query", json={"question": "x", "visitor_id": str(uuid4())})
        namespace = client.post("/api/query", json={"question": "x", "namespace": "victim"})
        nested_namespace = client.post("/api/query", json={"question": "x", "filters": {"namespace": "victim"}})
        object_key = client.post("/api/uploads/presign", json={"file_name": "x.txt", "size_bytes": 1, "mime_type": "text/plain", "sha256": "a" * 64, "object_key": "../../victim"})
    assert {visitor.status_code, namespace.status_code, nested_namespace.status_code, object_key.status_code} == {422}


def test_fabricated_and_enumerated_response_ids_are_indistinguishable(monkeypatch):
    """Unknown response UUIDs cannot be evaluated or used as an enumeration oracle."""
    settings = _settings()
    visitor = new_visitor_id()
    catalog = ScopedApiCatalog(visitor, new_visitor_id())
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    with TestClient(app) as client:
        _set_visitor(client, visitor, settings)
        first = client.post("/api/feedback", json={"response_id": str(uuid4()), "useful": True})
        second = client.post("/api/feedback", json={"response_id": str(uuid4()), "useful": True})
    assert first.status_code == second.status_code == 404
    assert first.json() == second.json()
    assert catalog.feedback == []


def test_query_without_owned_documents_and_with_foreign_filter_returns_no_sources(monkeypatch):
    """Empty and foreign-only scopes cannot recover or cite another visitor's document."""
    settings = _settings()
    owner, empty = new_visitor_id(), new_visitor_id()
    catalog = ScopedApiCatalog(owner, new_visitor_id())
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server._run_query", lambda body, visitor_id: {"answer": "insufficient", "sources": [], "insufficient_context": True})
    with TestClient(app) as client:
        _set_visitor(client, empty, settings)
        no_documents = client.post("/api/query", json={"question": "x"})
        foreign_filter = client.post("/api/query", json={"question": "x", "filters": {"doc_id": catalog.docs[owner]}})
    assert no_documents.status_code == foreign_filter.status_code == 200
    assert no_documents.json()["sources"] == foreign_filter.json()["sources"] == []
    assert no_documents.json()["insufficient_context"] is foreign_filter.json()["insufficient_context"] is True


def test_cross_visitor_delete_and_response_route_do_not_expose_resources(monkeypatch):
    """Foreign file deletion is rejected and no public response-by-ID route exists."""
    settings = _settings(public_demo_mode=True)
    owner, attacker = new_visitor_id(), new_visitor_id()
    catalog = ScopedApiCatalog(owner, attacker)
    deleted = []

    async def record_delete(*args):
        deleted.append(args)

    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server.delete_document_async", record_delete)
    with TestClient(app) as client:
        _set_visitor(client, attacker, settings)
        foreign_delete = client.delete(f"/api/files/{catalog.docs[owner]}")
        absent_response_route = client.get(f"/api/responses/{uuid4()}")
    assert foreign_delete.status_code == absent_response_route.status_code == 404
    assert deleted == []
