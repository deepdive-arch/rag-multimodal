import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import os
import sys
import time
from uuid import uuid4

import fitz
import httpx
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from api.server import app
from core.config import get_settings
from db.catalog import Catalog
from services.deletion import cleanup_expired_documents, clear_namespace_async
from services.pinecone_service import get_index
from services.storage import document_object_prefix, get_object_storage, original_object_key


pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_E2E") != "1", reason="set RUN_LIVE_E2E=1 for paid live services"),
]


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Evidência PDF persistente para a auditoria final. " * 4)
    payload = document.tobytes()
    document.close()
    return payload


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), "navy").save(output, format="PNG")
    return output.getvalue()


def _preflight(url: str, headers: dict[str, str], origin: str) -> httpx.Response:
    requested = ",".join(name.lower() for name in headers)
    return httpx.options(url, headers={"Origin": origin, "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": requested}, timeout=20)


def _presign(client: TestClient, name: str, mime_type: str, payload: bytes) -> dict:
    response = client.post("/api/uploads/presign", json={"file_name": name, "size_bytes": len(payload), "mime_type": mime_type, "sha256": sha256(payload).hexdigest()})
    assert response.status_code == 200, response.text
    return response.json()


def _upload(client: TestClient, name: str, mime_type: str, payload: bytes, *, expire_first: bool = False) -> str:
    settings = get_settings()
    authorization = _presign(client, name, mime_type, payload)
    if expire_first:
        preflight = _preflight(authorization["upload_url"], authorization["headers"], settings.frontend_origin)
        assert preflight.status_code == 204
        assert preflight.headers.get("access-control-allow-origin") == settings.frontend_origin
        time.sleep(settings.r2_presigned_upload_ttl_seconds + 1)
        expired = httpx.put(authorization["upload_url"], content=payload, headers=authorization["headers"], timeout=30)
        assert expired.status_code in {400, 403}
        refreshed = _presign(client, name, mime_type, payload)
        assert refreshed["upload_url"] and refreshed["upload_url"] != authorization["upload_url"]
        authorization = refreshed
    uploaded = httpx.put(authorization["upload_url"], content=payload, headers=authorization["headers"], timeout=60)
    assert uploaded.status_code in {200, 201, 204}, uploaded.text
    completed = client.post(f"/api/uploads/{authorization['doc_id']}/complete")
    assert completed.status_code == 200, completed.text
    current = client.get(f"/api/files/{authorization['doc_id']}")
    assert current.status_code == 200 and current.json()["status"] == "ready", current.text
    return authorization["doc_id"]


async def _external_ids(doc_id: str) -> tuple[list[str], list[str]]:
    catalog = Catalog.ephemeral(get_settings())
    try:
        return await catalog.get_vector_ids(doc_id), await catalog.get_object_keys(doc_id)
    finally:
        await catalog.close()


async def _assert_deleted(doc_id: str, vector_ids: list[str]) -> None:
    settings = get_settings()
    catalog = Catalog.ephemeral(settings)
    try:
        record = await catalog.get_file(doc_id)
        assert record and record["status"] == "deleted"
        assert await catalog.get_vector_ids(doc_id) == []
        assert await catalog.get_object_keys(doc_id) == []
    finally:
        await catalog.close()
    assert await get_object_storage(settings).list_objects_by_prefix(document_object_prefix(doc_id, settings)) == []
    response = await asyncio.to_thread(get_index(settings).fetch, ids=vector_ids, namespace=settings.pinecone_namespace)
    vectors = getattr(response, "vectors", None) or (response.get("vectors", {}) if isinstance(response, dict) else {})
    assert not vectors


async def _expire_pending_document() -> str:
    settings = get_settings()
    catalog = Catalog.ephemeral(settings)
    doc_id = str(uuid4())
    try:
        await catalog.create_document({"doc_id": doc_id, "original_name": "expired.txt", "sanitized_name": "expired.txt", "object_key": original_object_key(doc_id, "expired.txt", settings), "file_type": "text", "mime_type": "text/plain", "sha256": sha256(doc_id.encode()).hexdigest(), "size_bytes": 1, "status": "pending_upload", "upload_expires_at": datetime.now(UTC) - timedelta(minutes=1)})
    finally:
        await catalog.close()
    outcomes = await cleanup_expired_documents(settings, limit=1, older_than=timedelta())
    assert outcomes and outcomes[0]["status"] == "deleted"
    return doc_id


def test_live_persistence_survives_restart_and_deletes_idempotently(test_schema):
    settings = get_settings()
    text_payload = ("O código de evidência persistente é FAROL-ATLANTICO-2026. " * 8).encode()
    headers = {"X-Admin-Token": settings.admin_token}
    try:
        with TestClient(app) as first_backend:
            text_id = _upload(first_backend, "evidence.txt", "text/plain", text_payload, expire_first=True)
            pdf_id = _upload(first_backend, "evidence.pdf", "application/pdf", _pdf_bytes())
            image_id = _upload(first_backend, "evidence.png", "image/png", _image_bytes())
            duplicate = _presign(first_backend, "evidence.txt", "text/plain", text_payload)
            assert duplicate["duplicate"] is True and duplicate["doc_id"] == text_id and duplicate["upload_url"] is None
            first_query = first_backend.post("/api/query", json={"question": "Qual é o código de evidência persistente?", "filters": {"doc_id": text_id}, "top_k": 3})
            assert first_query.status_code == 200 and first_query.json()["sources"]
            assert first_query.json()["sources"][0]["doc_id"] == text_id
        with TestClient(app) as restarted_backend:
            listed = restarted_backend.get("/api/files")
            assert listed.status_code == 200
            assert {text_id, pdf_id, image_id}.issubset({item["doc_id"] for item in listed.json()["files"]})
            second_query = restarted_backend.post("/api/query", json={"question": "Repita o código de evidência persistente.", "filters": {"doc_id": text_id}, "top_k": 3})
            assert second_query.status_code == 200 and second_query.json()["sources"]
            limited = restarted_backend.post("/api/query", json={"question": "Esta consulta deve exceder o limite."})
            assert limited.status_code == 429
            vector_ids, object_keys = asyncio.run(_external_ids(text_id))
            assert vector_ids and object_keys
            deleted = restarted_backend.delete(f"/api/files/{text_id}", headers=headers)
            repeated = restarted_backend.delete(f"/api/files/{text_id}", headers=headers)
            assert deleted.status_code == 200 and deleted.json()["status"] == "deleted"
            assert repeated.status_code == 200 and repeated.json()["status"] == "deleted"
        asyncio.run(_assert_deleted(text_id, vector_ids))
        expired_id = asyncio.run(_expire_pending_document())
        asyncio.run(_assert_deleted(expired_id, []))
    finally:
        original_failure = sys.exc_info()[0] is not None
        try:
            asyncio.run(clear_namespace_async(settings, "DELETE_ALL", settings.admin_token))
        except Exception:
            if not original_failure:
                raise
