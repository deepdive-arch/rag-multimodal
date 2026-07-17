from fastapi.testclient import TestClient
import pytest

from api.server import app
from core.exceptions import FileTooLargeError, InvalidMediaError
from services.ingestion import IngestionResult


def test_health_is_safe_without_keys():
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert "google_configured" in response.json()["services"]


def test_query_response_contract(monkeypatch):
    monkeypatch.setattr("api.server._run_query", lambda request: {"answer": "Resposta [Fonte 1]", "sources": [], "insufficient_context": False})
    with TestClient(app) as client:
        response = client.post("/api/query", json={"question": "O que há?"})
    assert response.status_code == 200
    assert response.json()["answer"].startswith("Resposta")


def test_ingest_endpoint_uses_safe_response(monkeypatch):
    monkeypatch.setattr("api.server.ingest_file", lambda path, name: IngestionResult("doc", name or "file.txt", "text", 1, False, []))
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


def test_ingest_hides_unexpected_stack_trace(monkeypatch):
    async def fail_upload(*args, **kwargs):
        raise RuntimeError("C:/private/secret.txt")

    monkeypatch.setattr("api.server.save_upload_stream", fail_upload)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/ingest", files={"file": ("file.txt", b"hello", "text/plain")})
    assert response.status_code == 500
    assert "secret.txt" not in response.json()["detail"]
