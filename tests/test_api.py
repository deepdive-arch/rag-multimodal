from fastapi.testclient import TestClient
import pytest
from pathlib import Path
from uuid import uuid4

from api.server import app
from core.config import Settings
from core.exceptions import ExternalServiceError, FileTooLargeError, InvalidMediaError, QuotaExceededError, UnsupportedFileError
from services.ingestion import IngestionResult
from services.storage import ObjectMetadata
from services.deletion import DeletionOutcome


def test_health_is_safe_without_keys(monkeypatch, tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@127.0.0.1:1/test",
        google_api_key="",
        pinecone_api_key="",
    )
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog.is_ready", lambda self: _ready())
    monkeypatch.setattr("api.server.index_health", lambda _: "missing_key")
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["services"] == {
        "database": "ok",
        "r2": "missing_config",
        "google": "missing_key",
        "gemini": "missing_key",
        "pinecone": "missing_key",
    }
    assert "google_api_key" not in response.text
    assert "pinecone_api_key" not in response.text


async def _ready():
    return True


def test_query_response_contract(monkeypatch):
    monkeypatch.setattr(
        "api.server._run_query",
        lambda request, _visitor_id=None: {"answer": "Resposta [Fonte 1]", "sources": [], "insufficient_context": False},
    )
    catalog = GeneratedResponseCatalog()
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    with TestClient(app) as client:
        response = client.post("/api/query", json={"question": "O que há?"})
    assert response.status_code == 200
    assert response.json()["answer"].startswith("Resposta")
    assert response.json()["response_id"] == response.json()["message_id"]
    assert response.json()["response_id"] in catalog.responses


class GeneratedResponseCatalog:
    """In-memory catalog double for response and feedback contract tests."""

    def __init__(self):
        self.responses = {}
        self.feedback = {}
        self.feedback_calls = []

    async def reserve_query_quota(self, *_args):
        return None

    async def persist_generated_response(self, visitor_id, question, answer, source_ids, sources, insufficient_context, conversation_id=None):
        response_id = str(uuid4())
        selected_conversation = conversation_id or str(uuid4())
        self.responses[response_id] = {"visitor_id": visitor_id, "message_id": response_id, "response_id": response_id, "conversation_id": selected_conversation, "question": question, "answer": answer, "source_ids": source_ids, "sources": sources, "insufficient_context": insufficient_context, "created_at": None}
        return {"conversation_id": selected_conversation, "response_id": response_id, "message_id": response_id}

    async def get_message(self, visitor_id, response_id):
        response = self.responses.get(response_id)
        return response if response and response["visitor_id"] == visitor_id else None

    async def record_feedback(self, visitor_id, response_id, useful):
        response = await self.get_message(visitor_id, response_id)
        if response is None:
            raise RuntimeError("response not found")
        key = (visitor_id, response_id)
        self.feedback_calls.append((visitor_id, response_id, useful))
        if key in self.feedback:
            self.feedback[key]["useful"] = useful
            return self.feedback[key]["id"]
        feedback_id = str(uuid4())
        self.feedback[key] = {"id": feedback_id, "useful": useful, "question": response["question"], "answer": response["answer"], "source_ids": response["source_ids"]}
        return feedback_id


def _response_id_for_feedback(monkeypatch):
    """Create one persisted response and return its client plus ID."""
    catalog = GeneratedResponseCatalog()
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server._run_query", lambda *_args: {"answer": "Resposta persistida", "sources": [], "insufficient_context": False})
    client = TestClient(app)
    response = client.post("/api/query", json={"question": "Pergunta original"})
    return client, catalog, response.json()["response_id"]


def test_feedback_accepts_positive_negative_and_updates_idempotently(monkeypatch):
    client, catalog, response_id = _response_id_for_feedback(monkeypatch)
    first = client.post("/api/feedback", json={"response_id": response_id, "useful": True})
    second = client.post("/api/feedback", json={"response_id": response_id, "useful": False})
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert catalog.feedback[next(iter(catalog.feedback))]["useful"] is False
    client.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"useful": True},
        {"response_id": "not-a-uuid", "useful": True},
        {"response_id": "00000000-0000-4000-8000-000000000000", "useful": "true"},
        {"response_id": "00000000-0000-4000-8000-000000000000", "useful": True, "visitor_id": "spoof"},
        {"response_id": "00000000-0000-4000-8000-000000000000", "useful": True, "answer": "x" * 20001},
    ],
)
def test_feedback_rejects_invalid_or_client_authoritative_fields(monkeypatch, payload):
    client, _, _ = _response_id_for_feedback(monkeypatch)
    assert client.post("/api/feedback", json=payload).status_code == 422
    client.close()


