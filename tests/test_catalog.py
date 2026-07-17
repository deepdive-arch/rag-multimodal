import pytest

from db.catalog import Catalog


@pytest.mark.asyncio
async def test_catalog_status_chunks_and_cascade(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    await catalog.initialize()
    await catalog.create_processing_file({"doc_id": "doc", "original_name": "a.txt", "stored_name": "x_a.txt", "storage_key": "uploads/x_a.txt", "file_type": "text", "mime_type": "text/plain", "size_bytes": 1})
    await catalog.add_chunks([{"chunk_id": "chunk", "doc_id": "doc", "vector_id": "vector", "chunk_index": 0, "page_number": 0, "content_modality": "text", "media_key": ""}])
    await catalog.update_file_status("doc", "ready", chunks_count=1)
    assert await catalog.get_vector_ids("doc") == ["vector"]
    await catalog.delete_file("doc")
    assert await catalog.get_file("doc") is None
