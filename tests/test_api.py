from fastapi.testclient import TestClient

from api.server import app
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