def test_feedback_rejects_invented_and_other_visitor_response_ids(monkeypatch):
    first_client, _, response_id = _response_id_for_feedback(monkeypatch)
    with TestClient(app) as second_client:
        assert second_client.post("/api/feedback", json={"response_id": response_id, "useful": True}).status_code == 404
    assert first_client.post("/api/feedback", json={"response_id": "00000000-0000-4000-8000-000000000000", "useful": True}).status_code == 404
    first_client.close()


def test_failed_generation_or_persistence_never_exposes_feedback_id(monkeypatch):
    catalog = GeneratedResponseCatalog()
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server._run_query", lambda *_args: (_ for _ in ()).throw(ExternalServiceError("Gemini indisponível")))
    with TestClient(app, raise_server_exceptions=False) as client:
        generation_failure = client.post("/api/query", json={"question": "timeout do provedor"})
    assert generation_failure.status_code == 503
    assert not catalog.responses

    monkeypatch.setattr("api.server._run_query", lambda *_args: (_ for _ in ()).throw(TimeoutError("provider timeout")))
    with TestClient(app, raise_server_exceptions=False) as client:
        timeout_failure = client.post("/api/query", json={"question": "timeout real"})
    assert timeout_failure.status_code == 500
    assert not catalog.responses

    def fail_persist(*_args, **_kwargs):
        raise RuntimeError("persistence failure")

    monkeypatch.setattr("api.server._run_query", lambda *_args: {"answer": "Resposta", "sources": [], "insufficient_context": False})
    catalog.persist_generated_response = fail_persist
    with TestClient(app, raise_server_exceptions=False) as client:
        persistence_failure = client.post("/api/query", json={"question": "falha de persistência"})
    assert persistence_failure.status_code == 500
    assert "response_id" not in persistence_failure.json()


def test_destructive_routes_require_admin_token(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path)
    monkeypatch.setattr("api.dependencies.get_settings", lambda: settings)
    with TestClient(app) as client:
        response = client.delete("/api/files/doc-1")
    assert response.status_code == 403


def test_destructive_route_accepts_valid_admin_token(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path, admin_token="admin-secret")
    monkeypatch.setattr("api.dependencies.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.delete_document", lambda _doc_id, _settings: DeletionOutcome("doc-1", "deleted", "completed", True))
    with TestClient(app) as client:
        response = client.delete("/api/files/doc-1", headers={"X-Admin-Token": "admin-secret"})
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


def test_retry_route_requires_admin_and_schedules_failed_document(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path, admin_token="admin-secret")
    catalog = FakeUploadCatalog(_upload_record(status="failed"))
    retried = []
    monkeypatch.setattr("api.dependencies.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server.retry_document", lambda doc_id, _settings: retried.append(doc_id))
    with TestClient(app) as client:
        denied = client.post("/api/files/doc-1/retry")
        accepted = client.post("/api/files/doc-1/retry", headers={"X-Admin-Token": "admin-secret"})
    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "processing"
    assert retried == ["doc-1"]


