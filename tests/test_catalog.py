from datetime import date
import os
from uuid import uuid4

import pytest

from core.exceptions import FileNotFoundInCatalogError
from db.catalog import Catalog


pytestmark = pytest.mark.integration


@pytest.fixture
async def catalog(test_schema):
    catalog = Catalog(os.environ["TEST_DATABASE_URL"])
    await catalog.clear_catalog()
    try:
        yield catalog
    finally:
        await catalog.clear_catalog()
        await catalog.close()


def _record(*, doc_id=None, sha256="a" * 64, status="processing", visitor_id=None, object_key=None):
    """Build a complete test document record."""
    return {"doc_id": str(doc_id or uuid4()), "original_name": "evidence.txt", "sanitized_name": "evidence.txt", "object_key": object_key or f"uploads/{sha256}.txt", "file_type": "text", "mime_type": "text/plain", "sha256": sha256, "size_bytes": 42, "status": status, "warnings": ["warning"], "visitor_id": visitor_id}


@pytest.mark.asyncio
async def test_catalog_document_dedup_status_listing_stats_chunks_feedback_cascade(catalog):
    first = await catalog.create_document(_record())
    duplicate = await catalog.create_document(_record(sha256="a" * 64))
    assert first["created"] is True
    assert duplicate["created"] is False
    assert duplicate["doc_id"] == first["doc_id"]

    await catalog.update_status(first["doc_id"], "indexing")
    await catalog.register_chunks([{"chunk_id": f"{first['doc_id']}-chunk-0", "doc_id": first["doc_id"], "chunk_index": 0, "page_number": 1, "content_modality": "text"}, {"chunk_id": f"{first['doc_id']}-chunk-1", "doc_id": first["doc_id"], "chunk_index": 1, "page_number": 1, "content_modality": "image", "media_key": "uploads/derived/page-1.png", "mime_type": "image/png"}])
    await catalog.update_status(first["doc_id"], "ready", chunks_count=2, warnings=["done"])
    with pytest.raises(ValueError, match="transição"):
        await catalog.update_status(first["doc_id"], "uploaded")

    assert await catalog.get_vector_ids(first["doc_id"]) == [f"{first['doc_id']}-chunk-0", f"{first['doc_id']}-chunk-1"]
    assert (await catalog.list_documents())[0]["doc_id"] == first["doc_id"]
    statistics = await catalog.get_statistics()
    assert {key: statistics[key] for key in ("files", "chunks", "by_type")} == {
        "files": 1,
        "chunks": 2,
        "by_type": {"text": 1},
    }
    visitor_id = str(uuid4())
    conversation_id = await catalog.create_conversation(visitor_id)
    response_id = await catalog.record_message(visitor_id, conversation_id, "question", "answer", [], [], False)
    assert len(await catalog.record_feedback(visitor_id, response_id, True)) == 36
    usage = await catalog.increment_usage("client-hash", date.today(), uploads=1, queries=2, bytes_uploaded=42)
    usage = await catalog.increment_usage("client-hash", date.today(), uploads=2, queries=1, bytes_uploaded=8)
    assert usage["uploads_count"] == 3
    assert usage["queries_count"] == 3
    assert usage["bytes_uploaded"] == 50
    assert len(await catalog.record_ingestion_event("ready", first["doc_id"], {"chunks": 2})) == 36

    await catalog.delete_file(first["doc_id"])
    assert await catalog.get_file(first["doc_id"]) is None
    assert await catalog.get_vector_ids(first["doc_id"]) == []


@pytest.mark.asyncio
async def test_catalog_connection_failure_is_bounded_and_safe():
    catalog = Catalog("postgresql+asyncpg://test:test@127.0.0.1:1/test")
    try:
        with pytest.raises(Exception):
            await catalog.is_ready(0.1)
    finally:
        await catalog.close()


@pytest.mark.asyncio
async def test_catalog_isolates_documents_chunks_conversations_and_feedback(catalog):
    """A document ID, chunk, response and feedback never cross visitor scopes."""
    first_visitor, second_visitor = str(uuid4()), str(uuid4())
    first_id, second_id = str(uuid4()), str(uuid4())
    await catalog.create_document(_record(doc_id=first_id, visitor_id=first_visitor, object_key="uploads/first.txt"))
    await catalog.create_document(_record(doc_id=second_id, visitor_id=second_visitor, object_key="uploads/second.txt"))
    assert (await catalog.find_by_sha256("a" * 64, first_visitor))["doc_id"] == first_id
    assert (await catalog.find_by_sha256("a" * 64, second_visitor))["doc_id"] == second_id
    assert [row["doc_id"] for row in await catalog.list_files(first_visitor)] == [first_id]
    assert await catalog.get_file(second_id, first_visitor) is None

    chunk_id = f"{first_id}-chunk"
    await catalog.register_chunks([{"chunk_id": chunk_id, "doc_id": first_id, "chunk_index": 0, "page_number": 1, "content_modality": "text"}])
    await catalog.update_status(first_id, "indexing")
    await catalog.update_status(first_id, "ready", chunks_count=1)
    refs = [{"chunk_id": chunk_id, "doc_id": first_id, "object_key": ""}]
    assert await catalog.valid_chunk_refs(refs, first_visitor) == {chunk_id}
    assert await catalog.valid_chunk_refs(refs, second_visitor) == set()
    first_stats = await catalog.stats(first_visitor)
    second_stats = await catalog.stats(second_visitor)
    assert first_stats["documents_by_status"] == {"ready": 1}
    assert second_stats["documents_by_status"] == {"processing": 1}

    conversation_id = await catalog.create_conversation(first_visitor)
    message_id = await catalog.record_message(first_visitor, conversation_id, "question", "answer", [chunk_id], [], False)
    assert (await catalog.get_conversation(first_visitor, conversation_id))[0]["message_id"] == message_id
    assert await catalog.get_conversation(second_visitor, conversation_id) is None
    assert await catalog.get_message(second_visitor, message_id) is None
    with pytest.raises(FileNotFoundInCatalogError):
        await catalog.create_conversation(second_visitor, conversation_id)
    feedback_id = await catalog.record_feedback(first_visitor, message_id, True)
    assert await catalog.record_feedback(first_visitor, message_id, False) == feedback_id
    with pytest.raises(FileNotFoundInCatalogError):
        await catalog.record_feedback(second_visitor, message_id, True)
    with pytest.raises(FileNotFoundInCatalogError):
        await catalog.record_feedback(first_visitor, str(uuid4()), True)
    persisted = await catalog.persist_generated_response(first_visitor, "persisted question", "persisted answer", [], [], True)
    assert persisted["response_id"] == persisted["message_id"]
    assert (await catalog.get_message(first_visitor, persisted["response_id"]))["answer"] == "persisted answer"
    assert len(await catalog.record_feedback(first_visitor, persisted["response_id"], False)) == 36