def test_public_query_quota_returns_429_and_retry_after(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path, public_demo_mode=True, rate_limit_secret="server-only-secret")

    class QuotaCatalog:
        async def reserve_query_quota(self, *_args):
            raise QuotaExceededError("O limite diário de consultas deste cliente foi atingido.", 42)

    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: QuotaCatalog())
    with TestClient(app) as client:
        response = client.post("/api/query", json={"question": "teste"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert "limite diário" in response.json()["detail"]


def test_ingest_endpoint_uses_safe_response(monkeypatch):
    monkeypatch.setattr(
        "api.server.ingest_file", lambda path, name, **kwargs: IngestionResult("doc", name or "file.txt", "text", 1, False, [])
    )
    with TestClient(app) as client:
        response = client.post("/api/ingest", files={"file": ("file.txt", b"hello", "text/plain")})
    assert response.status_code == 200
    assert response.json()["doc_id"] == "doc"


@pytest.mark.asyncio
async def test_ingest_preserves_file_too_large_status(monkeypatch):
    async def fail_upload(*args, **kwargs):
        raise FileTooLargeError("Arquivo excede o limite")

    monkeypatch.setattr("api.server.save_upload_stream", fail_upload)
    with TestClient(app) as client:
        response = client.post("/api/ingest", files={"file": ("file.txt", b"hello", "text/plain")})
    assert response.status_code == 413
    assert response.json()["detail"] == "Arquivo excede o limite"


@pytest.mark.asyncio
async def test_ingest_preserves_unprocessable_detail(monkeypatch):
    async def fail_upload(*args, **kwargs):
        raise InvalidMediaError("O arquivo não contém conteúdo indexável.")

    monkeypatch.setattr("api.server.save_upload_stream", fail_upload)
    with TestClient(app) as client:
        response = client.post("/api/ingest", files={"file": ("file.txt", b"hello", "text/plain")})
    assert response.status_code == 422
    assert "conteúdo indexável" in response.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_preserves_unsupported_signature_status(monkeypatch):
    async def fail_upload(*args, **kwargs):
        raise UnsupportedFileError("Assinatura JPG inválida")

    monkeypatch.setattr("api.server.save_upload_stream", fail_upload)
    with TestClient(app) as client:
        response = client.post("/api/ingest", files={"file": ("image.jpg", b"hello", "image/jpeg")})
    assert response.status_code == 415
    assert response.json()["detail"] == "Assinatura JPG inválida"


def test_ingest_hides_unexpected_stack_trace(monkeypatch):
    async def fail_upload(*args, **kwargs):
        raise RuntimeError("C:/private/secret.txt")

    monkeypatch.setattr("api.server.save_upload_stream", fail_upload)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/ingest", files={"file": ("file.txt", b"hello", "text/plain")})
    assert response.status_code == 500
    assert "secret.txt" not in response.json()["detail"]


def _upload_settings(tmp_path: Path, **overrides) -> Settings:
    """Build safe settings for presign endpoint tests."""
    values = {
        "_env_file": None,
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@127.0.0.1:1/test",
        "r2_endpoint_url": "https://account.r2.cloudflarestorage.com",
        "r2_access_key_id": "access",
        "r2_secret_access_key": "secret",
        "r2_bucket_name": "bucket",
        "temp_processing_dir": tmp_path / "processing",
    }
    return Settings(**(values | overrides))


def _upload_record(doc_id="doc-1", status="pending_upload", **overrides):
    """Build the compatibility projection returned by the catalog."""
    return {
        "doc_id": doc_id,
        "original_name": "notes.txt",
        "sanitized_name": "notes.txt",
        "stored_name": "notes.txt",
        "storage_key": "rag/test/default/documents/doc-1/original/notes.txt",
        "file_type": "text",
        "mime_type": "text/plain",
        "sha256": "a" * 64,
        "size_bytes": 5,
        "status": status,
        "chunks_count": 0,
        "warnings": [],
        "warnings_json": "[]",
        "safe_error_message": None,
        "created_at": "2026-07-18T00:00:00+00:00",
    } | overrides


class FakeUploadCatalog:
    """Minimal async catalog double for public upload route tests."""

    def __init__(self, record=None):
        self.record = record
        self.claims = 0

    async def find_by_sha256(self, _sha256, _visitor_id=None):
        return self.record

    async def create_document(self, record):
        self.record = _upload_record(
            **{
                key: value
                for key, value in record.items()
                if key
                in {
                    "doc_id",
                    "status",
                    "original_name",
                    "sanitized_name",
                    "mime_type",
                    "sha256",
                    "size_bytes",
                    "storage_key",
                }
            },
            storage_key=record["object_key"],
            file_type=record["file_type"],
            created_at=record.get("created_at"),
        )
        return self.record | {"created": True}

    async def create_document_with_quota(self, record, *_args):
        return await self.create_document(record)

    async def get_file(self, _doc_id, _visitor_id=None):
        return self.record

    async def renew_upload(self, _doc_id, _expires_at, _visitor_id=None):
        return None

    async def mark_uploaded(self, _doc_id, _visitor_id=None):
        if self.record["status"] != "pending_upload":
            return False
        self.record["status"] = "uploaded"
        return True

    async def claim_processing(self, _doc_id, _visitor_id=None):
        if self.record["status"] != "uploaded":
            return False
        self.claims += 1
        self.record["status"] = "processing"
        return True


class FakeUploadStorage:
    """R2 double exposing the provider-neutral adapter contract."""

    def __init__(self, metadata=None):
        self.metadata = metadata
        self.presign_calls = []

    async def generate_presigned_put_url(self, key, **kwargs):
        self.presign_calls.append((key, kwargs))
        return "https://account.r2.cloudflarestorage.com/bucket/signed"

    async def head_object(self, _key):
        return self.metadata


def test_upload_presign_validates_and_signs_server_metadata(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path)
    catalog = FakeUploadCatalog()
    storage = FakeUploadStorage()
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server.get_object_storage", lambda _settings: storage)
    with TestClient(app) as client:
        response = client.post(
            "/api/uploads/presign",
            json={"file_name": "notes.txt", "size_bytes": 5, "mime_type": "text/plain", "sha256": "A" * 64},
        )
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "pending_upload"
    assert body["headers"]["Content-Type"] == "text/plain"
    assert body["headers"]["x-amz-meta-sha256"] == "a" * 64
    assert "object_key" not in body
    assert settings.r2_secret_access_key not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"file_name": "notes.exe", "size_bytes": 5, "mime_type": "application/octet-stream", "sha256": "a" * 64},
        {"file_name": "notes.txt", "size_bytes": 5, "mime_type": "application/pdf", "sha256": "a" * 64},
        {"file_name": "notes.txt", "size_bytes": 5, "mime_type": "text/plain", "sha256": "not-a-sha"},
    ],
)
def test_upload_presign_rejects_extension_mime_and_sha(monkeypatch, tmp_path, payload):
    settings = _upload_settings(tmp_path)
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    with TestClient(app) as client:
        response = client.post("/api/uploads/presign", json=payload)
    assert response.status_code == 422 if payload["sha256"] == "not-a-sha" else response.status_code in {415, 422}


def test_upload_presign_rejects_size_and_duplicate(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path, max_upload_size_mb=2, max_media_context_size_mb=1)
    catalog = FakeUploadCatalog(_upload_record(status="ready"))
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    with TestClient(app) as client:
        too_large = client.post(
            "/api/uploads/presign",
            json={
                "file_name": "notes.txt",
                "size_bytes": 3 * 1024 * 1024,
                "mime_type": "text/plain",
                "sha256": "b" * 64,
            },
        )
        duplicate = client.post(
            "/api/uploads/presign",
            json={"file_name": "notes.txt", "size_bytes": 5, "mime_type": "text/plain", "sha256": "a" * 64},
        )
    assert too_large.status_code == 413
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["upload_url"] is None


def test_upload_presign_rejects_client_object_key(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path)
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/uploads/presign",
            json={
                "file_name": "notes.txt",
                "size_bytes": 5,
                "mime_type": "text/plain",
                "sha256": "a" * 64,
                "object_key": "../../private",
            },
        )
    assert response.status_code == 422


def test_upload_complete_rejects_missing_and_mismatched_object(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path)
    catalog = FakeUploadCatalog(_upload_record())
    storage = FakeUploadStorage(None)
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server.get_object_storage", lambda _settings: storage)
    with TestClient(app) as client:
        missing = client.post("/api/uploads/doc-1/complete")
    assert missing.status_code == 409
    storage.metadata = ObjectMetadata(catalog.record["storage_key"], 6, "text/plain", metadata={})
    with TestClient(app) as client:
        mismatch = client.post("/api/uploads/doc-1/complete")
    assert mismatch.status_code == 422


def test_upload_complete_is_idempotent_and_claims_once(monkeypatch, tmp_path):
    settings = _upload_settings(tmp_path)
    record = _upload_record()
    expected = {
        "doc-id": "doc-1",
        "sha256": "a" * 64,
        "original-name": "notes.txt",
        "mime-type": "text/plain",
        "upload-version": "1",
    }
    catalog = FakeUploadCatalog(record)
    storage = FakeUploadStorage(ObjectMetadata(record["storage_key"], 5, "text/plain", metadata=expected))
    processed = []
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog", lambda _settings: catalog)
    monkeypatch.setattr("api.server.get_object_storage", lambda _settings: storage)
    monkeypatch.setattr("api.server.process_uploaded_document", lambda doc_id, _settings: processed.append(doc_id))
    with TestClient(app) as client:
        first = client.post("/api/uploads/doc-1/complete")
        second = client.post("/api/uploads/doc-1/complete")
    assert first.status_code == 200
    assert first.json()["status"] == "processing"
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert catalog.claims == 1
    assert processed == ["doc-1"]


def test_frontend_upload_contract_has_no_r2_secrets_and_exposes_states():
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend/lib/api.ts").read_text(encoding="utf-8") + (
        root / "frontend/components/UploadDropzone.tsx"
    ).read_text(encoding="utf-8")
    assert "crypto.subtle.digest" in source
    assert "XMLHttpRequest" in source
    assert all(secret not in source for secret in ("R2_SECRET_ACCESS_KEY", "R2_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))
    for phase in ("preparando", "enviando", "validando", "processando", "indexando", "pronto", "falhou"):
        assert phase in source
